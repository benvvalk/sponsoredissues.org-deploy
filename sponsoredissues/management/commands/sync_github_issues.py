import requests
import traceback
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from itertools import islice
from sponsoredissues.models import GitHubAppInstallation, GitHubIssue, GitHubRepo
from sponsoredissues.github_api import github_api, github_app_installation_is_suspended, github_issue_has_sponsoredissues_label, github_graphql, random_sleep_for_rate_limiting
from sponsoredissues.github_app import GitHubApp, GitHubAppInstallationClass
from urllib.parse import urlparse

class SyncStats:
    """
    Stats about what changed when syncing installations/repos/issues.
    """
    added = 0
    updated = 0
    removed = 0

class Command(BaseCommand):
    help = 'Sync GitHub issues with "sponsoredissues.org" label using GraphQL API'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.github_app = GitHubApp()

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes',
        )
        parser.add_argument(
            '--installation-id',
            type=int,
            help='Sync issues for specific GitHub App installation ID only',
        )
        parser.add_argument(
            '--loop',
            action='store_true',
            help='Run sync in an infinite loop',
        )
        parser.add_argument(
            '--loop-delay',
            type=int,
            default=0,
            help='Delay in seconds between sync iterations when using --loop (default: 0)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        loop_mode = options['loop']
        loop_delay = options['loop_delay']

        self.stdout.write(f'Starting GitHub issues sync (dry_run={dry_run}, loop={loop_mode})')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        if loop_mode:
            self.stdout.write(f'Loop mode enabled with {loop_delay}s delay between iterations')

        cycle = 0

        try:
            while True:
                cycle += 1

                if loop_mode:
                    from datetime import datetime
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    self.stdout.write(f'\n{"="*60}')
                    self.stdout.write(f'Sync cycle {cycle} starting at {timestamp}')
                    self.stdout.write(f'{"="*60}')

                try:
                    self._sync_installations(options)
                except Exception as e:
                    self.stdout.write(f'Error: {e}\n{traceback.format_exc()}')
                    if not loop_mode:
                        return
                    else:
                        random_sleep_for_rate_limiting()
                        continue

                # Exit if not in loop mode
                if not loop_mode:
                    break

                # Wait before next cycle
                if loop_delay > 0:
                    random_sleep_for_rate_limiting()

        except KeyboardInterrupt:
            self.stdout.write(f'\n\nSync interrupted by user after {cycle} cycle(s)')
            self.stdout.write(self.style.WARNING('Exiting gracefully...'))

    def _sync_installations(self, options):
        # Get GitHub App installations
        target_installation_id = options.get('installation_id')
        dry_run = options.get('dry_run', False)

        try:
            installations = self.github_app.query_installations(target_installation_id)
        except Exception as e:
            raise RuntimeError(f'Failed to get GitHub App installations: {e}') from e

        if not installations:
            raise RuntimeError(f'No GitHub App installations found')

        self.stdout.write(f'Found {len(installations)} GitHub App installations to sync')

        installation_stats = SyncStats()
        repo_stats = SyncStats()
        issue_stats = SyncStats()

        # Compare the app installation URLs in our database to the
        # installation URLs we retrieved from the GitHub API, to
        # identify which installations have been newly
        # installed/uninstalled.
        current_installation_urls = set(
            GitHubAppInstallation.objects.values_list('url', flat=True)
        )
        found_installation_urls = set()

        for installation in installations:
            installation_json = installation.installation_json
            assert installation_json
            installation_url = installation_json['html_url']
            installation_id = installation_json['id']
            account_login = installation_json['account']['login']

            found_installation_urls.add(installation_url)

            try:
                _installation_stats, _repo_stats, _issue_stats = self._sync_installation(installation_json, dry_run)

                installation_stats.added += _installation_stats.added
                installation_stats.updated += _installation_stats.updated
                installation_stats.removed += _installation_stats.removed

                repo_stats.added += _repo_stats.added
                repo_stats.updated += _repo_stats.updated
                repo_stats.removed += _repo_stats.removed

                issue_stats.added += _issue_stats.added
                issue_stats.updated += _issue_stats.updated
                issue_stats.removed += _issue_stats.removed

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error syncing installation {account_login}: {e}\n{traceback.format_exc()}')
                )

            random_sleep_for_rate_limiting()

        # Delete any app installations in our database that weren't
        # present in the latest list of app installations from the
        # GitHub API. (These app installations must have been
        # uninstalled by the maintainer.)
        installation_urls_to_remove = current_installation_urls - found_installation_urls

        if not dry_run:
            _, deleted_by_model = GitHubAppInstallation.objects.filter(url__in=installation_urls_to_remove).delete()
            installations_removed = deleted_by_model.get('GitHubAppInstallation', 0)
            repos_removed = deleted_by_model.get('GitHubRepo', 0)
            issues_removed = deleted_by_model.get('GitHubIssue', 0)
            self.stdout.write(f'Removed {installations_removed} installations, because they were uninstalled or suspended (- {repos_removed} repos, - {issues_removed} issues)')
        else:
            installations_removed = len(installation_urls_to_remove)
            repos_removed = 0
            issues_removed = 0
            self.stdout.write(f'Removed {installations_removed} installations, because they were uninstalled or suspended')

        installation_stats.removed += installations_removed
        repo_stats.removed += repos_removed
        issue_stats.removed += issues_removed

        # Final summary
        self.stdout.write(f'\n=== SYNC SUMMARY ===')
        self.stdout.write(f'Total installations added: {installation_stats.added}')
        self.stdout.write(f'Total installations updated: {installation_stats.updated}')
        self.stdout.write(f'Total installations removed: {installation_stats.removed}')
        self.stdout.write(f'---\n')
        self.stdout.write(f'Total repos added: {repo_stats.added}')
        self.stdout.write(f'Total repos updated: {repo_stats.updated}')
        self.stdout.write(f'Total repos removed: {repo_stats.removed}')
        self.stdout.write(f'---\n')
        self.stdout.write(f'Total issues added: {issue_stats.added}')
        self.stdout.write(f'Total issues updated: {issue_stats.updated}')
        self.stdout.write(f'Total issues removed: {issue_stats.removed}')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No actual changes made'))
        else:
            self.stdout.write(self.style.SUCCESS('Sync completed'))

    def _sync_installation(self, installation_json, dry_run):
        account_login = installation_json['account']['login']
        installation_id = installation_json['id']
        installation_url = installation_json['html_url']

        self.stdout.write(f'\n--- Syncing installation: {account_login} (ID: {installation_id}) ---')

        installation_stats = SyncStats()
        repo_stats = SyncStats()
        issue_stats = SyncStats()

        installation = GitHubAppInstallation.objects.get(url=installation_url)

        # check if maintainer has suspended the app installation
        if github_app_installation_is_suspended(installation_json):
            if installation:
                if not dry_run:
                    _, deleted_by_model = GitHubAppInstallation.objects.get(url=installation_url).delete()
                    repo_stats.removed += deleted_by_model.get('GitHubRepo', 0)
                    issue_stats.removed += deleted_by_model.get('GitHubIssue', 0)
                installation_stats.removed += 1
                self.stdout.write(f'Removed: installation {account_login}, because it is suspended')
            else:
                self.stdout.write(f'Skipped: installation {account_login}, because it is suspended')
            return installation_stats, repo_stats, issue_stats

        repo_stats = self._sync_installation_repos(installation_json, dry_run)
        issue_stats = self._sync_installation_issues(installation_json, dry_run)

        self.stdout.write(
            f'Installation {account_login}: +{repo_stats.added} ~{repo_stats.updated} -{repo_stats.removed} repos'
        )
        self.stdout.write(
            f'Installation {account_login}: +{issue_stats.added} ~{issue_stats.updated} -{issue_stats.removed} issues'
        )

        # create/update record for app installation in database
        if installation:
            if not dry_run:
                # call `save()` to set new value for `GitHubAppInstallation.updated_at`
                installation.save()
            installation_stats.updated += 1
            self.stdout.write(f'Updated: installation {account_login}')
        else:
            if not dry_run:
                GitHubAppInstallation.objects.update_or_create(url=installation_url)
            installation_stats.added += 1
            self.stdout.write(f'Added: installation {account_login}')

        return installation_stats, repo_stats, issue_stats

    def _sync_installation_repos(self, installation_json, dry_run):
        """Sync repos for a single GitHub App installation"""
        installation_id = installation_json['id']
        installation_url = installation_json['html_url']
        account_login = installation_json['account']['login']

        installation = GitHubAppInstallation.objects.get(url=installation_url)
        assert installation

        # Query repositories and issues using GraphQL
        _installation = GitHubAppInstallationClass.from_json(installation_json)
        repos = _installation.query_repos()

        self.stdout.write(f'`Installation {account_login}: found {len(repos)} repos')

        # Get current repo URLs for this installation's account
        owner_url = f'https://github.com/{account_login}'
        current_repo_urls = set(
            GitHubRepo.objects.filter(
                url__startswith=f'{owner_url}/'
            ).values_list('url', flat=True)
        )

        # Process found repos
        repo_stats = SyncStats()
        found_repo_urls = set()

        for repo in repos:
            repo_url = repo['html_url']
            found_repo_urls.add(repo_url)

            if repo['private']:
                self.stdout.write(f'Skipped: {repo_url} (private repo)')
                continue

            if repo_url in current_repo_urls:
                # Update existing repo
                if not dry_run:
                    GitHubRepo.objects.filter(url=repo_url).update(updated_at=timezone.now())
                repo_stats.updated += 1
                self.stdout.write(f'Updated: {repo_url}')
            else:
                # Add new repo
                if not dry_run:
                    GitHubRepo.objects.update_or_create(url=repo_url, app_installation=installation)
                repo_stats.added += 1
                self.stdout.write(f'Added: {repo_url}')

        # Remove repos that the `sponsoredissues-maintainer` GitHub App
        # can no longer access
        repos_to_remove = current_repo_urls - found_repo_urls

        for repo_url in repos_to_remove:
            if not dry_run:
                deleted_count, _ = GitHubRepo.objects.filter(url=repo_url).delete()
                if deleted_count > 0:
                    repo_stats.removed += 1
                    self.stdout.write(f'Removed: {repo_url}')
            else:
                repo_stats.removed += 1
                self.stdout.write(f'Removed: {repo_url}')

        return repo_stats

    def _sync_installation_issues(self, installation, dry_run):
        """Sync issues for a single GitHub App installation"""
        installation_id = installation['id']
        account_login = installation['account']['login']

        # Get installation access token
        _installation = GitHubAppInstallationClass.from_json(installation)
        access_token = _installation.get_access_token()

        # Query repositories and issues using GraphQL
        issues_data = self._query_installation_issues(account_login, access_token)

        self.stdout.write(f'Found {len(issues_data)} issues with sponsoredissues.org label or funding')

        # Get current repo URLs for this installation's account
        current_repo_urls = set(
            GitHubRepo.objects.filter(
                url__startswith=f'https://github.com/{account_login}/'
            ).values_list('url', flat=True)
        )

        # Get current issues URLs for this installation's account
        current_issues = dict(
            GitHubIssue.objects.filter(
                url__contains=f'github.com/{account_login}/'
            ).values_list('url', 'data')
        )

        # The set of issues that currently have non-zero user funding,
        # (a subset of `current_issues` above).
        #
        # We should never delete funded issues, for several reasons:
        #
        # (1) It allows us to compute interesting historical stats for
        # closed issues, such as average funding amount for close
        # issues, average time to close issues, etc.
        #
        # (2) We want the maintainer to be able to reopen closed
        # issues, in which case we need to restore the funding totals
        # of the issues to the same values as when they were closed.
        #
        # (3) If the maintainer accidentally removes the
        # `sponsoredissues.org` label from an issue with non-zero
        # funding, we display the issue in a special "frozen" state,
        # with the "Add or Remove Funds" button disabled and an
        # explanatory error message. This is much better than
        # immediately just the issue because it doesn't undo users'
        # funding allocations.
        funded_issue_urls = set(
            GitHubIssue.objects.filter(
                url__contains=f'github.com/{account_login}/',
                sponsor_amounts__isnull=False,
            ).distinct().values_list('url', flat=True)
        )

        # Issues that we should not delete from our database, because
        # all of the following are true:
        #
        # (1) The issue still exists on GitHub, *AND*
        # (2) The `sponsoredissues-maintainer` GitHub App
        # is still installed and active on the repo, *AND*
        # (3) The issue still has the `sponsoredissues.org`
        # label on GitHub.
        found_issue_urls = set()

        # Stats about new/updated issues (returned from this method).
        issue_stats = SyncStats()

        # Unfunded issues that we should delete from our database,
        # because the `sponsoredissues-maintainer` GitHub App has been
        # uninstalled/suspended on the repo.
        repo_disabled_issue_urls = set()

        # Unfunded issues that we should delete from our database,
        # because the `sponsoredissues.org` GitHub App has been
        # uninstalled/suspended on the repo.
        label_removed_issue_urls = set()

        for issue_data in issues_data:
            issue_url = issue_data['url']
            repo_url = '/'.join(issue_url.split('/')[:-2])

            # Unfunded issues will be deleted if either:
            #
            # (1) The `sponsoredissues-maintainer` GitHub App is no
            # longer installed/active on the repo that contains the
            # issue.
            # (2) The `sponsoredissues.org` label was removed
            # from the issue.
            #
            # Note our detection of (1) and (2) is mutually exclusive;
            # We will not be able to retrieve the current labels for
            # an issue after the app is uninstalled/suspended.

            if not issue_url in funded_issue_urls:
                if not repo_url in current_repo_urls:
                    repo_disabled_issue_urls.add(issue_url)
                    continue
                elif not github_issue_has_sponsoredissues_label(issue_data):
                    label_removed_issue_urls.add(issue_url)
                    continue

            found_issue_urls.add(issue_url)
            repo = GitHubRepo.objects.get(url=repo_url)

            if issue_url in current_issues:
                # Update existing issue
                if not dry_run:
                    GitHubIssue.objects.filter(url=issue_url).update(data=issue_data, repo=repo, updated_at=timezone.now())
                issue_stats.updated += 1
                self.stdout.write(f'Updated: {issue_url}')
            else:
                # Add new issue
                if not dry_run:
                    GitHubIssue.objects.update_or_create(
                        url=issue_url,
                        defaults={
                            'data': issue_data,
                            'repo': repo,
                        }
                    )
                issue_stats.added += 1
                self.stdout.write(f'Added: {issue_url}')

        # Remove issues that no longer have the label
        current_issue_urls = current_issues.keys()
        issues_to_remove = current_issue_urls - found_issue_urls

        for issue_url in issues_to_remove:
            issue = GitHubIssue.objects.filter(url=issue_url)

            removed_old = issue_stats.removed
            if not dry_run:
                deleted_count, _ = issue.delete()
                if deleted_count > 0:
                    issue_stats.removed += 1
            else:
                issue_stats.removed += 1

            if issue_stats.removed > removed_old:
                if issue_url in repo_disabled_issue_urls:
                    self.stdout.write(f'Removed: {issue_url} (GitHub App was disabled on repo)')
                elif issue_url in label_removed_issue_urls:
                    self.stdout.write(f'Removed: {issue_url} (`sponsoredissues.org` label was removed)')
                else:
                    self.stdout.write(f'Removed: {issue_url}')

        return issue_stats

    def _query_installation_issues(self, username, access_token):
        """
        Retrieve the latest JSON issue data from the GitHub GraphQL
        API, for all issues that are relevant to sponsoredissues.org.

        An issue is relevant to sponsoredissues.org if either:

        (1) It belongs to a repo with the "sponsoredissues-maintainer" GitHub
        App installed *AND* it has the `sponsoredissues.org` label.
        (2) It has a non-zero amount of funding on sponsoredissues.org.

        Note that it is possible for any combination of (1) and (2) to
        be true. For example, the maintainer might accidentally remove
        the `sponsoredissues.org` label from an issue that already has
        funding on their sponsored issues page. In that case, the
        issue is shown in a special "frozen" state, with the "Add or
        Remove Funds" button disabled.
        """
        issues_with_label = self._query_installation_issues_with_label(username, access_token)
        issues_with_funding = self._query_installation_issues_with_funding(username, access_token)

        # Merge lists.
        issues_by_url = {issue['url']: issue for issue in issues_with_label}
        issues_by_url.update({issue['url']: issue for issue in issues_with_funding})

        return list(issues_by_url.values())

    def _query_installation_issues_with_label(self, username, access_token):
        """Query user's public repositories and issues with sponsoredissues.org label"""
        query = """
        query($username: String!, $issueFirst: Int!, $cursor: String) {
            user(login: $username) {
                repositories(
                    first: 30
                    after: $cursor
                    privacy: PUBLIC
                    orderBy: {field: UPDATED_AT, direction: DESC}
                ) {
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                    nodes {
                        name
                        owner {
                            login
                        }
                        issues(
                            first: $issueFirst
                            states: [OPEN, CLOSED]
                            labels: ["sponsoredissues.org"]
                        ) {
                            nodes {
                                number
                                title
                                body
                                state
                                url
                                createdAt
                                updatedAt
                                labels(first: 20) {
                                    nodes {
                                        name
                                        color
                                    }
                                }
                                author {
                                    login
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        variables = {
            'username': username,
            'issueFirst': 100,  # Get up to 100 issues per repo
            'cursor': None
        }

        issues = []
        repos_processed = 0
        page_info = {'hasNextPage': True, 'endCursor': None}

        while page_info.get('hasNextPage'):
            variables['cursor'] = page_info.get('endCursor')

            self.stdout.write(f'Querying repos (processed {repos_processed} repos so far)...')

            try:
                data = github_graphql(query, access_token, variables=variables, timeout=30)
            except requests.RequestException as e:
                self.stdout.write(self.style.ERROR(f'GraphQL request failed: {e}'))
                random_sleep_for_rate_limiting()
                continue

            user_data = data.get('user')
            if not user_data:
                break

            repositories = user_data.get('repositories', {})
            repos = repositories.get('nodes', [])

            # Process issues from each repository
            for repo in repos:
                repo_name = repo['name']
                owner_login = repo['owner']['login']
                repo_issues = repo.get('issues', {}).get('nodes', [])

                if repo_issues:
                    self.stdout.write(f'  {owner_login}/{repo_name}: {len(repo_issues)} issues')

                for issue in repo_issues:
                    # Convert GraphQL response to REST API format for compatibility
                    issue_data = {
                        'number': issue['number'],
                        'title': issue['title'],
                        'body': issue['body'],
                        'state': issue['state'].lower(),
                        'url': issue['url'],
                        'created_at': issue['createdAt'],
                        'updated_at': issue['updatedAt'],
                        'labels': [
                            {
                                'name': label['name'],
                                'color': label['color']
                            }
                            for label in issue.get('labels', {}).get('nodes', [])
                        ],
                        'user': {
                            'login': issue.get('author', {}).get('login', '')
                        }
                    }
                    issues.append(issue_data)

            repos_processed += len(repos)

            # Update info about next page of query results (if any)
            page_info = repositories.get('pageInfo')

            random_sleep_for_rate_limiting()

        return issues

    def _build_issues_query(self, issue_urls):
        """
        Build a GitHub GraphQL query that gets the latest data for
        given issue URLs.
        """
        # Build a dictionary that groups issues by repo.
        repos = dict()
        for issue_url in issue_urls:
            url_path = urlparse(issue_url).path.strip('/')
            repo_url = '/'.join(url_path.split('/')[:-2])
            if repo_url not in repos:
                repos[repo_url] = []
            repos[repo_url].append(issue_url)

        # Monotonically-increasing indices for GraphQL aliases.
        repo_index = 0
        issue_index = 0

        query = """query {"""
        for (repo_url, issue_urls) in repos.items():
            path = urlparse(repo_url).path.strip('/')
            owner = path.split('/')[-2]
            repo_name = path.split('/')[-1]

            query += f"""
            repo{repo_index}: repository(owner: "{owner}", name: "{repo_name}") {{"""
            repo_index += 1

            for issue_url in issue_urls:
                path = urlparse(issue_url).path.strip('/')
                issue_number = path.split('/')[-1]

                query += f"""
                issue{issue_index}: issue(number: {issue_number}) {{"""
                query += """
                    number
                    title
                    body
                    state
                    url
                    createdAt
                    updatedAt
                    labels(first: 30) {
                        nodes {
                            name
                            color
                        }
                    }
                    author {
                        login
                    }
                }
                """
                issue_index += 1
            query += """
            }
            """
        query += """
        }
        """
        return query

    def _query_installation_issues_with_funding(self, username, access_token):
        """
        Get latest issue data queries for GitHub issues that have received
        non-zero user funding on sponsoredissues.org.

        These queries corresponds to case (2), in the comment at the
        top of this function.
        """

        # Get issues with non-zero funding.
        issue_urls = GitHubIssue.objects.filter(
            url__startswith=f"https://github.com/{username}/",
            sponsor_amounts__isnull=False
        ).distinct().values_list('url', flat=True)

        # Query in batches to avoid exceeding GitHub API limits.
        queries = []
        iterator = iter(issue_urls)
        while True:
            batch = list(islice(iterator, 100))
            if not batch:
                break
            query = self._build_issues_query(batch)
            queries.append(query)

        issues = []
        for query in queries:
            try:
                data = github_graphql(query, access_token, timeout=30)
            except requests.RequestException as e:
                self.stdout.write(self.style.ERROR(f'GraphQL request failed: {e}'))
                random_sleep_for_rate_limiting()
                continue

            for i in range(len(data)):
                repo = data.get(f'repo{i}')
                for j in range(len(repo)):
                    issue = repo.get(f'issue{j}')
                    # Convert GraphQL response to REST API format for compatibility
                    issue_data = {
                        'number': issue['number'],
                        'title': issue['title'],
                        'body': issue['body'],
                        'state': issue['state'].lower(),
                        'url': issue['url'],
                        'created_at': issue['createdAt'],
                        'updated_at': issue['updatedAt'],
                        'labels': [
                            {
                                'name': label['name'],
                                'color': label['color']
                            }
                            for label in issue.get('labels', {}).get('nodes', [])
                        ],
                        'user': {
                            'login': issue.get('author', {}).get('login', '')
                        }
                    }
                    issues.append(issue_data)
            random_sleep_for_rate_limiting()

        return issues
