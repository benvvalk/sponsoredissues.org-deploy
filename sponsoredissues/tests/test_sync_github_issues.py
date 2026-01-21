from django.test import TestCase
from django.utils import timezone
from unittest.mock import Mock, patch
from io import StringIO
import time

from sponsoredissues.github_app import GitHubAppInstallationClass
from sponsoredissues.management.commands.sync_github_issues import Command
from sponsoredissues.models import GitHubAppInstallation, GitHubRepo, GitHubIssue, SponsorAmount
from django.contrib.auth.models import User


class SyncInstallationReposTest(TestCase):
    """Tests for the _sync_installation_repos method."""

    def setUp(self):
        """Set up test fixtures."""
        self.command = Command()
        self.command.stdout = StringIO()

        # Mock installation data
        installation_url = 'https://github.com/installation/1234'
        self.installation_json = {
            'id': 12345,
            'account': {
                'login': 'testuser',
                'html_url': 'https://github.com/testuser'
            },
            'html_url': installation_url
        }
        self.installation = GitHubAppInstallation.objects.create(url=installation_url)

    @patch.object(Command, '_query_installation_repos')
    def test_add_new_public_repo(self, mock_query_repos):
        """Test adding a new public repository."""
        # Mock the API response with one new public repo
        mock_query_repos.return_value = [
            {
                'name': 'test-repo',
                'html_url': 'https://github.com/testuser/test-repo',
                'private': False
            }
        ]

        # Call the method
        stats = self.command._sync_installation_repos(
            self.installation_json,
            dry_run=False
        )

        # Verify the repo was created in the database
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.first()
        self.assertEqual(repo.url, 'https://github.com/testuser/test-repo')

        # Verify correct counts
        self.assertEqual(stats.added, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.removed, 0)

    @patch.object(Command, '_query_installation_repos')
    def test_update_existing_repo(self, mock_query_repos):
        """Test updating an existing repository's timestamp."""
        # Create an existing repo in the database
        repo_url = 'https://github.com/testuser/existing-repo'
        existing_repo = GitHubRepo.objects.create(url=repo_url, app_installation=self.installation)
        original_updated_at = existing_repo.updated_at

        # Wait a moment to ensure timestamp will be different
        time.sleep(0.01)

        # Mock the API response with the same repo
        mock_query_repos.return_value = [
            {
                'name': 'existing-repo',
                'html_url': repo_url,
                'private': False
            }
        ]

        # Call the method
        stats = self.command._sync_installation_repos(
            self.installation_json,
            dry_run=False
        )

        # Verify repo still exists and was updated
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.get(url=repo_url)
        self.assertGreater(repo.updated_at, original_updated_at)

        # Verify correct counts
        self.assertEqual(stats.added, 0)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.removed, 0)

    @patch.object(Command, '_query_installation_repos')
    def test_remove_repo_no_longer_accessible(self, mock_query_repos):
        """Test removing a repository that is no longer accessible."""
        # Create an existing repo in the database
        repo_url = 'https://github.com/testuser/removed-repo'
        GitHubRepo.objects.create(url=repo_url, app_installation=self.installation)

        # Mock the API response with empty list (no repos accessible)
        mock_query_repos.return_value = []

        # Call the method
        stats = self.command._sync_installation_repos(
            self.installation_json,
            dry_run=False
        )

        # Verify repo was deleted from database
        self.assertEqual(GitHubRepo.objects.count(), 0)
        self.assertFalse(GitHubRepo.objects.filter(url=repo_url).exists())

        # Verify correct counts
        self.assertEqual(stats.added, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.removed, 1)

    @patch.object(Command, '_query_installation_repos')
    def test_skip_private_repos(self, mock_query_repos):
        """Test that private repositories are skipped and not added to database."""
        # Mock the API response with one private repo
        mock_query_repos.return_value = [
            {
                'name': 'private-repo',
                'html_url': 'https://github.com/testuser/private-repo',
                'private': True
            }
        ]

        # Call the method
        stats = self.command._sync_installation_repos(
            self.installation_json,
            dry_run=False
        )

        # Verify no repo was created
        self.assertEqual(GitHubRepo.objects.count(), 0)

        # Verify correct counts (private repo should not be counted)
        self.assertEqual(stats.added, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.removed, 0)


