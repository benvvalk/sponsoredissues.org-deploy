import time
import random
import requests
import traceback
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from itertools import islice
from sponsoredissues.models import GitHubIssue
from sponsoredissues.github_api import github_graphql
from sponsoredissues.github_auth import GitHubAppAuth
from urllib.parse import urlparse

# Rate limiting configuration
REQUEST_DELAY_MIN = 2.0  # Minimum delay between requests (seconds)
REQUEST_DELAY_MAX = 10.0 # Maximum delay between requests (seconds)
RETRY_DELAY = 60.0       # Delay before retrying failed requests (seconds)

class Command(BaseCommand):
    help = 'Sync GitHub issues with "sponsoredissues.org" label using GraphQL API'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.github_app_auth = GitHubAppAuth()

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
                        self.stdout.write(f'Waiting {loop_delay}s before next cycle...')
                        time.sleep(loop_delay)
                        continue

                # Exit if not in loop mode
                if not loop_mode:
                    break

                # Wait before next cycle
                if loop_delay > 0:
                    self.stdout.write(f'\nWaiting {loop_delay}s before next cycle...')
                    time.sleep(loop_delay)

        except KeyboardInterrupt:
            self.stdout.write(f'\n\nSync interrupted by user after {cycle} cycle(s)')
            self.stdout.write(self.style.WARNING('Exiting gracefully...'))

    def _sync_installations(self, options):
        # Get GitHub App installations
        target_installation_id = options.get('installation_id')
        try:
            installations = self.github_app_auth.get_app_installations(target_installation_id)
        except Exception as e:
            raise RuntimeError(f'Failed to get GitHub App installations: {e}') from e

        if not installations:
            raise RuntimeError(f'No GitHub App installations found')

        self.stdout.write(f'Found {len(installations)} GitHub App installations to sync')

        total_added = 0
        total_updated = 0
        total_removed = 0

        for installation in installations:
            account_login = installation['account']['login']
            installation_id = installation['id']

            self.stdout.write(f'\n--- Syncing installation: {account_login} (ID: {installation_id}) ---')

            try:
                dry_run = options['dry_run']
                added, updated, removed = self._sync_installation_issues(installation, dry_run)
                total_added += added
                total_updated += updated
                total_removed += removed

                self.stdout.write(
                    f'Installation {account_login}: +{added} ~{updated} -{removed} issues'
                )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error syncing installation {account_login}: {e}\n{traceback.format_exc()}')
                )
                continue

            # Rate limiting between installations
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            time.sleep(delay)

        # Final summary
        self.stdout.write(f'\n=== SYNC SUMMARY ===')
        self.stdout.write(f'Total added: {total_added}')
        self.stdout.write(f'Total updated: {total_updated}')
        self.stdout.write(f'Total removed: {total_removed}')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No actual changes made'))
        else:
            self.stdout.write(self.style.SUCCESS('Sync completed'))

    def _sync_installation_issues(self, installation, dry_run):
        """Sync issues for a single GitHub App installation"""
        installation_id = installation['id']
        account_login = installation['account']['login']

        # Get installation access token
        try:
            access_token = self.github_app_auth.get_installation_access_token(installation_id)
        except Exception as e:
            self.stdout.write(f'Failed to get GitHub App access token for installation {installation_id}: {e}')
            return 0, 0, 0

        # Query repositories and issues using GraphQL
        issues_data = self._query_installation_issues(account_login, access_token)

        self.stdout.write(f'Found {len(issues_data)} issues with sponsoredissues.org label or funding')

        # Get current issues URLs for this installation's account
        current_issue_urls = set(
            GitHubIssue.objects.filter(
                url__contains=f'github.com/{account_login}/'
            ).values_list('url', flat=True)
        )

        # Process found issues
        added = updated = 0
        found_issue_urls = set()

        for issue_data in issues_data:
            issue_url = issue_data['url']
            found_issue_urls.add(issue_url)

            if issue_url in current_issue_urls:
                # Update existing issue
                if not dry_run:
                    GitHubIssue.objects.filter(url=issue_url).update(data=issue_data)
                updated += 1
                self.stdout.write(f'Updated: {issue_url}')
            else:
                # Add new issue
                if not dry_run:
                    GitHubIssue.objects.update_or_create(
                        url=issue_url,
                        defaults={'data': issue_data}
                    )
                added += 1
                self.stdout.write(f'Added: {issue_url}')

        # Remove issues that no longer have the label
        issues_to_remove = current_issue_urls - found_issue_urls
        removed = 0

        for issue_url in issues_to_remove:
            issue = GitHubIssue.objects.filter(url=issue_url).first()

            if not issue:
                continue

            # We never delete funded issues, for several
            # reasons:
            #
            # (1) It allows us to compute interesting historical
            # stats, such as average funding amount for resolved
            # issues, average time to resolve issues, etc.
            #
            # (2) If the maintainer reopens a closed issue, we need to
            # restore the funding amount to the same value as when it
            # was closed.
            #
            # (3) If the maintainer accidentally removes the
            # `sponsoredissues.org` label from an issue with non-zero
            # funding, we don't want to automatically return all the
            # assigned funds to users. Instead, we continue to show
            # the issue on the maintainer's sponsored issues page, but
            # in a special frozen/error state with the "Add or Remove
            # Funds" button disabled and an explanatory error message.
            if issue.is_funded():
                continue

            if not dry_run:
                deleted_count, _ = issue.delete()
                if deleted_count > 0:
                    removed += 1
                    self.stdout.write(f'Removed: {issue_url}')
            else:
                removed += 1
                self.stdout.write(f'Removed: {issue_url}')

        return added, updated, removed

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
        cursor = None

        while repos_processed < repo_limit:
            variables['cursor'] = cursor

            self.stdout.write(f'Querying repositories (processed: {repos_processed}/{repo_limit})...')

            try:
                data = github_graphql(query, access_token, variables=variables, timeout=30)
            except requests.RequestException as e:
                self.stdout.write(self.style.ERROR(f'GraphQL request failed: {e}'))
                time.sleep(RETRY_DELAY)
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

            # Check if there are more repositories to fetch
            page_info = repositories.get('pageInfo', {})
            if page_info.get('hasNextPage') and repos_processed < repo_limit:
                cursor = page_info.get('endCursor')
            else:
                break

            # Rate limiting between requests
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            time.sleep(delay)

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
                time.sleep(RETRY_DELAY)
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
            # Rate limiting between requests
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            time.sleep(delay)

        return issues
