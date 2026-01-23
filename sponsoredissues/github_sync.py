import logging

from django.utils import timezone
from sponsoredissues.github_api import github_app_installation_is_suspended, github_issue_has_sponsoredissues_label
from sponsoredissues.github_app import GitHubAppInstallationClass
from sponsoredissues.logging import PrefixLoggerAdapter
from sponsoredissues.models import GitHubAppInstallation, GitHubIssue, GitHubRepo

default_logger = logging.getLogger(__name__)

def github_sync_app_installation(installation_id, base_logger=default_logger):
    installation_api = GitHubAppInstallationClass.from_id(installation_id)

    logger = PrefixLoggerAdapter(base_logger, {'prefix': f'Installation {installation_id}: '})
    logger.info(f'starting sync')

    installation_json = installation_api.query_json()
    assert installation_json

    account_login = installation_json['account']['login']
    installation_url = installation_json['html_url']

    logger.info(f'GitHub account is "{account_login}"')

    installation = GitHubAppInstallation.objects.filter(url=installation_url).first()

    # check if maintainer has suspended the app installation
    if github_app_installation_is_suspended(installation_json):
        if installation:
            _, deleted_by_object = installation.delete()
            repos_removed = deleted_by_object.get('GitHubRepo', 0)
            issues_removed = deleted_by_object.get('GitHubIssue', 0)
            logger.info(f'removed installation, because it is suspended (removed: {repos_removed} repos, {issues_removed} issues)')
        else:
            logger.info(f'skipped installation, because it is suspended')
        return

    installation, created = GitHubAppInstallation.objects.get_or_create(url=installation_url)
    if created:
        logger.info(f'added (empty) installation to DB')

    github_sync_repos_for_app_installation(installation_api, logger)
    github_sync_issues_for_app_installation(installation_api, logger)

    installation.updated_at = timezone.now()
    installation.save()
    logger.info(f'successfully synced installation')

def github_sync_repos_for_app_installation(installation_api, logger=default_logger):
    """Sync repos for a single GitHub App installation"""
    installation_json = installation_api.query_json()
    installation_url = installation_json['html_url']

    installation = GitHubAppInstallation.objects.get(url=installation_url)
    assert installation

    # query currently enabled repositories for app installation
    logger.info(f'querying GitHub for enabled repos')
    repos_from_github = installation_api.query_repos()
    logger.info(f'found {len(repos_from_github)} enabled repos')

    # Get current repo URLs for this installation's account
    repo_urls_in_db = set(
        GitHubRepo.objects.filter(
            app_installation=installation
        ).values_list('url', flat=True)
    )

    repo_urls_from_github = {repo['html_url'] for repo in repos_from_github if not repo['private']}
    repo_urls_to_add = repo_urls_from_github - repo_urls_in_db
    repo_urls_to_update = repo_urls_from_github & repo_urls_in_db
    repo_urls_to_remove = repo_urls_in_db - repo_urls_from_github

    for repo_url in repo_urls_to_add:
        GitHubRepo.objects.create(url=repo_url, app_installation=installation)
        logger.info(f'added repo {repo_url}')

    for repo_url in repo_urls_to_update:
        GitHubRepo.objects.filter(url=repo_url).update(updated_at=timezone.now())    
        logger.info(f'updated repo {repo_url}')

    for repo_url in repo_urls_to_remove:
        GitHubRepo.objects.get(url=repo_url).delete()
        logger.info(f'removed repo {repo_url}')
    
    logger.info(f'repo sync stats: +{len(repo_urls_to_add)} ~{len(repo_urls_to_update)} -{len(repo_urls_to_remove)}')

