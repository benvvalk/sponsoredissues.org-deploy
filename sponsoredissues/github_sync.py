import logging

from django.utils import timezone
from enum import Enum
from requests.exceptions import HTTPError
from sponsoredissues.github_api import github_api, github_app_installation_is_suspended, github_issue_has_sponsoredissues_label
from sponsoredissues.github_app import github_app_installation_query_json, github_app_installation_query_issues_with_sponsoredissues_label, github_app_installation_query_issue_urls, github_app_installation_query_repos, github_app_installation_query_token
from sponsoredissues.github_sponsors import GitHubSponsorService
from sponsoredissues.logging import PrefixLoggerAdapter
from sponsoredissues.models import GitHubAppInstallation, GitHubIssue, GitHubRepo, Maintainer

default_logger = logging.getLogger(__name__)

class SyncResult(Enum):
    """
    What happened to an individual issue in the database, after the latest
    JSON data for the issue from GitHub.
    """
    ADDED = 0
    UPDATED = 1
    REMOVED = 2
    IGNORED = 3

def github_sync_maintainer(github_account_id: int, access_token=None, logger=default_logger):
    # get JSON data for GitHub user
    github_user_json = github_api(f'/user/{github_account_id}', access_token=access_token)
    github_account_name = github_user_json['login']

    # check if maintainer has created a GitHub Sponsors profile
    github_sponsors = GitHubSponsorService()
    if github_sponsors.has_sponsors_profile(github_account_name):
        github_sponsors_profile_url = f'https://github.com/sponsors/{github_account_name}'
    else:
        github_sponsors_profile_url = None

    # update or create `Maintainer` in database
    maintainer, created = Maintainer.objects.update_or_create(
        github_account_id = github_account_id,
        defaults = {
            'github_user_json': github_user_json,
            'github_sponsors_profile_url': github_sponsors_profile_url
        }
    )
    if created:
        logger.info(f'created Maintainer: "{github_account_name}"')
    else:
        logger.info(f'update Maintainer: "{github_account_name}"')

    return maintainer

def github_sync_app_installation_remove(installation, logger=default_logger):
    installation_url = installation.url
    _, deleted_by_object = installation.delete()
    repos_removed = deleted_by_object.get('GitHubRepo', 0)
    issues_removed = deleted_by_object.get('GitHubIssue', 0)
    logger.info(f'removed installation from database: {installation_url} (removed: {repos_removed} repos, {issues_removed} unfunded issues)')

def github_sync_app_installation(installation_id, base_logger=default_logger):
    installation_url = f'https://github.com/settings/installations/{installation_id}'
    installation_token = github_app_installation_query_token(installation_id)

    installation = GitHubAppInstallation.objects.filter(url=installation_url).first()

    logger = PrefixLoggerAdapter(base_logger, {'prefix': f'Installation {installation_id}: '})
    logger.info(f'starting sync')

    try:
        logger.info(f'querying JSON data')
        installation_json = github_app_installation_query_json(installation_id)
    except HTTPError as e:
        # We will get HTTP 404 if the maintainer has uninstalled the
        # "sponsoredissues-maintainer" GitHub App, in which case
        # we need to abort the sync.
        #
        # This app installation will eventually get removed from the
        # database by
        # `task_sync_github_app_installations_new_and_removed`.  We
        # shouldn't remove the app installation here because that the
        # HTTP 404 happened for some other reason (e.g. a general
        # GitHub API outage).
        if e.response.status_code == 404 and installation:
            logger.info('query for installation JSON returned HTTP 404, skipping sync')
            return
        else:
            raise

    assert installation_json

    account_login = installation_json['account']['login']
    installation_url = installation_json['html_url']

    logger.info(f'GitHub account is "{account_login}"')

    # check if maintainer has suspended the app installation
    if github_app_installation_is_suspended(installation_json):
        logger.info('installation is suspended')
        if installation:
            github_sync_app_installation_remove(installation, logger)
        return

    installation, created = GitHubAppInstallation.objects.update_or_create(
        url=installation_url,
        defaults={'data': installation_json}
    )
    if created:
        logger.info(f'added (empty) installation to DB')

    github_sync_maintainer(installation_json['account']['id'], access_token=installation_token, logger=logger)
    github_sync_app_installation_repos(installation_token, installation, logger)
    github_sync_app_installation_issues(installation_token, installation, logger)

    installation.updated_at = timezone.now()
    installation.save()
    logger.info(f'successfully synced installation')

def github_sync_app_installation_repos(installation_token, installation, logger=default_logger):
    """Sync repos for a single GitHub App installation"""

    # query currently enabled repositories for app installation
    logger.info(f'querying GitHub for enabled repos')
    repos_from_github = github_app_installation_query_repos(installation_token)
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

