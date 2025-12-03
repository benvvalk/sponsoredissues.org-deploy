from django.test import TestCase
from django.utils import timezone
from unittest.mock import Mock, patch
from io import StringIO
import time

from sponsoredissues.management.commands.sync_github_issues import Command
from sponsoredissues.models import GitHubRepo


class SyncInstallationReposTest(TestCase):
    """Tests for the _sync_installation_repos method."""

    def setUp(self):
        """Set up test fixtures."""
        self.command = Command()
        self.command.stdout = StringIO()

        # Mock installation data
        self.installation = {
            'id': 12345,
            'account': {
                'login': 'testuser',
                'html_url': 'https://github.com/testuser'
            }
        }

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

        # Mock getting the access token
        with patch.object(self.command.github_app_auth, 'get_installation_access_token') as mock_token:
            mock_token.return_value = 'fake-token'

            # Call the method
            added, updated, removed = self.command._sync_installation_repos(
                self.installation,
                dry_run=False
            )

        # Verify the repo was created in the database
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.first()
        self.assertEqual(repo.url, 'https://github.com/testuser/test-repo')

        # Verify correct counts
        self.assertEqual(added, 1)
        self.assertEqual(updated, 0)
        self.assertEqual(removed, 0)

    @patch.object(Command, '_query_installation_repos')
    def test_update_existing_repo(self, mock_query_repos):
        """Test updating an existing repository's timestamp."""
        # Create an existing repo in the database
        repo_url = 'https://github.com/testuser/existing-repo'
        existing_repo = GitHubRepo.objects.create(url=repo_url)
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

        # Mock getting the access token
        with patch.object(self.command.github_app_auth, 'get_installation_access_token') as mock_token:
            mock_token.return_value = 'fake-token'

            # Call the method
            added, updated, removed = self.command._sync_installation_repos(
                self.installation,
                dry_run=False
            )

        # Verify repo still exists and was updated
        self.assertEqual(GitHubRepo.objects.count(), 1)
        repo = GitHubRepo.objects.get(url=repo_url)
        self.assertGreater(repo.updated_at, original_updated_at)

        # Verify correct counts
        self.assertEqual(added, 0)
        self.assertEqual(updated, 1)
        self.assertEqual(removed, 0)

    @patch.object(Command, '_query_installation_repos')
    def test_remove_repo_no_longer_accessible(self, mock_query_repos):
        """Test removing a repository that is no longer accessible."""
        # Create an existing repo in the database
        repo_url = 'https://github.com/testuser/removed-repo'
        GitHubRepo.objects.create(url=repo_url)

        # Mock the API response with empty list (no repos accessible)
        mock_query_repos.return_value = []

        # Mock getting the access token
        with patch.object(self.command.github_app_auth, 'get_installation_access_token') as mock_token:
            mock_token.return_value = 'fake-token'

            # Call the method
            added, updated, removed = self.command._sync_installation_repos(
                self.installation,
                dry_run=False
            )

        # Verify repo was deleted from database
        self.assertEqual(GitHubRepo.objects.count(), 0)
        self.assertFalse(GitHubRepo.objects.filter(url=repo_url).exists())

        # Verify correct counts
        self.assertEqual(added, 0)
        self.assertEqual(updated, 0)
        self.assertEqual(removed, 1)

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

        # Mock getting the access token
        with patch.object(self.command.github_app_auth, 'get_installation_access_token') as mock_token:
            mock_token.return_value = 'fake-token'

            # Call the method
            added, updated, removed = self.command._sync_installation_repos(
                self.installation,
                dry_run=False
            )

        # Verify no repo was created
        self.assertEqual(GitHubRepo.objects.count(), 0)

        # Verify correct counts (private repo should not be counted)
        self.assertEqual(added, 0)
        self.assertEqual(updated, 0)
        self.assertEqual(removed, 0)
