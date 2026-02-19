from django.test import TestCase
from typing import Final
from unittest.mock import patch
import time

from sponsoredissues.github_app import GitHubAppInstallationClass
from sponsoredissues.github_sync import github_sync_app_installation, github_sync_app_installation_issues, github_sync_app_installation_repos, github_sync_issue
from sponsoredissues.models import GitHubAppInstallation, GitHubRepo, GitHubIssue, SponsorAmount
from django.contrib.auth.models import User

class MockData:
    APP_INSTALLATION_TOKEN : Final = 'dummy-token'
    DEFAULT_USER_NAME : Final = 'test-user'
    DEFAULT_REPO_NAME : Final = 'test-repo'

    @staticmethod
    def installation_json(
        installation_id=1111,
        user_name=DEFAULT_USER_NAME,
        suspended_at=None,
    ):
        json = {
            'id': installation_id,
            'account': {
                'login': f'{user_name}',
                'html_url': f'https://github.com/{user_name}'
            },
            'html_url': f'https://github.com/settings/installations/{installation_id}'
        }

        if suspended_at:
            json['suspended_at'] = suspended_at

        return json

    @staticmethod
    def repo_json(
        user_name=DEFAULT_USER_NAME,
        repo_name=DEFAULT_REPO_NAME,
        private=False
    ):
        return {
            'name': f'{repo_name}',
            'html_url': f'https://github.com/{user_name}/{repo_name}',
            'private': private
        }

    @staticmethod
    def issue_json(
        user_name=DEFAULT_USER_NAME,
        repo_name=DEFAULT_REPO_NAME,
        issue_number=1,
        issue_state='open'
    ):
        return {
            'number': issue_number,
            'title': 'Test Issue',
            'body': 'Test body',
            'state': issue_state,
            'html_url': f'https://github.com/{user_name}/{repo_name}/issues/{issue_number}',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [
                {'name': 'sponsoredissues.org', 'color': '000000'}
            ],
            'user': {'login': f'{user_name}'},
            'repository' : {
                'html_url': f'https://github.com/{user_name}/{repo_name}'
            }
        }

