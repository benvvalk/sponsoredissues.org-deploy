from django.test import TestCase
from typing import Final
from unittest.mock import patch
import time

from sponsoredissues.github_sync import github_sync_app_installation, github_sync_app_installation_issues, github_sync_app_installation_repos, github_sync_issue
from sponsoredissues.models import GitHubAppInstallation, GitHubRepo, GitHubIssue, IssueSponsorship, Maintainer
from django.contrib.auth.models import User
from sponsoredissues.tests.mock_data import MockData


class SyncReposForInstallationTest(TestCase):
    """Tests for the _sync_installation_repos method."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock maintainer
        maintainer_user_id = 1
        maintainer_user_name = 'maintainer'
        self.maintainer = Maintainer.objects.create(
            github_account_id = maintainer_user_id,
            github_user_json = MockData.user_json(maintainer_user_id, maintainer_user_name),
            github_sponsors_profile_url = f'https://github.com/sponsors/{maintainer_user_name}'
        )

        # Mock installation data
        installation_json = MockData.installation_json()
        installation_url = installation_json['html_url']
        self.installation = GitHubAppInstallation.objects.create(
            url=installation_url,
            data=installation_json,
            maintainer=self.maintainer
        )

    @patch('sponsoredissues.github_sync.github_app_installation_query_repos')
    def test_add_new_public_repo(self, mock_query_repos):
        """Test adding a new public repository."""
        repo_json = MockData.repo_json()
        mock_query_repos.return_value = [ repo_json ]

        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify the repo was created in the database
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.first()
        assert repo
        self.assertEqual(repo.url, repo_json['html_url'])

    @patch('sponsoredissues.github_sync.github_app_installation_query_repos')
    def test_update_existing_repo(self, mock_query_repos):
        """Test updating an existing repository's timestamp."""
        # Create an existing repo in the database
        repo_json = MockData.repo_json()
        repo_url = repo_json['html_url']
        existing_repo = GitHubRepo.objects.create(url=repo_url, app_installation=self.installation)
        original_updated_at = existing_repo.updated_at

        # Wait a moment to ensure timestamp will be different
        time.sleep(0.01)

        # Mock the API response with the same repo
        mock_query_repos.return_value = [ repo_json ]

        # Call the method
        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify repo still exists and was updated
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.get(url=repo_url)
        self.assertGreater(repo.updated_at, original_updated_at)

    @patch('sponsoredissues.github_sync.github_app_installation_query_repos')
    def test_remove_repo_no_longer_accessible(self, mock_query_repos):
        """Test removing a repository that is no longer accessible."""
        # Create an existing repo in the database
        repo_json = MockData.repo_json()
        repo_url = repo_json['html_url']
        GitHubRepo.objects.create(url=repo_url, app_installation=self.installation)

        # Mock the API response with empty list (no repos accessible)
        mock_query_repos.return_value = []

        # Call the method
        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify repo was deleted from database
        self.assertEqual(GitHubRepo.objects.count(), 0)
        self.assertFalse(GitHubRepo.objects.filter(url=repo_url).exists())

    @patch('sponsoredissues.github_sync.github_app_installation_query_repos')
    def test_skip_private_repos(self, mock_query_repos):
        """Test that private repositories are skipped and not added to database."""
        # Mock the API response with one private repo
        repo_json = MockData.repo_json(private=True)
        mock_query_repos.return_value = [ repo_json ]

        # Call the method
        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify no repo was created
        self.assertEqual(GitHubRepo.objects.count(), 0)


