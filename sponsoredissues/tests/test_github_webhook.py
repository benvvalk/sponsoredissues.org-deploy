import requests
import sponsoredissues.views

from django.test import TestCase
from typing import Final
from unittest.mock import patch

class MockData:
    DEFAULT_USER_NAME: Final[str] = 'test-user'
    DEFAULT_INSTALLATION_ID: Final[int] = 1111

    @staticmethod
    def webhook_request_headers():
        return {
            'X-GitHub-Event': 'installation'
        }

    @staticmethod
    def webhook_request_payload(
            action: str,
            installation_id: int = DEFAULT_INSTALLATION_ID,
            user_name: str = DEFAULT_USER_NAME
    ):
        json = {
            'action': action,
            'installation': {
                'account': {
                    'login': f'{user_name}',
                    'html_url': f'https://github.com/{user_name}'
                },
                'id': installation_id,
                'html_url': f'https://github.com/settings/installations/{installation_id}',
            },

        }

        return json

    @staticmethod
    def webhook_request(
            action: str,
            installation_id: int = DEFAULT_INSTALLATION_ID,
            user_name: str = DEFAULT_USER_NAME,
    ):
        headers = MockData.webhook_request_headers()
        payload = MockData.webhook_request_payload(action, installation_id, user_name)
        request = requests.Request('POST', "https://example.com", json=payload, headers=headers)
        prepared_request = request.prepare()
        return prepared_request

class GitHubWebhookInstallationEventTest(TestCase):
    """
    Tests to verify that `sponsoredissues.views.github_webhook` calls
    the right methods in response to various webhook event types
    (e.g. notification of a new GitHub App installation -> start
    Celery task to sync the app installation to our database).

    Note: These tests do not test the actual GitHub syncing or
    database operations, only that the right operations are triggered.
    The GitHub syncing and database operations have their own
    dedicated tests in `test_models.py` and `test_github_sync.py`,
    respectively.
    """

    @patch('sponsoredissues.views.task_sync_github_app_installation')
    @patch('sponsoredissues.views._verify_webhook_signature')
    def test_installation_action_created(self, mock_verify_webhook_signature, mock_celery_task):
        mock_verify_webhook_signature.return_value = True

        installation_id = 123
        request = MockData.webhook_request('created', installation_id=installation_id)
        response = sponsoredissues.views.github_webhook(request)

        # Verify the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify the background sync task was started with correct installation_id
        mock_celery_task.delay.assert_called_once_with(installation_id)

    @patch('sponsoredissues.views.task_sync_github_app_installation')
    @patch('sponsoredissues.views._verify_webhook_signature')
    def test_installation_action_unsuspend(self, mock_verify_webhook_signature, mock_celery_task):
        mock_verify_webhook_signature.return_value = True

        installation_id = 123
        request = MockData.webhook_request('unsuspend', installation_id=installation_id)
        response = sponsoredissues.views.github_webhook(request)

        # Verify the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify the background sync task was started with correct installation_id
        mock_celery_task.delay.assert_called_once_with(installation_id)

    @patch('sponsoredissues.views.GitHubAppInstallation')
    @patch('sponsoredissues.views._verify_webhook_signature')
    def test_installation_action_deleted(self, mock_verify_webhook_signature, mock_installation_model):
        mock_verify_webhook_signature.return_value = True

        installation_id = 123
        user_name = 'test-user'
        request = MockData.webhook_request('deleted', installation_id=installation_id, user_name=user_name)
        response = sponsoredissues.views.github_webhook(request)

        # Verify the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify that we invoke `GitHubAppInstallation.delete()` with the correct URL
        expected_url = f'https://github.com/settings/installations/{installation_id}'
        mock_installation_model.objects.filter.assert_called_once_with(url=expected_url)
        mock_installation_model.objects.filter.return_value.first.return_value.delete.assert_called_once()

    @patch('sponsoredissues.views.GitHubAppInstallation')
    @patch('sponsoredissues.views._verify_webhook_signature')
    def test_installation_action_suspend(self, mock_verify_webhook_signature, mock_installation_model):
        mock_verify_webhook_signature.return_value = True

        installation_id = 123
        user_name = 'test-user'
        request = MockData.webhook_request('suspend', installation_id=installation_id, user_name=user_name)
        response = sponsoredissues.views.github_webhook(request)

        # Verify the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify that we invoke `GitHubAppInstallation.delete()` with the correct URL
        expected_url = f'https://github.com/settings/installations/{installation_id}'
        mock_installation_model.objects.filter.assert_called_once_with(url=expected_url)
        mock_installation_model.objects.filter.return_value.first.return_value.delete.assert_called_once()

    @patch('sponsoredissues.views._verify_webhook_signature')
    def test_installation_action_invalid(self, mock_verify_webhook_signature):
        mock_verify_webhook_signature.return_value = True

        installation_id = 123
        request = MockData.webhook_request('invalid', installation_id=installation_id)
        response = sponsoredissues.views.github_webhook(request)

        # an invalid action should still generate successful response,
        # even though it does nothing
        self.assertEqual(response.status_code, 200)