class SyncInstallationIssuesTest(TestCase):
    """Tests for the _sync_installation_issues method."""

    def setUp(self):
        """Set up test fixtures."""
        self.command = Command()
        self.command.stdout = StringIO()

        # Mock installation data
        installation_url = 'https:://github.com/installation/1234'
        self.installation_json = {
            'id': 12345,
            'account': {
                'login': 'testuser',
                'html_url': 'https://github.com/testuser'
            },
            'html_url': installation_url
        }
        self.installation = GitHubAppInstallation.objects.create(
            url=installation_url
        )

        # Create a repo for the issues to belong to
        self.repo = GitHubRepo.objects.create(
            url='https://github.com/testuser/test-repo',
            app_installation=self.installation,
        )

    @patch.object(Command, '_query_installation_issues')
    def test_add_new_issue_with_label(self, mock_query_issues):
        """Test adding a new issue with sponsoredissues.org label."""
        # Mock the API response with one new issue
        issue_data = {
            'number': 1,
            'title': 'Test Issue',
            'body': 'Test body',
            'state': 'open',
            'url': 'https://github.com/testuser/test-repo/issues/1',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [
                {'name': 'sponsoredissues.org', 'color': '000000'}
            ],
            'user': {'login': 'issueauthor'}
        }
        mock_query_issues.return_value = [issue_data]

        # Call the method
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=False
            )

        # Verify the issue was created in the database
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.first()
        self.assertEqual(issue.url, 'https://github.com/testuser/test-repo/issues/1')
        self.assertEqual(issue.data['title'], 'Test Issue')
        self.assertEqual(issue.repo, self.repo)

        # Verify correct counts
        self.assertEqual(stats.added, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.removed, 0)

    @patch.object(Command, '_query_installation_issues')
    def test_update_existing_issue(self, mock_query_issues):
        """Test updating an existing issue's data."""
        # Create an existing issue in the database
        issue_url = 'https://github.com/testuser/test-repo/issues/2'
        original_data = {
            'number': 2,
            'title': 'Old Title',
            'body': 'Old body',
            'state': 'open',
            'url': issue_url,
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        existing_issue = GitHubIssue.objects.create(
            url=issue_url,
            data=original_data,
            repo=self.repo
        )
        original_updated_at = existing_issue.updated_at

        # Wait a moment to ensure timestamp will be different
        time.sleep(0.01)

        # Mock the API response with updated data
        updated_data = original_data.copy()
        updated_data['title'] = 'New Title'
        updated_data['body'] = 'New body'
        mock_query_issues.return_value = [updated_data]

        # Call the method
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=False
            )

        # Verify issue still exists and was updated
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.get(url=issue_url)
        self.assertEqual(issue.data['title'], 'New Title')
        self.assertEqual(issue.data['body'], 'New body')
        self.assertGreater(issue.updated_at, original_updated_at)

        # Verify correct counts
        self.assertEqual(stats.added, 0)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.removed, 0)

    @patch.object(Command, '_query_installation_issues')
    def test_remove_unfunded_issue_when_label_removed(self, mock_query_issues):
        """Test removing an unfunded issue when sponsoredissues.org label is removed."""
        # Create an existing unfunded issue in the database
        issue_url = 'https://github.com/testuser/test-repo/issues/3'
        issue_data = {
            'number': 3,
            'title': 'Test Issue',
            'body': 'Test body',
            'state': 'open',
            'url': issue_url,
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        GitHubIssue.objects.create(
            url=issue_url,
            data=issue_data,
            repo=self.repo
        )

        # Mock the API response - issue now without the sponsoredissues.org label
        updated_data = issue_data.copy()
        updated_data['labels'] = [{'name': 'bug', 'color': 'ff0000'}]
        mock_query_issues.return_value = [updated_data]

        # Call the method
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=False
            )

        # Verify issue was deleted from database
        self.assertEqual(GitHubIssue.objects.count(), 0)
        self.assertFalse(GitHubIssue.objects.filter(url=issue_url).exists())

        # Verify correct counts
        self.assertEqual(stats.added, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.removed, 1)

    @patch.object(Command, '_query_installation_issues')
    def test_issue_assigned_to_correct_repo(self, mock_query_issues):
        """Test that issues are correctly assigned to their parent repository."""
        # Create a second repo
        repo2 = GitHubRepo.objects.create(
            url='https://github.com/testuser/another-repo',
            app_installation=self.installation
        )

        # Mock API response with issues from different repos
        issue1_data = {
            'number': 1,
            'title': 'Issue in test-repo',
            'body': 'Test body',
            'state': 'open',
            'url': 'https://github.com/testuser/test-repo/issues/1',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        issue2_data = {
            'number': 2,
            'title': 'Issue in another-repo',
            'body': 'Test body',
            'state': 'open',
            'url': 'https://github.com/testuser/another-repo/issues/2',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        mock_query_issues.return_value = [issue1_data, issue2_data]

        # Call the method
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=False
            )

        # Verify both issues were created with correct repo assignments
        self.assertEqual(GitHubIssue.objects.count(), 2)
        issue1 = GitHubIssue.objects.get(url='https://github.com/testuser/test-repo/issues/1')
        issue2 = GitHubIssue.objects.get(url='https://github.com/testuser/another-repo/issues/2')
        self.assertEqual(issue1.repo, self.repo)
        self.assertEqual(issue2.repo, repo2)

        # Verify correct counts
        self.assertEqual(stats.added, 2)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.removed, 0)

    @patch.object(Command, '_query_installation_issues')
    def test_mixed_add_update_remove_operations(self, mock_query_issues):
        """Test mixed operations: add new issue, update existing, remove old."""
        # Create two existing issues
        existing_issue1_url = 'https://github.com/testuser/test-repo/issues/1'
        existing_issue1_data = {
            'number': 1,
            'title': 'Existing Issue 1',
            'body': 'Old body',
            'state': 'open',
            'url': existing_issue1_url,
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        GitHubIssue.objects.create(
            url=existing_issue1_url,
            data=existing_issue1_data,
            repo=self.repo
        )

        removed_issue_url = 'https://github.com/testuser/test-repo/issues/2'
        removed_issue_data = {
            'number': 2,
            'title': 'To Be Removed',
            'body': 'Will be removed',
            'state': 'open',
            'url': removed_issue_url,
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        GitHubIssue.objects.create(
            url=removed_issue_url,
            data=removed_issue_data,
            repo=self.repo
        )

        # Mock API response: update issue 1, add issue 3, remove issue 2
        updated_issue1_data = existing_issue1_data.copy()
        updated_issue1_data['title'] = 'Updated Issue 1'
        new_issue3_data = {
            'number': 3,
            'title': 'New Issue 3',
            'body': 'New body',
            'state': 'open',
            'url': 'https://github.com/testuser/test-repo/issues/3',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        mock_query_issues.return_value = [updated_issue1_data, new_issue3_data]

        # Call the method
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=False
            )

        # Verify operations
        self.assertEqual(GitHubIssue.objects.count(), 2)  # issue1 and issue3

        # Issue 1 should be updated
        issue1 = GitHubIssue.objects.get(url=existing_issue1_url)
        self.assertEqual(issue1.data['title'], 'Updated Issue 1')

        # Issue 3 should be added
        self.assertTrue(GitHubIssue.objects.filter(url='https://github.com/testuser/test-repo/issues/3').exists())

        # Issue 2 should be removed
        self.assertFalse(GitHubIssue.objects.filter(url=removed_issue_url).exists())

        # Verify correct counts
        self.assertEqual(stats.added, 1)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.removed, 1)

    @patch.object(Command, '_query_installation_issues')
    def test_dry_run_mode_makes_no_changes(self, mock_query_issues):
        """Test that dry run mode doesn't make any database changes."""
        # Create an existing issue
        existing_issue_url = 'https://github.com/testuser/test-repo/issues/1'
        existing_issue_data = {
            'number': 1,
            'title': 'Old Title',
            'body': 'Old body',
            'state': 'open',
            'url': existing_issue_url,
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        GitHubIssue.objects.create(
            url=existing_issue_url,
            data=existing_issue_data,
            repo=self.repo
        )

        # Mock API response: update existing, add new, remove existing
        updated_issue_data = existing_issue_data.copy()
        updated_issue_data['title'] = 'New Title'
        new_issue_data = {
            'number': 2,
            'title': 'New Issue',
            'body': 'New body',
            'state': 'open',
            'url': 'https://github.com/testuser/test-repo/issues/2',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        mock_query_issues.return_value = [updated_issue_data, new_issue_data]

        # Call the method in DRY RUN mode
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=True
            )

        # Verify NO database changes occurred
        self.assertEqual(GitHubIssue.objects.count(), 1)  # Still just the original issue

        # Original issue should be unchanged
        issue = GitHubIssue.objects.get(url=existing_issue_url)
        self.assertEqual(issue.data['title'], 'Old Title')  # Not updated

        # New issue should NOT exist
        self.assertFalse(GitHubIssue.objects.filter(url='https://github.com/testuser/test-repo/issues/2').exists())

        # But counts should reflect what WOULD have happened
        self.assertEqual(stats.added, 1)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.removed, 0)

    @patch.object(Command, '_query_installation_issues')
    def test_issue_state_change_open_to_closed(self, mock_query_issues):
        """Test that issue state changes (open to closed) are properly updated."""
        # Create an existing open issue
        issue_url = 'https://github.com/testuser/test-repo/issues/1'
        issue_data = {
            'number': 1,
            'title': 'Test Issue',
            'body': 'Test body',
            'state': 'open',
            'url': issue_url,
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
            'user': {'login': 'issueauthor'}
        }
        GitHubIssue.objects.create(
            url=issue_url,
            data=issue_data,
            repo=self.repo
        )

        # Mock API response with the same issue but now closed
        closed_issue_data = issue_data.copy()
        closed_issue_data['state'] = 'closed'
        closed_issue_data['updated_at'] = '2024-01-02T00:00:00Z'
        mock_query_issues.return_value = [closed_issue_data]

        # Call the method
        with patch.object(GitHubAppInstallationClass, 'get_access_token', return_value='fake-token'):
            stats = self.command._sync_installation_issues(
                self.installation_json,
                dry_run=False
            )

        # Verify issue still exists (not deleted)
        self.assertEqual(GitHubIssue.objects.count(), 1)
        issue = GitHubIssue.objects.get(url=issue_url)

        # Verify state was updated to closed
        self.assertEqual(issue.data['state'], 'closed')

        # Verify correct counts (updated, not removed)
        self.assertEqual(stats.added, 0)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.removed, 0)