class SyncIssuesForInstallationTest(TestCase):
    """Tests for the _sync_installation_issues method."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock user
        self.user = User.objects.create_user(
            username=MockData.DEFAULT_USER_NAME,
            email='test@example.com'
        )

        # Mock maintainer
        maintainer_user_id = 1
        maintainer_user_name = 'maintainer'
        self.maintainer = Maintainer.objects.create(
            github_account_id = maintainer_user_id,
            github_user_json = MockData.user_json(maintainer_user_id, maintainer_user_name),
            github_sponsors_profile_url = f'https://github.com/sponsors/{maintainer_user_name}'
        )

        # Mock installation
        installation_json = MockData.installation_json()
        installation_url = installation_json['html_url']
        self.installation = GitHubAppInstallation.objects.create(
            url=installation_url,
            data=installation_json,
            maintainer=self.maintainer
        )

        # Mock repo
        repo_json = MockData.repo_json()
        self.repo = GitHubRepo.objects.create(
            url=repo_json['html_url'],
            app_installation=self.installation,
        )

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_add_new_issue_with_label(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test adding a new issue with sponsoredissues.org label."""
        # Mock the API response with one new issue
        issue_json : Final = MockData.issue_json()
        mock_query_issues_with_label.return_value = [ issue_json ]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify the issue was created in the database
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, issue_json['html_url'])
        self.assertEqual(issue.data['title'], issue_json['title'])
        self.assertEqual(issue.repo, self.repo)

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_update_existing_issue(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test updating an existing issue's data."""
        # Create an existing issue in the database
        existing_issue_json : Final = MockData.issue_json()
        existing_issue = GitHubIssue.objects.create(
            url=existing_issue_json['html_url'],
            data=existing_issue_json,
            repo=self.repo
        )
        original_updated_at = existing_issue.updated_at

        # Wait a moment to ensure timestamp will be different
        time.sleep(0.01)

        # Mock the API response with updated data
        updated_issue_json = existing_issue_json.copy()
        updated_issue_json['title'] = 'New Title'
        updated_issue_json['body'] = 'New body'
        mock_query_issues_with_label.return_value = [ updated_issue_json ]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify issue still exists and was updated
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.get(url=existing_issue_json['html_url'])
        self.assertEqual(issue.data['title'], 'New Title')
        self.assertEqual(issue.data['body'], 'New body')
        self.assertGreater(issue.updated_at, original_updated_at)

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_issue_assigned_to_correct_repo(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test that issues are correctly assigned to their parent repository."""
        # Create a second repo
        repo1 = self.repo
        repo2_name = 'another-repo'
        repo2 = GitHubRepo.objects.create(
            url=f'https://github.com/{MockData.DEFAULT_USER_NAME}/{repo2_name}',
            app_installation=self.installation
        )

        # Mock API response with issues from different repos
        issue1_json = MockData.issue_json()
        issue2_json = MockData.issue_json(repo_name=repo2_name)
        mock_query_issues_with_label.return_value = [issue1_json, issue2_json]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify both issues were created with correct repo assignments
        self.assertEqual(GitHubIssue.objects.count(), 2)
        issue1 = GitHubIssue.objects.get(url=issue1_json['html_url'])
        issue2 = GitHubIssue.objects.get(url=issue2_json['html_url'])
        self.assertEqual(issue1.repo, repo1)
        self.assertEqual(issue2.repo, repo2)

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_mixed_add_update_remove_operations(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test mixed operations: add new issue, update existing, remove old."""
        # Set up test data
        existing_issue_json = MockData.issue_json(issue_number=1)
        GitHubIssue.objects.create(
            url=existing_issue_json['html_url'],
            data=existing_issue_json,
            repo=self.repo
        )

        removed_issue_json = MockData.issue_json(issue_number=2)
        GitHubIssue.objects.create(
            url=removed_issue_json['html_url'],
            data=removed_issue_json,
            repo=self.repo
        )

        new_issue_json = MockData.issue_json(issue_number=3)

        # Mock API response: update issue, add issue, remove issue
        updated_issue_json = existing_issue_json.copy()
        updated_issue_json['title'] = 'Updated Issue 1'
        mock_query_issues_with_label.return_value = [updated_issue_json, new_issue_json]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify operations
        self.assertEqual(GitHubIssue.objects.count(), 2)  # issue1 and issue3

        # Check existing issue updated
        issue = GitHubIssue.objects.get(url=existing_issue_json['html_url'])
        self.assertEqual(issue.data['title'], 'Updated Issue 1')

        # Check new issue added
        self.assertTrue(GitHubIssue.objects.filter(url=new_issue_json['html_url']).exists())

        # Check existing issue removed
        self.assertFalse(GitHubIssue.objects.filter(url=removed_issue_json['html_url']).exists())

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_issue_remove_closed_issue_without_funding(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test that an unfunded issue is removed when closed."""
        # Create an existing open issue
        issue_json = MockData.issue_json()
        GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )

        # Mock API response with the same issue but now closed
        closed_issue_json = issue_json.copy()
        closed_issue_json['state'] = 'closed'
        mock_query_issues_with_label.return_value = [closed_issue_json]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify issue was deleted
        self.assertEqual(GitHubIssue.objects.count(), 0)

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_issue_preserve_closed_issue_with_funding(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test that an funded issue is kept when closed."""
        # Create an existing open issue
        open_issue_json = MockData.issue_json()
        funded_issue = GitHubIssue.objects.create(
            url=open_issue_json['html_url'],
            data=open_issue_json,
            repo=self.repo
        )
        # Add funding to the issue
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        # Mock API response with the same issue but now closed
        closed_issue_json = open_issue_json.copy()
        closed_issue_json['state'] = 'closed'
        mock_query_issues_with_label.return_value = [closed_issue_json]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify issue closed issue still exists
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, open_issue_json['html_url'])
        self.assertEqual(issue.data['title'], open_issue_json['title'])
        self.assertEqual(issue.repo, self.repo)

    @patch('sponsoredissues.github_sync.github_app_installation_query_issue_urls')
    @patch('sponsoredissues.github_sync.github_app_installation_query_issues_with_sponsoredissues_label')
    def test_preserve_funded_issues(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test removing an unfunded issue when sponsoredissues.org label is removed."""
        # Create an existing unfunded issue in the database
        unfunded_issue_json = MockData.issue_json(issue_number=3)
        GitHubIssue.objects.create(
            url=unfunded_issue_json['html_url'],
            data=unfunded_issue_json,
            repo=self.repo
        )

        funded_issue_json = MockData.issue_json(issue_number=4)
        funded_issue = GitHubIssue.objects.create(
            url=funded_issue_json['html_url'],
            data=funded_issue_json,
            repo=self.repo
        )
        # Add funding to the issue
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        # Mock the API response.
        # Simulate removal of `sponsoredissues.org` label from both
        # issues, by returning empty list from mocked
        # `github_app_installation_query_issues_with_sponsoredissues_label`
        # method.
        mock_query_issues_with_label.return_value = []
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation)

        # Verify funded was preserved and unfunded issue was deleted
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue_json['html_url']).exists())
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue_json['html_url']).exists())
        self.assertEqual(GitHubIssue.objects.count(), 1)

class SyncAppInstallationTest(TestCase):
    """Tests for `github_sync_app_installation`."""

    def setUp(self):
        """Set up test fixtures."""
        # Create test user for funded issues
        self.user = User.objects.create_user(username='testuser', email='test@example.com')

        maintainer1_user_id = 1
        maintainer1_user_name = 'maintainer1'
        self.maintainer1 = Maintainer.objects.create(
            github_account_id = maintainer1_user_id,
            github_user_json = MockData.user_json(maintainer1_user_id, maintainer1_user_name),
            github_sponsors_profile_url = f'https://github.com/sponsors/{maintainer1_user_name}'
        )

        maintainer2_user_id = 2
        maintainer2_user_name = 'maintainer2'
        self.maintainer2 = Maintainer.objects.create(
            github_account_id = maintainer2_user_id,
            github_user_json = MockData.user_json(maintainer2_user_id, maintainer2_user_name),
            github_sponsors_profile_url = f'https://github.com/sponsors/{maintainer2_user_name}'
        )

    @patch('sponsoredissues.github_sync.github_sync_maintainer')
    @patch('sponsoredissues.github_sync.github_sync_app_installation_repos')
    @patch('sponsoredissues.github_sync.github_sync_app_installation_issues')
    def test_suspended_installation_removes_unfunded_issues(self, mock_sync_issues, mock_sync_repos, mock_sync_maintainer):
        """Test that suspended installations remove repos and unfunded issues."""
        mock_sync_maintainer.return_value = self.maintainer1

        # Mock installation with suspended_at field
        suspended_installation_json = MockData.installation_json(
            installation_id = 99999,
            suspended_at = '2024-01-01T00:00:00Z'
        )
        suspended_installation = GitHubAppInstallation.objects.create(
            url=suspended_installation_json['html_url'],
            data=suspended_installation_json,
            maintainer=self.maintainer1
        )

        # Create repos and issues for the suspended account
        repo1_name = 'repo1'
        repo1 = GitHubRepo.objects.create(
            url=f'https://github.com/{MockData.DEFAULT_USER_NAME}/{repo1_name}',
            app_installation=suspended_installation,
        )
        repo2_name = 'repo2'
        repo2 = GitHubRepo.objects.create(
            url=f'https://github.com/{MockData.DEFAULT_USER_NAME}/{repo2_name}',
            app_installation=suspended_installation,
        )

        # Create an unfunded issue
        unfunded_issue_json = MockData.issue_json(issue_number=1, repo_name=repo1_name)
        unfunded_issue = GitHubIssue.objects.create(
            url=unfunded_issue_json['html_url'],
            data=unfunded_issue_json,
            repo=repo1
        )

        # Create a funded issue (should be kept)
        funded_issue_json = MockData.issue_json(issue_number=2, repo_name=repo2_name)
        funded_issue = GitHubIssue.objects.create(
            url=funded_issue_json['html_url'],
            data=funded_issue_json,
            repo=repo1
        )
        # Add funding to the issue
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        # Call the method
        with patch('sponsoredissues.github_sync.github_app_installation_query_token') as mock_token:
            mock_token.return_value = MockData.APP_INSTALLATION_TOKEN
            with patch('sponsoredissues.github_sync.github_app_installation_query_json') as mock_query_json:
                mock_query_json.return_value = suspended_installation_json
                github_sync_app_installation(suspended_installation_json['id'])

        # Verify suspended installation was removed
        self.assertFalse(GitHubAppInstallation.objects.filter(url=suspended_installation_json['html_url']).exists())

        # Verify repos were removed
        self.assertEqual(GitHubRepo.objects.all().count(), 0)

        # Verify unfunded issue was removed
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue_json['html_url']).exists())

        # Verify funded issue was kept (has non-null repo reference initially, but repo was deleted)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue_json['html_url']).exists())
        remaining_issue = GitHubIssue.objects.get(url=funded_issue_json['html_url'])
        self.assertIsNone(remaining_issue.repo)  # Repo should be null due to `on_delete=models.SET_NULL`

        # Verify Maintainer was synced despite suspended app installation.
        # We need to do this to ensure activated/deactivated state of
        # "Sponsor @user" button on sponsored issues page stays in
        # sync with existence/non-existence of GitHub Sponsors
        # profile.
        self.assertEqual(mock_sync_maintainer.call_count, 1)

        # Verify _sync_installation_repos and _sync_installation_issues were NOT called for suspended installation
        mock_sync_repos.assert_not_called()
        mock_sync_issues.assert_not_called()

    @patch('sponsoredissues.github_sync.github_sync_maintainer')
    @patch('sponsoredissues.github_sync.github_sync_app_installation_repos')
    @patch('sponsoredissues.github_sync.github_sync_app_installation_issues')
    def test_mix_of_suspended_and_active_installations(self, mock_sync_issues, mock_sync_repos, mock_sync_maintainer):
        """Test that mix of suspended and active installations are handled correctly."""
        # Mock suspended installation
        suspended_user_name = 'suspended-user'
        suspended_installation_json = MockData.installation_json(
            installation_id = 1,
            user_name = suspended_user_name,
            suspended_at = '2024-01-01T00:00:00Z'
        )
        suspended_installation = GitHubAppInstallation.objects.create(
            url=suspended_installation_json['html_url'],
            data=suspended_installation_json,
            maintainer=self.maintainer1
        )

        suspended_repo_name = 'repo1'
        suspended_repo = GitHubRepo.objects.create(
            url=f'https://github.com/{suspended_user_name}/{suspended_repo_name}',
            app_installation=suspended_installation)

        suspended_issue_json = MockData.issue_json(
            user_name=suspended_user_name,
            issue_number=1
        )
        GitHubIssue.objects.create(
            url=suspended_issue_json['html_url'],
            data=suspended_issue_json,
            repo=suspended_repo
        )

        # Mock active installation
        active_user_name = 'active-user'
        active_installation_json = MockData.installation_json(
            installation_id = 2,
            user_name = active_user_name
        )
        active_installation = GitHubAppInstallation.objects.create(
            url=active_installation_json['html_url'],
            data=active_installation_json,
            maintainer=self.maintainer2
        )

        active_repo_name = 'repo1'
        active_repo = GitHubRepo.objects.create(
            url=f'https://github.com/{active_user_name}/{active_repo_name}',
            app_installation=active_installation)

        # Sync installations

        with patch('sponsoredissues.github_sync.github_app_installation_query_token', return_value=MockData.APP_INSTALLATION_TOKEN):
            with patch('sponsoredissues.github_sync.github_app_installation_query_json') as mock_query_json:
                mock_query_json.return_value = suspended_installation_json
                mock_sync_maintainer.return_value = self.maintainer1
                github_sync_app_installation(suspended_installation_json['id'])

            with patch('sponsoredissues.github_sync.github_app_installation_query_json') as mock_query_json:
                mock_query_json.return_value = active_installation_json
                mock_sync_maintainer.return_value = self.maintainer2
                github_sync_app_installation(active_installation_json['id'])

        # Verify suspended installation was removed
        self.assertFalse(GitHubAppInstallation.objects.filter(url=suspended_installation_json['html_url']).exists())

        # Verify repo and unfunded issue from suspended installation were removed
        self.assertFalse(GitHubRepo.objects.filter(url__startswith=f'https://github.com/{suspended_user_name}/').exists())
        self.assertFalse(GitHubIssue.objects.filter(url__startswith=f'https://github.com/{suspended_user_name}/').exists())

        # Verify active installation was not removed
        self.assertTrue(GitHubAppInstallation.objects.filter(url=active_installation_json['html_url']).exists())

        # Verify repo from active installation was not removed
        self.assertTrue(GitHubRepo.objects.filter(url__startswith=f'https://github.com/{active_user_name}/').exists())

        # Verify Maintainer was synced despite suspended app installation.
        # We need to do this to ensure activated/deactivated state of
        # "Sponsor @user" button on sponsored issues page stays in
        # sync with existence/non-existence of GitHub Sponsors
        # profile.
        self.assertEqual(mock_sync_maintainer.call_count, 2)

        # Verify github_sync_*_for_app_installation methods were called only on active installation
        self.assertEqual(mock_sync_repos.call_count, 1)
        self.assertEqual(mock_sync_issues.call_count, 1)

class SyncIssueTest(TestCase):

    def setUp(self):
        """Set up test fixtures."""
        # Mock user
        self.user = User.objects.create_user(
            username=MockData.DEFAULT_USER_NAME,
            email='test@example.com'
        )

        # Mock maintainer
        maintainer_user_id = 1
        maintainer_user_name = 'maintainer'
        self.maintainer = Maintainer.objects.create(
            github_account_id = maintainer_user_id,
            github_user_json = MockData.user_json(maintainer_user_id, maintainer_user_name),
            github_sponsors_profile_url = f'https://github.com/sponsors/{maintainer_user_name}'
        )

        # Mock installation
        installation_json = MockData.installation_json()
        installation_url = installation_json['html_url']
        self.installation = GitHubAppInstallation.objects.create(
            url=installation_url,
            data=installation_json,
            maintainer=self.maintainer
        )

        # Mock repo
        repo_json = MockData.repo_json()
        self.repo = GitHubRepo.objects.create(
            url=repo_json['html_url'],
            app_installation=self.installation,
        )

    def test_add_issue_with_label(self):
        """Test adding a new issue with `sponsoredissues.org` label."""
        issue_json = MockData.issue_json()

        # Call the method
        github_sync_issue(issue_json)

        # Verify the issue was created in the database
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, issue_json['html_url'])
        self.assertEqual(issue.data['title'], issue_json['title'])

        # Verify issue linked to correct repo in database
        self.assertEqual(issue.repo, self.repo)

    def test_update_existing_issue(self):
        """Test updating an existing issue's data."""
        # Create an existing issue in the database
        existing_issue_json : Final = MockData.issue_json()
        existing_issue = GitHubIssue.objects.create(
            url=existing_issue_json['html_url'],
            data=existing_issue_json,
            repo=self.repo
        )
        original_updated_at = existing_issue.updated_at

        # Wait a moment to ensure timestamp will be different
        time.sleep(0.01)

        # Mock the API response with updated data
        updated_issue_json = existing_issue_json.copy()
        updated_issue_json['title'] = 'New Title'
        updated_issue_json['body'] = 'New body'

        # Call the method
        github_sync_issue(updated_issue_json)

        # Verify issue still exists and was updated
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.get(url=existing_issue_json['html_url'])
        self.assertEqual(issue.data['title'], 'New Title')
        self.assertEqual(issue.data['body'], 'New body')
        self.assertGreater(issue.updated_at, original_updated_at)

    def test_remove_closed_issue_if_unfunded(self):
        """Test that issue state changes (open to closed) are properly updated."""
        # Create an existing open issue
        issue_json = MockData.issue_json()
        GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )

        # Mock API response with the same issue but now closed
        closed_issue_json = issue_json.copy()
        closed_issue_json['state'] = 'closed'

        # Call the method
        github_sync_issue(closed_issue_json)

        # Verify closed issue deleted (because unfunded)
        self.assertEqual(GitHubIssue.objects.count(), 0)

    def test_keep_closed_issue_if_funded(self):
        """Test that issue state changes (open to closed) are properly updated."""
        # Create an existing open issue
        issue_json = MockData.issue_json()
        funded_issue = GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        # Mock API response with the same issue but now closed
        closed_issue_json = issue_json.copy()
        closed_issue_json['state'] = 'closed'

        # Call the method
        github_sync_issue(closed_issue_json)

        # Verify closed issue is kept (because it was funded)
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, issue_json['html_url'])
        self.assertEqual(issue.data['title'], issue_json['title'])

    def test_keep_funded_issue_if_label_removed(self):
        # Create an existing open issue
        issue_json = MockData.issue_json()
        funded_issue = GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        unlabeled_issue_json = issue_json.copy()
        unlabeled_issue_json['labels'] = []

        # Call the method
        github_sync_issue(unlabeled_issue_json)

        # Verify the issue was created in the database
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, issue_json['html_url'])
        self.assertEqual(issue.data['title'], issue_json['title'])

    def test_keep_funded_issue_if_repo_disabled(self):
        # Create an existing open issue
        issue_json = MockData.issue_json()
        funded_issue = GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        self.repo.delete()

        # Call the method
        github_sync_issue(issue_json)

        # Verify the issue was created in the database
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, issue_json['html_url'])
        self.assertEqual(issue.data['title'], issue_json['title'])

    def test_skip_add_issue_from_disabled_repo(self):
        issue_json = MockData.issue_json(repo_name='disabled_repo')

        # Call the method
        github_sync_issue(issue_json)

        # Verify issue is *not* added to database, because
        # the associated repo doesn't exist in the database.
        #
        # If the associated repo doesn't exist in the database, it
        # indicates that GitHub App is currently disabled for that
        # repo.
        self.assertEqual(GitHubIssue.objects.count(), 0)

    def test_remove_unfunded_issue_when_label_removed(self):
        """Test that unfunded issue gets deleted when sponsoredissues.org label is removed."""
        # Create an existing open issue with label
        issue_json = MockData.issue_json()
        GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )

        # Remove the sponsoredissues.org label
        unlabeled_issue_json = issue_json.copy()
        unlabeled_issue_json['labels'] = []

        # Call the method
        github_sync_issue(unlabeled_issue_json)

        # Verify the unfunded issue was deleted
        self.assertEqual(GitHubIssue.objects.count(), 0)

    def test_remove_unfunded_issue_when_repo_disabled(self):
        """Test that unfunded issue gets deleted when its parent repo is disabled."""
        # Create an existing open issue with label
        issue_json = MockData.issue_json()
        GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=self.repo
        )

        # Disable the repo by removing it from database
        self.repo.delete()

        # Call the method with the issue (still has label and is open)
        github_sync_issue(issue_json)

        # Verify the unfunded issue was deleted
        self.assertEqual(GitHubIssue.objects.count(), 0)

    def test_skip_add_closed_issue_with_label_but_unfunded(self):
        """Test that closed issue with label but no funding is not added to database."""
        # Create issue JSON for closed issue with label
        issue_json = MockData.issue_json(issue_state='closed')

        # Call the method
        github_sync_issue(issue_json)

        # Verify the issue was not added (closed + unfunded = should not exist)
        self.assertEqual(GitHubIssue.objects.count(), 0)

    def test_repo_reference_updated_when_repo_reenabled(self):
        """Test that issue's repo reference gets updated when repo is re-enabled."""
        # Create an existing funded issue with repo=None (simulating disabled repo)
        issue_json = MockData.issue_json()
        funded_issue = GitHubIssue.objects.create(
            url=issue_json['html_url'],
            data=issue_json,
            repo=None  # Repo was previously disabled
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            issue=funded_issue
        )

        # Verify repo is None initially
        self.assertIsNone(funded_issue.repo)

        # Now "re-enable" the repo by ensuring it exists in database
        # (self.repo already exists from setUp)

        # Call the method with updated issue JSON
        github_sync_issue(issue_json)

        # Verify the issue's repo reference was updated
        updated_issue = GitHubIssue.objects.get(url=issue_json['html_url'])
        self.assertEqual(updated_issue.repo, self.repo)
        self.assertIsNotNone(updated_issue.repo)