def github_sync_app_installation_issues(installation_token, installation, logger=default_logger):
    """Sync issues for a single GitHub App installation"""
    installation_json = installation.data
    github_username = installation_json['account']['login']

    # Get all issues in DB related to app installation.
    #
    # Note:
    #
    # It is important to query issues by URL here
    # (i.e. `url__startswith==...`), rather than with a join query
    # like `repo__app_installation=installation`, because latter will
    # omit issues where `GitHubIssue.repo == NULL`, which we also want
    # to include in our issue data updates.
    #
    # `GitHubIssue.repo == NULL` means that the maintainer has
    # disabled the GitHub App on the parent repo for the issue, by
    # removing it from list of selected repos under Profile ->
    # Settings -> Application -> sponsoredissues-maintainer –> Only
    # select repositories (radio button). We are still able to retrieve
    # the latest issue data from deselected repos because all repos
    # used with `sponsoredissues.org` are public.

    issues_in_db = GitHubIssue.objects.filter(
        url__startswith = f'https://github.com/{github_username}/'
    )
    issue_urls_in_db = set(
        issues_in_db.distinct().values_list('url', flat=True)
    )

    # Get the funded issues in the DB.
    #
    # We treat funded issues as a special case when removing issues
    # from the DB. If the maintainer performs an action on GitHub that
    # would remove a funded issue, such as removing the
    # "sponsoredissues.org" label or disabling the GitHub App on the
    # parent repo, we put the issue into a special "frozen" state
    # instead of removing it [1]. Frozen issues still appear on the
    # maintainer's sponsored issues page, but their "Add or Remove
    # Funds" buttons are disabled, and warning messages are shown that
    # explain why the issue(s) are frozen.
    #
    # We never want to delete funded issues from the DB, because:
    #
    # (1) Deleting a funded issue would undo the work of the issue's
    # contributors.
    #
    # (2) The maintainer probably triggered deletion of the funded
    # issue by accident. Thus we want the maintainer to be able to easily
    # undo their mistake, by re-adding the `sponsoredissues.org` label
    # and/or re-enabling the app on the parent repo.
    #
    # (3) Keeping previously funded issues in the database allows us
    # to compute interesting historical stats, such as average funding
    # for closed issues.
    #
    # [1]: Implementation note: In the database, an funded issue is
    # frozen if either: (1) `GitHubIssue.repo` is NULL (indicating
    # that the GitHub App is disabled on the repo), or (2) the JSON
    # data for the issue does not contain the `sponsoredissues.org`
    # label.

    funded_issues = issues_in_db.filter(sponsor_amounts__isnull=False)
    funded_issue_urls_in_db = set(funded_issues.distinct().values_list('url', flat=True))

    # Retrieve the latest JSON issue data from the GitHub GraphQL
    # API, for all issues that are relevant to sponsoredissues.org.
    #
    # An issue is relevant to sponsoredissues.org if either:
    #
    # (1) It belongs to a repo with the "sponsoredissues-maintainer" GitHub
    # App installed *AND* it has the `sponsoredissues.org` label.
    # (2) It has a non-zero amount of funding on sponsoredissues.org.
    #
    # Note that it is possible for any combination of (1) and (2) to
    # be true. For example, the maintainer might accidentally remove
    # the `sponsoredissues.org` label from an issue that already has
    # funding on their sponsored issues page. In that case, the
    # issue is shown in a special "frozen" state, with the "Add or
    # Remove Funds" button disabled.

    logger.info(f'querying GitHub for issues with "sponsoredissues.org" label')
    issues_from_github_with_label = github_app_installation_query_issues_with_sponsoredissues_label(installation_token, github_username)

    logger.info(f'querying GitHub for issues with funding')
    issues_from_github_with_funding = github_app_installation_query_issue_urls(installation_token, funded_issue_urls_in_db)

    # Merge results from two queries above
    issues_from_github = {issue['html_url']: issue for issue in issues_from_github_with_label}
    issues_from_github.update({issue['html_url']: issue for issue in issues_from_github_with_funding})
    issue_urls_from_github = issues_from_github.keys()
    logger.info(f'retrieved latest data for {len(issue_urls_from_github)} issues')

    # Create or update issues that are either funded or "active".
    #
    # For an issue to be "active", all of the following must be true:
    #
    # (1) The issue must be open, *AND*
    # (2) The issue must have the "sponsoredissues.org" label, *AND*
    # (3) The "sponsoredissues-maintainer" GitHub App must be enabled
    # on the repo that contains the issue.

    issue_urls_added = set()
    issue_urls_updated = set()
    issue_urls_removed = set()

    for issue_url in issue_urls_from_github:
        result = github_sync_issue(issues_from_github[issue_url])
        if result is SyncResult.ADDED:
            issue_urls_added.add(issue_url)
        elif result is SyncResult.UPDATED:
            issue_urls_updated.add(issue_url)
        elif result is SyncResult.REMOVED:
            issue_urls_removed.add(issue_url)

    # Remove issues from database that were not included in the GitHub
    # query results for labeled/funded issues above
    # (i.e. `issue_urls_from_github`).
    #
    # An issue could be missing from the GitHub query results for
    # many reasons, including:
    #
    # (1) The maintainer removed the "sponsoredissues.org" label.
    # (2) The maintainer deleted the issue on GitHub. (This
    # corresponds to the red "Delete issue" link in the
    # bottom right corner of the GitHub issue page.)
    # (3) The maintainer deleted the repo that contains the issue.

    issue_urls_to_remove = (issue_urls_in_db
                        - funded_issue_urls_in_db
                        - issue_urls_from_github)

    for issue_url in issue_urls_to_remove:
        GitHubIssue.objects.get(url=issue_url).delete()
        issue_urls_removed.add(issue_url)
        logger.info(f'removed issue {issue_url}')

    logger.info(f'issue sync stats: +{len(issue_urls_added)} ~{len(issue_urls_updated)} -{len(issue_urls_removed)}')