class SyncReposForInstallationTest(TestCase):
    """Tests for the _sync_installation_repos method."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock installation data
        self.installation_json = MockData.installation_json()
        self.installation_api = GitHubAppInstallationClass.from_json(self.installation_json)
        installation_url = self.installation_json['html_url']
        self.installation = GitHubAppInstallation.objects.create(url=installation_url)

    @patch.object(GitHubAppInstallationClass, 'query_repos')
    def test_add_new_public_repo(self, mock_query_repos):
        """Test adding a new public repository."""
        repo_json = MockData.repo_json()
        mock_query_repos.return_value = [ repo_json ]

        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify the repo was created in the database
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.first()
        self.assertEqual(repo.url, repo_json['html_url'])

    @patch.object(GitHubAppInstallationClass, 'query_repos')
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
        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify repo still exists and was updated
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.get(url=repo_url)
        self.assertGreater(repo.updated_at, original_updated_at)

    @patch.object(GitHubAppInstallationClass, 'query_repos')
    def test_remove_repo_no_longer_accessible(self, mock_query_repos):
        """Test removing a repository that is no longer accessible."""
        # Create an existing repo in the database
        repo_json = MockData.repo_json()
        repo_url = repo_json['html_url']
        GitHubRepo.objects.create(url=repo_url, app_installation=self.installation)

        # Mock the API response with empty list (no repos accessible)
        mock_query_repos.return_value = []

        # Call the method
        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify repo was deleted from database
        self.assertEqual(GitHubRepo.objects.count(), 0)
        self.assertFalse(GitHubRepo.objects.filter(url=repo_url).exists())

    @patch.object(GitHubAppInstallationClass, 'query_repos')
    def test_skip_private_repos(self, mock_query_repos):
        """Test that private repositories are skipped and not added to database."""
        # Mock the API response with one private repo
        repo_json = MockData.repo_json(private=True)
        mock_query_repos.return_value = [ repo_json ]

        # Call the method
        github_sync_app_installation_repos(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

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

        # Mock installation
        self.installation_json = MockData.installation_json()
        self.installation_api = GitHubAppInstallationClass.from_json(self.installation_json)
        installation_url = self.installation_json['html_url']
        self.installation = GitHubAppInstallation.objects.create(url=installation_url)

        # Mock repo
        repo_json = MockData.repo_json()
        self.repo = GitHubRepo.objects.create(
            url=repo_json['html_url'],
            app_installation=self.installation,
        )

    @patch.object(GitHubAppInstallationClass, 'query_issue_urls')
    @patch.object(GitHubAppInstallationClass, 'query_issues_with_sponsoredissues_label')
    def test_add_new_issue_with_label(self, mock_query_issues_with_label, mock_query_issues_with_funding):
        """Test adding a new issue with sponsoredissues.org label."""
        # Mock the API response with one new issue
        issue_json : Final = MockData.issue_json()
        mock_query_issues_with_label.return_value = [ issue_json ]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify the issue was created in the database
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        assert issue
        self.assertEqual(issue.url, issue_json['html_url'])
        self.assertEqual(issue.data['title'], issue_json['title'])
        self.assertEqual(issue.repo, self.repo)

    @patch.object(GitHubAppInstallationClass, 'query_issue_urls')
    @patch.object(GitHubAppInstallationClass, 'query_issues_with_sponsoredissues_label')
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
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify issue still exists and was updated
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.get(url=existing_issue_json['html_url'])
        self.assertEqual(issue.data['title'], 'New Title')
        self.assertEqual(issue.data['body'], 'New body')
        self.assertGreater(issue.updated_at, original_updated_at)

    @patch.object(GitHubAppInstallationClass, 'query_issue_urls')
    @patch.object(GitHubAppInstallationClass, 'query_issues_with_sponsoredissues_label')
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
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify both issues were created with correct repo assignments
        self.assertEqual(GitHubIssue.objects.count(), 2)
        issue1 = GitHubIssue.objects.get(url=issue1_json['html_url'])
        issue2 = GitHubIssue.objects.get(url=issue2_json['html_url'])
        self.assertEqual(issue1.repo, repo1)
        self.assertEqual(issue2.repo, repo2)

    @patch.object(GitHubAppInstallationClass, 'query_issue_urls')
    @patch.object(GitHubAppInstallationClass, 'query_issues_with_sponsoredissues_label')
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
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify operations
        self.assertEqual(GitHubIssue.objects.count(), 2)  # issue1 and issue3

        # Check existing issue updated
        issue = GitHubIssue.objects.get(url=existing_issue_json['html_url'])
        self.assertEqual(issue.data['title'], 'Updated Issue 1')

        # Check new issue added
        self.assertTrue(GitHubIssue.objects.filter(url=new_issue_json['html_url']).exists())

        # Check existing issue removed
        self.assertFalse(GitHubIssue.objects.filter(url=removed_issue_json['html_url']).exists())

    @patch.object(GitHubAppInstallationClass, 'query_issue_urls')
    @patch.object(GitHubAppInstallationClass, 'query_issues_with_sponsoredissues_label')
    def test_issue_state_change_open_to_closed(self, mock_query_issues_with_label, mock_query_issues_with_funding):
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
        mock_query_issues_with_label.return_value = [closed_issue_json]
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

        # Verify issue still exists (not deleted)
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.get(url=issue_json['html_url'])

        # Verify state was updated to closed
        self.assertEqual(issue.data['state'], 'closed')

    @patch.object(GitHubAppInstallationClass, 'query_issue_urls')
    @patch.object(GitHubAppInstallationClass, 'query_issues_with_sponsoredissues_label')
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
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
        )

        # Mock the API response.
        # Simulate removal of `sponsoredissues.org` label from both
        # issues, by returning empty list from
        # mocked `query_issues_with_sponsoredissues_label` method.
        mock_query_issues_with_label.return_value = []
        mock_query_issues_with_funding.return_value = []

        # Call the method
        github_sync_app_installation_issues(MockData.APP_INSTALLATION_TOKEN, self.installation_json, self.installation_api)

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

    @patch('sponsoredissues.github_sync.github_sync_app_installation_repos')
    @patch('sponsoredissues.github_sync.github_sync_app_installation_issues')
    def test_suspended_installation_removes_unfunded_issues(self, mock_sync_issues, mock_sync_repos):
        """Test that suspended installations remove repos and unfunded issues."""
        # Mock installation with suspended_at field
        suspended_installation_json = MockData.installation_json(
            installation_id = 99999,
            suspended_at = '2024-01-01T00:00:00Z'
        )
        suspended_installation = GitHubAppInstallation.objects.create(url=suspended_installation_json['html_url'])

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
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
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

        # Verify _sync_installation_repos and _sync_installation_issues were NOT called for suspended installation
        mock_sync_repos.assert_not_called()
        mock_sync_issues.assert_not_called()

    @patch('sponsoredissues.github_sync.github_sync_app_installation_repos')
    @patch('sponsoredissues.github_sync.github_sync_app_installation_issues')
    def test_mix_of_suspended_and_active_installations(self, mock_sync_issues, mock_sync_repos):
        """Test that mix of suspended and active installations are handled correctly."""
        # Mock suspended installation
        suspended_user_name = 'suspended-user'
        suspended_installation_json = MockData.installation_json(
            installation_id = 1,
            user_name = suspended_user_name,
            suspended_at = '2024-01-01T00:00:00Z'
        )
        suspended_installation = GitHubAppInstallation.objects.create(url=suspended_installation_json['html_url'])

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
            url=active_installation_json['html_url']
        )

        active_repo_name = 'repo1'
        active_repo = GitHubRepo.objects.create(
            url=f'https://github.com/{active_user_name}/{active_repo_name}',
            app_installation=active_installation)

        # Sync installations

        with patch('sponsoredissues.github_sync.github_app_installation_query_token', return_value=MockData.APP_INSTALLATION_TOKEN):
            with patch('sponsoredissues.github_sync.github_app_installation_query_json') as mock_query_json:
                mock_query_json.return_value = suspended_installation_json
                github_sync_app_installation(suspended_installation_json['id'])

            with patch('sponsoredissues.github_sync.github_app_installation_query_json') as mock_query_json:
                mock_query_json.return_value = active_installation_json
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

        # Mock installation
        installation_json = MockData.installation_json()
        installation_url = installation_json['html_url']
        self.installation_api = GitHubAppInstallationClass.from_json(installation_json)
        self.installation = GitHubAppInstallation.objects.create(url=installation_url)

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
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
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
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
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
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
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
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
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