def github_sync_issues_for_app_installation(installation_api, logger=default_logger):
    """Sync issues for a single GitHub App installation"""
    installation_json = installation_api.query_json()
    account_login = installation_json['account']['login']

    # query relevant issues using GitHub API
    logger.info(f'querying GitHub for issues with "sponsoredissues.org" label or existing funding')
    issues_on_github = installation_api.query_issues_with_sponsoredissues_label_or_funding()
    logger.info(f'found {len(issues_on_github)} issues')

    # Get current repo URLs for this installation's account
    repo_urls_in_db = set(
        GitHubRepo.objects.filter(
            url__startswith=f'https://github.com/{account_login}/'
        ).values_list('url', flat=True)
    )

    # Get current issues URLs for this installation's account
    issues_in_db = dict(
        GitHubIssue.objects.filter(
            url__contains=f'github.com/{account_login}/'
        ).values_list('url', 'data')
    )

    # The set of issues that currently have non-zero user funding,
    # (a subset of `issues_in_db` above).
    #
    # We never delete funded issues, for several reasons:
    #
    # (1) It undoes the work of users who contributed to the issue(s).
    #
    # (2) Deleting funded issue(s) is usually an accident by the
    # maintainer. We want the maintainer to easily undo their
    # mistake, by re-adding the `sponsoredissues.org` label and/or
    # reinstalling/unsuspending the app, in which case we need restore
    # the issue funding totals to their previous values.
    #
    # (3) Keeping previously funded issues in the database allows us
    # to compute interesting historical stats for closed issues, such
    # as average funding amount for close issues, average time to
    # close issues, etc.
    #
    # Regarding (2): If the maintainer accidentally removes the
    # `sponsoredissues.org` label from an issue with existing
    # funding, we display the issue in a special "frozen" state,
    # with the "Add or Remove Funds" button disabled and an
    # explanatory error message.
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

    # Stats about added/updated/removed issues.
    issues_added = 0
    issues_updated = 0
    issues_removed = 0

    # Unfunded issues that we should delete from our database,
    # because the `sponsoredissues-maintainer` GitHub App has been
    # uninstalled/suspended on the repo.
    repo_disabled_issue_urls = set()

    # Unfunded issues that we should delete from our database,
    # because the `sponsoredissues.org` GitHub App has been
    # uninstalled/suspended on the repo.
    label_removed_issue_urls = set()

    for issue_json in issues_on_github:
        issue_url = issue_json['url']
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
            if not repo_url in repo_urls_in_db:
                repo_disabled_issue_urls.add(issue_url)
                continue
            elif not github_issue_has_sponsoredissues_label(issue_json):
                label_removed_issue_urls.add(issue_url)
                continue

        found_issue_urls.add(issue_url)
        repo = GitHubRepo.objects.get(url=repo_url)

        if issue_url in issues_in_db:
            GitHubIssue.objects.filter(url=issue_url).update(data=issue_json, repo=repo, updated_at=timezone.now())
            issues_updated += 1
            logger.info(f'updated issue {issue_url}')
        else:
            GitHubIssue.objects.update_or_create(
                url=issue_url,
                defaults={
                    'data': issue_json,
                    'repo': repo,
                }
            )
            issues_added += 1
            logger.info(f'added issue {issue_url}')

    # Remove unfunded issues that no longer have "sponsoredissues.org"
    # label, or whose repos have been disabled for the GitHub App.

    issues_urls_in_db = issues_in_db.keys()
    issues_to_remove = issues_urls_in_db - found_issue_urls

    for issue_url in issues_to_remove:
        issue = GitHubIssue.objects.filter(url=issue_url)
        assert issue

        deleted_count, _ = issue.delete()
        assert deleted_count == 1
        issues_removed += 1

        if issue_url in repo_disabled_issue_urls:
            logger.info(f'removed issue {issue_url}, because GitHub App is disabled on repo)')
        elif issue_url in label_removed_issue_urls:
            logger.info(f'removed issue {issue_url}, because `sponsoredissues.org` label was removed')
        else:
            logger.info(f'removed issue {issue_url}')

    logger.info(f'issue sync stats: +{issues_added} ~{issues_updated} -{issues_removed}')