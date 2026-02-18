from django.db import migrations
import sys
import os

# Add parent directory to path so we can import from sponsoredissues package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def init_app_installation_for_existing_repos(apps, schema_editor):
    """
    Fill in any NULLs for `GithubRepo.app_installation` with the
    correct values, so that we can set `null=False` for
    `GitHub.app_installation` in the next database migration.

    For `GitHubRepo` where `GitHubRepo.app_installation` == NULL:

    1. Query the GitHub API to determine which app installation URL
    we need to use.

    2. Create an `GitHubAppInstallation` instance in the database, if
    it doesn't already exist.

    3. Set `GitHubRepo.app_installation` field to the
    `GitHubAppInstallation` instance we found/created in Step 2.

    Note: In case of a runtime error (e.g. GitHub API times out), we
    intentionally keep any `app_installation` values that we've
    already set so far. It is safe to do this, and it allows us to
    make forward progress in case we need to run the migration
    multiple times (e.g. due to a spotty internet connection).
    """
    from sponsoredissues.github_app import github_app_query_installation_for_github_account

    # Import models

    GitHubRepo = apps.get_model('sponsoredissues', 'GitHubRepo')
    GitHubAppInstallation = apps.get_model('sponsoredissues', 'GitHubAppInstallation')

    # Get repos where `GitHubRepo.app_installation` == NULL

    unlinked_repos = GitHubRepo.objects.filter(app_installation__isnull=True)

    if not unlinked_repos.exists():
        print("Found 0 unlinked repos with `app_installation` == NULL, nothing to do")
        return

    print(f"Found {unlinked_repos.count()} unlinked repos with `app_installation` == NULL")

    # Cache of GitHub username/orgname -> `GitHubAppInstallation` mappings.
    account_to_installation_map = {}

    num_repos_linked = 0
    for repo in unlinked_repos:
        print(f"Fixing unlinked repo: {repo.url}...")
        github_username = repo.url.split('/')[-2]
        installation = account_to_installation_map.get(github_username)
        if not installation:
            installation_json = github_app_query_installation_for_github_account(github_username)
            # TODO: Not sure this is the right key / URL format.
            # I might have to construct the URL myself from the installation ID.
            installation_url = installation_json['html_url']
            installation, created = GitHubAppInstallation.objects.get_or_create(url=installation_url)
            if created:
                print(f"Created `GitHubAppInstallation` for `{github_username}`")
            account_to_installation_map[github_username] = installation
        assert installation
        repo.app_installation = installation
        repo.save()
        num_repos_linked += 1

    print(f"Success: Fixed {num_repos_linked} NULL values for `GitHubRepo.app_installation`")

class Migration(migrations.Migration):

    dependencies = [
        ('sponsoredissues', '0011_githubappinstallation_githubrepo_app_installation'),
    ]

    operations = [
        # Note: `reverse_code` is a no-op here because it does no harm
        # to leave the newly-generated `app_installation` values in
        # place when reversing the migration. Also, if the parent
        # migration (0011) is subsequently reversed, it will drop the
        # entire `app_installation` column anyway.
        migrations.RunPython(
            init_app_installation_for_existing_repos,
            reverse_code=migrations.RunPython.noop)
    ]