class SuspendedInstallationTest(TestCase):
    """Tests for handling suspended GitHub App installations."""

    def setUp(self):
        """Set up test fixtures."""
        self.command = Command()
        self.command.stdout = StringIO()

        # Create test user for funded issues
        self.user = User.objects.create_user(username='testuser', email='test@example.com')

    @patch.object(Command, '_sync_installation_repos')
    @patch.object(Command, '_sync_installation_issues')
    def test_suspended_installation_removes_unfunded_content(self, mock_sync_issues, mock_sync_repos):
        """Test that suspended installations remove repos and unfunded issues."""
        # Mock installation with suspended_at field
        suspended_installation_url = 'https://github.com/suspended-installation'
        suspended_installation_json = {
            'id': 99999,
            'account': {
                'login': 'suspended-user',
                'html_url': 'https://github.com/suspended-user'
            },
            'html_url': suspended_installation_url,
            'suspended_at': '2024-01-01T00:00:00Z'
        }
        suspended_installation = GitHubAppInstallation.objects.create(url=suspended_installation_url)

        # Create repos and issues for the suspended account
        repo1 = GitHubRepo.objects.create(
            url='https://github.com/suspended-user/repo1',
            app_installation=suspended_installation,
        )
        repo2 = GitHubRepo.objects.create(
            url='https://github.com/suspended-user/repo2',
            app_installation=suspended_installation,
        )

        # Create an unfunded issue
        unfunded_issue_data = {
            'number': 1,
            'title': 'Unfunded Issue',
            'state': 'open',
            'url': 'https://github.com/suspended-user/repo1/issues/1',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
        }
        unfunded_issue = GitHubIssue.objects.create(
            url='https://github.com/suspended-user/repo1/issues/1',
            data=unfunded_issue_data,
            repo=repo1
        )

        # Create a funded issue (should be kept)
        funded_issue_data = {
            'number': 2,
            'title': 'Funded Issue',
            'state': 'open',
            'url': 'https://github.com/suspended-user/repo1/issues/2',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/suspended-user/repo1/issues/2',
            data=funded_issue_data,
            repo=repo1
        )
        # Add funding to the issue
        SponsorAmount.objects.create(
            cents_usd=1000,
            sponsor_user=self.user,
            target_github_issue=funded_issue
        )

        with patch.object(GitHubAppInstallationClass, 'get_access_token') as mock_token:
            mock_token.return_value = 'fake-token'
            with patch.object(self.command.github_app, 'query_installations') as mock_installations:
                mock_installations.return_value = [
                    GitHubAppInstallationClass.from_json(suspended_installation_json)
                ]
                # Call _sync_installations
                self.command._sync_installations({'dry_run': False})

        # Verify suspended installation was removed
        self.assertFalse(GitHubAppInstallation.objects.filter(url=suspended_installation_url).exists())

        # Verify repos were removed
        self.assertEqual(GitHubRepo.objects.filter(url__startswith='https://github.com/suspended-user/').count(), 0)

        # Verify unfunded issue was removed
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue.url).exists())

        # Verify funded issue was kept (has non-null repo reference initially, but repo was deleted)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())
        remaining_issue = GitHubIssue.objects.get(url=funded_issue.url)
        self.assertIsNone(remaining_issue.repo)  # Repo should be null due to SET_NULL

        # Verify _sync_installation_repos and _sync_installation_issues were NOT called for suspended installation
        mock_sync_repos.assert_not_called()
        mock_sync_issues.assert_not_called()

    @patch.object(Command, '_sync_installation_repos')
    @patch.object(Command, '_sync_installation_issues')
    def test_mix_of_suspended_and_active_installations(self, mock_sync_issues, mock_sync_repos):
        """Test that mix of suspended and active installations are handled correctly."""
        # Mock installations: one suspended, one active
        suspended_installation_url = 'https://github.com/suspended-installation'
        suspended_installation_json = {
            'id': 11111,
            'account': {
                'login': 'suspended-user',
                'html_url': 'https://github.com/suspended-user'
            },
            'html_url': suspended_installation_url,
            'suspended_at': '2024-01-01T00:00:00Z'
        }
        suspended_installation = GitHubAppInstallation.objects.create(url=suspended_installation_url)

        active_installation_url = 'https://github.com/active-installation'
        active_installation_json = {
            'id': 22222,
            'account': {
                'login': 'active-user',
                'html_url': 'https://github.com/active-user'
            },
            'html_url': active_installation_url
        }
        active_installation = GitHubAppInstallation.objects.create(url=active_installation_url)

        # Create content for suspended account
        suspended_repo = GitHubRepo.objects.create(
            url='https://github.com/suspended-user/repo1',
            app_installation=suspended_installation)

        suspended_issue_data = {
            'number': 1,
            'title': 'Issue in suspended repo',
            'state': 'open',
            'url': 'https://github.com/suspended-user/repo1/issues/1',
        }
        GitHubIssue.objects.create(
            url='https://github.com/suspended-user/repo1/issues/1',
            data=suspended_issue_data,
            repo=suspended_repo
        )

        # Create content for active account
        active_repo = GitHubRepo.objects.create(
            url='https://github.com/active-user/repo1',
            app_installation=active_installation)

        # Mock sync methods to return counts
        mock_sync_repos.return_value = (0, 1, 0)  # added, updated, removed
        mock_sync_issues.return_value = (0, 0, 0)

        with patch.object(GitHubAppInstallationClass, 'get_access_token') as mock_token:
            mock_token.return_value = 'fake-token'
            with patch.object(self.command.github_app, 'query_installations') as mock_installations:
                mock_installations.return_value = [
                    GitHubAppInstallationClass.from_json(suspended_installation_json),
                    GitHubAppInstallationClass.from_json(active_installation_json),
                ]
                # Call _sync_installations
                self.command._sync_installations({'dry_run': False})

        # Verify suspended installation was removed
        self.assertFalse(GitHubAppInstallation.objects.filter(url=suspended_installation_url).exists())

        # Verify repo and (unfunded) issue from suspended installation were removed
        self.assertFalse(GitHubRepo.objects.filter(url__startswith='https://github.com/suspended-user/').exists())
        self.assertFalse(GitHubIssue.objects.filter(url__startswith='https://github.com/suspended-user/').exists())

        # Verify active installation was not removed
        self.assertTrue(GitHubAppInstallation.objects.filter(url=active_installation_url).exists())

        # Verify repo from active installation was not removed
        self.assertTrue(GitHubRepo.objects.filter(url__startswith='https://github.com/active-user/').exists())

        # Verify _sync_installation_repos and _sync_installation_issues were called ONLY for active installation
        self.assertEqual(mock_sync_repos.call_count, 1)
        self.assertEqual(mock_sync_issues.call_count, 1)
