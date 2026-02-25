from django.test import TestCase
from django.contrib.auth.models import User

from sponsoredissues.models import GitHubAppInstallation, GitHubRepo, GitHubIssue, IssueSponsorship


class GitHubAppInstallationDeleteTest(TestCase):
    """Tests for the GitHubAppInstallation_delete signal handler."""

    def setUp(self):
        """Set up test fixtures."""
        # Create test user for funded issues
        self.user = User.objects.create_user(username='testuser', email='test@example.com')

        # Create test installation
        self.installation = GitHubAppInstallation.objects.create(
            url='https://github.com/installation/12345',
            data='{}'
        )

        # Create test repos
        self.repo1 = GitHubRepo.objects.create(
            url='https://github.com/testuser/repo1',
            app_installation=self.installation
        )
        self.repo2 = GitHubRepo.objects.create(
            url='https://github.com/testuser/repo2',
            app_installation=self.installation
        )

    def test_delete_removes_unfunded_issues_on_instance_delete(self):
        """Test that unfunded issues are deleted when calling delete() on an installation instance."""
        # Create an unfunded issue
        unfunded_issue_data = {
            'number': 1,
            'title': 'Unfunded Issue',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/1',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
        }
        unfunded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/1',
            data=unfunded_issue_data,
            repo=self.repo1
        )

        # Create a funded issue
        funded_issue_data = {
            'number': 2,
            'title': 'Funded Issue',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/2',
            'labels': [{'name': 'sponsoredissues.org', 'color': '000000'}],
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/2',
            data=funded_issue_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # Verify initial state
        self.assertEqual(GitHubIssue.objects.count(), 2)

        # Delete the installation instance
        self.installation.delete()

        # Verify unfunded issue was deleted
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue.url).exists())

        # Verify funded issue still exists (but with null repo due to CASCADE)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())
        remaining_issue = GitHubIssue.objects.get(url=funded_issue.url)
        self.assertIsNone(remaining_issue.repo)

        # Verify repos were deleted via CASCADE
        self.assertEqual(GitHubRepo.objects.count(), 0)

    def test_delete_removes_unfunded_issues_on_queryset_delete(self):
        """Test that unfunded issues are deleted when calling delete() on a QuerySet."""
        # Create a second installation
        installation2 = GitHubAppInstallation.objects.create(
            url='https://github.com/installation/67890',
            data='{}'
        )
        repo3 = GitHubRepo.objects.create(
            url='https://github.com/testuser/repo3',
            app_installation=installation2
        )

        # Create unfunded issues for both installations
        unfunded_issue1_data = {
            'number': 1,
            'title': 'Unfunded Issue 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/1',
        }
        unfunded_issue1 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/1',
            data=unfunded_issue1_data,
            repo=self.repo1
        )

        unfunded_issue2_data = {
            'number': 2,
            'title': 'Unfunded Issue 2',
            'state': 'open',
            'url': 'https://github.com/testuser/repo3/issues/2',
        }
        unfunded_issue2 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo3/issues/2',
            data=unfunded_issue2_data,
            repo=repo3
        )

        # Create funded issues for both installations
        funded_issue1_data = {
            'number': 3,
            'title': 'Funded Issue 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/3',
        }
        funded_issue1 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/3',
            data=funded_issue1_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            target_github_issue=funded_issue1
        )

        funded_issue2_data = {
            'number': 4,
            'title': 'Funded Issue 2',
            'state': 'open',
            'url': 'https://github.com/testuser/repo3/issues/4',
        }
        funded_issue2 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo3/issues/4',
            data=funded_issue2_data,
            repo=repo3
        )
        IssueSponsorship.objects.create(
            cents_usd=2000,
            sponsor=self.user,
            target_github_issue=funded_issue2
        )

        # Verify initial state
        self.assertEqual(GitHubAppInstallation.objects.count(), 2)
        self.assertEqual(GitHubIssue.objects.count(), 4)

        # Delete all installations using QuerySet.delete()
        GitHubAppInstallation.objects.all().delete()

        # Verify both unfunded issues were deleted
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue1.url).exists())
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue2.url).exists())

        # Verify both funded issues still exist (but with null repos)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue1.url).exists())
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue2.url).exists())
        self.assertEqual(GitHubIssue.objects.count(), 2)

        # Verify repos were deleted
        self.assertEqual(GitHubRepo.objects.count(), 0)

    def test_delete_with_multiple_unfunded_issues_per_repo(self):
        """Test that all unfunded issues are deleted when there are multiple per repo."""
        # Create multiple unfunded issues in the same repo
        for i in range(5):
            issue_data = {
                'number': i + 1,
                'title': f'Unfunded Issue {i + 1}',
                'state': 'open',
                'url': f'https://github.com/testuser/repo1/issues/{i + 1}',
            }
            GitHubIssue.objects.create(
                url=f'https://github.com/testuser/repo1/issues/{i + 1}',
                data=issue_data,
                repo=self.repo1
            )

        # Create one funded issue
        funded_issue_data = {
            'number': 99,
            'title': 'Funded Issue',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/99',
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/99',
            data=funded_issue_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=5000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # Verify initial state
        self.assertEqual(GitHubIssue.objects.count(), 6)

        # Delete the installation
        self.installation.delete()

        # Verify only the funded issue remains
        self.assertEqual(GitHubIssue.objects.count(), 1)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())

    def test_delete_with_no_issues(self):
        """Test that deleting an installation with no issues works correctly."""
        # Verify no issues exist
        self.assertEqual(GitHubIssue.objects.count(), 0)

        # Delete the installation (should not raise any errors)
        self.installation.delete()

        # Verify installation and repos were deleted
        self.assertFalse(GitHubAppInstallation.objects.filter(url=self.installation.url).exists())
        self.assertEqual(GitHubRepo.objects.count(), 0)

    def test_delete_with_only_funded_issues(self):
        """Test that deleting an installation with only funded issues preserves them."""
        # Create only funded issues
        for i in range(3):
            issue_data = {
                'number': i + 1,
                'title': f'Funded Issue {i + 1}',
                'state': 'open',
                'url': f'https://github.com/testuser/repo1/issues/{i + 1}',
            }
            issue = GitHubIssue.objects.create(
                url=f'https://github.com/testuser/repo1/issues/{i + 1}',
                data=issue_data,
                repo=self.repo1
            )
            IssueSponsorship.objects.create(
                cents_usd=1000 * (i + 1),
                sponsor=self.user,
                target_github_issue=issue
            )

        # Verify initial state
        self.assertEqual(GitHubIssue.objects.count(), 3)

        # Delete the installation
        self.installation.delete()

        # Verify all funded issues still exist
        self.assertEqual(GitHubIssue.objects.count(), 3)
        for i in range(3):
            self.assertTrue(
                GitHubIssue.objects.filter(
                    url=f'https://github.com/testuser/repo1/issues/{i + 1}'
                ).exists()
            )

    def test_delete_filters_by_correct_installation(self):
        """Test that the delete handler only deletes issues for the correct installation."""
        # Create a second installation with its own repos and issues
        installation2 = GitHubAppInstallation.objects.create(
            url='https://github.com/installation/67890',
            data='{}'
        )
        repo3 = GitHubRepo.objects.create(
            url='https://github.com/testuser/repo3',
            app_installation=installation2
        )

        # Create unfunded issues for both installations
        unfunded_issue1_data = {
            'number': 1,
            'title': 'Unfunded Issue in Installation 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/1',
        }
        unfunded_issue1 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/1',
            data=unfunded_issue1_data,
            repo=self.repo1
        )

        unfunded_issue2_data = {
            'number': 2,
            'title': 'Unfunded Issue in Installation 2',
            'state': 'open',
            'url': 'https://github.com/testuser/repo3/issues/2',
        }
        unfunded_issue2 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo3/issues/2',
            data=unfunded_issue2_data,
            repo=repo3
        )

        # Verify initial state
        self.assertEqual(GitHubIssue.objects.count(), 2)

        # Delete only the first installation
        self.installation.delete()

        # Verify only the issue from the first installation was deleted
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue1.url).exists())
        self.assertTrue(GitHubIssue.objects.filter(url=unfunded_issue2.url).exists())

        # Verify the second installation and its repo still exist
        self.assertTrue(GitHubAppInstallation.objects.filter(url=installation2.url).exists())
        self.assertTrue(GitHubRepo.objects.filter(url=repo3.url).exists())

    def test_delete_includes_handler_deletes_in_counts(self):
        """Test that the overridden delete() includes issues deleted by the delete handler."""
        # Create unfunded issues
        unfunded_issue1_data = {
            'number': 1,
            'title': 'Unfunded Issue 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/1',
        }
        GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/1',
            data=unfunded_issue1_data,
            repo=self.repo1
        )

        unfunded_issue2_data = {
            'number': 2,
            'title': 'Unfunded Issue 2',
            'state': 'open',
            'url': 'https://github.com/testuser/repo2/issues/2',
        }
        GitHubIssue.objects.create(
            url='https://github.com/testuser/repo2/issues/2',
            data=unfunded_issue2_data,
            repo=self.repo2
        )

        # Create a funded issue
        funded_issue_data = {
            'number': 3,
            'title': 'Funded Issue',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/3',
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/3',
            data=funded_issue_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # Verify initial state
        self.assertEqual(GitHubIssue.objects.count(), 3)
        self.assertEqual(GitHubRepo.objects.count(), 2)
        self.assertEqual(GitHubAppInstallation.objects.count(), 1)

        # Delete the installation and capture the return value
        total_deleted, deleted_by_model = self.installation.delete()

        # Verify the counts
        # Our overridden delete() method includes unfunded issues deleted by the delete handler:
        # - 1 GitHubAppInstallation (the object being deleted)
        # - 2 GitHubRepo (CASCADE from installation)
        # - 2 unfunded GitHubIssue objects (deleted by our delete handler)
        self.assertEqual(total_deleted, 5)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubAppInstallation'), 1)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubRepo'), 2)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubIssue'), 2)

        # Verify the unfunded issues were actually deleted (by the delete handler)
        self.assertEqual(GitHubIssue.objects.count(), 1)  # Only the funded issue remains
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())

    def test_delete_returns_unfunded_issue_count_instance_method(self):
        """Test that delete() on an instance includes unfunded issues in deleted_by_model dict."""
        # Create unfunded issues
        for i in range(3):
            issue_data = {
                'number': i + 1,
                'title': f'Unfunded Issue {i + 1}',
                'state': 'open',
                'url': f'https://github.com/testuser/repo1/issues/{i + 1}',
            }
            GitHubIssue.objects.create(
                url=f'https://github.com/testuser/repo1/issues/{i + 1}',
                data=issue_data,
                repo=self.repo1
            )

        # Create a funded issue
        funded_issue_data = {
            'number': 99,
            'title': 'Funded Issue',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/99',
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/99',
            data=funded_issue_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=5000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # Call delete() - it now includes unfunded issues in the return value
        total_deleted, deleted_by_model = self.installation.delete()

        # Verify the total count includes unfunded issues
        self.assertEqual(total_deleted, 6)  # 1 installation + 2 repos + 3 unfunded issues

        # Verify individual model counts
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubAppInstallation'), 1)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubRepo'), 2)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubIssue'), 3)

        # Verify only the funded issue remains
        self.assertEqual(GitHubIssue.objects.count(), 1)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())

    def test_delete_returns_unfunded_issue_count_queryset_method(self):
        """Test that delete() on a queryset includes unfunded issues in deleted_by_model dict."""
        # Create a second installation
        installation2 = GitHubAppInstallation.objects.create(
            url='https://github.com/installation/67890',
            data='{}'
        )
        repo3 = GitHubRepo.objects.create(
            url='https://github.com/testuser/repo3',
            app_installation=installation2
        )

        # Create unfunded issues for both installations
        unfunded_issue1_data = {
            'number': 1,
            'title': 'Unfunded Issue 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/1',
        }
        GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/1',
            data=unfunded_issue1_data,
            repo=self.repo1
        )

        unfunded_issue2_data = {
            'number': 2,
            'title': 'Unfunded Issue 2',
            'state': 'open',
            'url': 'https://github.com/testuser/repo3/issues/2',
        }
        GitHubIssue.objects.create(
            url='https://github.com/testuser/repo3/issues/2',
            data=unfunded_issue2_data,
            repo=repo3
        )

        # Create a funded issue
        funded_issue_data = {
            'number': 3,
            'title': 'Funded Issue',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/3',
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/3',
            data=funded_issue_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # Verify initial state
        self.assertEqual(GitHubAppInstallation.objects.count(), 2)
        self.assertEqual(GitHubIssue.objects.count(), 3)

        # Call delete() on queryset - it now includes unfunded issues in the return value
        total_deleted, deleted_by_model = GitHubAppInstallation.objects.all().delete()

        # Verify the total count includes unfunded issues
        self.assertEqual(total_deleted, 7)  # 2 installations + 3 repos + 2 unfunded issues

        # Verify individual model counts
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubAppInstallation'), 2)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubRepo'), 3)
        self.assertEqual(deleted_by_model.get('sponsoredissues.GitHubIssue'), 2)

        # Verify only the funded issue remains
        self.assertEqual(GitHubIssue.objects.count(), 1)
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())

    def test_delete_with_issues_across_multiple_repos(self):
        """Test that unfunded issues are deleted across all repos in an installation."""
        # Create unfunded issues in both repos
        unfunded_issue1_data = {
            'number': 1,
            'title': 'Unfunded Issue in Repo 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/1',
        }
        unfunded_issue1 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/1',
            data=unfunded_issue1_data,
            repo=self.repo1
        )

        unfunded_issue2_data = {
            'number': 2,
            'title': 'Unfunded Issue in Repo 2',
            'state': 'open',
            'url': 'https://github.com/testuser/repo2/issues/2',
        }
        unfunded_issue2 = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo2/issues/2',
            data=unfunded_issue2_data,
            repo=self.repo2
        )

        # Create a funded issue in one of the repos
        funded_issue_data = {
            'number': 3,
            'title': 'Funded Issue in Repo 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/3',
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/3',
            data=funded_issue_data,
            repo=self.repo1
        )
        IssueSponsorship.objects.create(
            cents_usd=3000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # Verify initial state
        self.assertEqual(GitHubIssue.objects.count(), 3)

        # Delete the installation
        self.installation.delete()

        # Verify both unfunded issues were deleted
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue1.url).exists())
        self.assertFalse(GitHubIssue.objects.filter(url=unfunded_issue2.url).exists())

        # Verify funded issue still exists
        self.assertTrue(GitHubIssue.objects.filter(url=funded_issue.url).exists())
        self.assertEqual(GitHubIssue.objects.count(), 1)