def github_sync_issue(issue_json, logger=default_logger) -> SyncResult:
    """
    Add, update, or remove a GitHubIssue from the database, given the
    the latest JSON issue data from GitHub.
    """
    issue_url = issue_json['html_url']
    issue_state = issue_json['state']

    # Check if issue has the sponsoredissues.org label
    has_label = github_issue_has_sponsoredissues_label(issue_json)

    # Get issue in database if it exists, otherwise `None`
    github_issue = GitHubIssue.objects.filter(url=issue_url).first()

    # Get associated repo for the issue in our database if it exists,
    # otherwise set to `None`.
    #
    # If a repo does not exist in our database, it means that the
    # maintainer has disabled the "sponsoredissues-maintainer" GitHub
    # App on the repo [1]. In that case, the issue should be removed
    # from the database unless it it has funding. If the the issue has
    # funding, we keep the issue and show it in a special "frozen"
    # state on the maintainer's sponsored issues page.
    #
    # [1]: The maintainer can select which repos are disabled/enabled
    # for the app by going to User menu -> Settings -> Applications ->
    # "sponsoredissues-maintainer" on the GitHub website.
    github_repo = GitHubRepo.get_by_issue_url(issue_url)

    # Decide if the issue should exist in our database.
    #
    # If an issue has non-zero funding, we always preserve it in
    # our database, so that we don't lose the data about funding
    # amounts and undo the work of the contributors [1].
    #
    # For unfunded issues, we should only add/update the issue in our
    # database if *all* of the following are true:
    #
    # (1) The repo is currently enabled for the app installation
    # (i.e. the repo currently exists in our GitHubRepo table)
    # (2) The issue is state "open"
    # (3) The issue `sponsoredissues.org` label
    #
    # If any of the above condition are not true, and the issue does
    # not have any funding, we should remove the issue from the
    # database.
    #
    # [1]: Additional notes about issues with non-zero funding:
    #
    #   * We always keep closed issues that received non-zero
    #   funding in our database, because it allows us to compute
    #   interesting historical stats about them (e.g. average
    #   funding for resolved issues).
    #
    #   * Issues that are preserved because they have non-zero
    #   funding, but would otherwise be deleted (e.g. because the
    #   maintainer removed the `sponsoredissues.org` label), are
    #   shown in a "frozen" state on the maintainer's sponsored
    #   issues page. See the following FAQ for further
    #   explanation/discussion:
    #   https://sponsoredissues.org/site/faq#label-removed
    should_exist = (github_issue != None and github_issue.is_funded()) | (github_repo != None and issue_state == 'open' and has_label)

    if should_exist and not github_issue:
        # Create new issue
        assert github_repo
        GitHubIssue.objects.create(
            url=issue_url,
            data=issue_json,
            repo=github_repo
        )
        logger.info(f"added issue: {issue_url}")
        return SyncResult.ADDED
    elif should_exist and github_issue:
        # Update existing issue
        github_issue.data = issue_json
        github_issue.repo = github_repo
        github_issue.save()
        logger.info(f"updated issue: {issue_url}")
        return SyncResult.UPDATED
    elif not should_exist and github_issue:
        # Delete issue (closed or label removed)
        github_issue.delete()
        logger.info(f"deleted issue: {issue_url} (issue closed or label removed, and issue does not have existing funding)")
        return SyncResult.REMOVED
    else:
        # Final case: Issue does not exist in database and should not
        # be added. (i.e. `not should_exist and not github_issue`)
        return SyncResult.IGNORED