class GithubIssueTest(TestCase):
    def setUp(self):
        """Set up test fixtures."""
        # Create test user for funded issues
        self.user = User.objects.create_user(username='testuser', email='test@example.com')

        # Create test installation
        self.installation = GitHubAppInstallation.objects.create(
            url='https://github.com/installation/12345',
            data='{}'
        )

        # Create test repos
        self.repo = GitHubRepo.objects.create(
            url='https://github.com/testuser/repo1',
            app_installation=self.installation
        )

    def test_delete_force_with_funded_issue(self):
        # create funded issue
        funded_issue_data = {
            'number': 3,
            'title': 'Funded Issue 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/3',
        }
        funded_issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/3',
            data=funded_issue_data,
            repo=self.repo
        )
        IssueSponsorship.objects.create(
            cents_usd=1000,
            sponsor=self.user,
            target_github_issue=funded_issue
        )

        # call the method
        funded_issue.delete_force()

        # confirm issue was deleted
        self.assertEqual(GitHubIssue.objects.count(), 0)

    def test_delete_force_with_unfunded_issue(self):
        # create funded issue
        issue_data = {
            'number': 3,
            'title': 'Funded Issue 1',
            'state': 'open',
            'url': 'https://github.com/testuser/repo1/issues/3',
        }
        issue = GitHubIssue.objects.create(
            url='https://github.com/testuser/repo1/issues/3',
            data=issue_data,
            repo=self.repo
        )

        # call the method
        issue.delete_force()

        # confirm issue was deleted
        self.assertEqual(GitHubIssue.objects.count(), 0)