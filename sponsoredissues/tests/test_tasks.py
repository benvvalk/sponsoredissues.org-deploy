import uuid

from unittest.mock import patch
from django.test import TestCase, override_settings
from celery.exceptions import SoftTimeLimitExceeded
from redis.exceptions import LockError, LockNotOwnedError
from typing import Any

from sponsoredissues.tasks import (
    task_sync_github_app_installation,
    task_app_installation_lock_acquire,
    TASK_WAIT_RETRY_TIME
)
from sponsoredissues.models import GitHubAppInstallation, Maintainer

class MockRedisLock:
    """Mock Redis lock for testing Celery tasks without a Redis server."""

    def __init__(self, mock_redis_db, name, timeout=None, blocking=True):
        self.mock_redis_db = mock_redis_db
        self.name = name
        self.timeout = timeout
        self.blocking = blocking
        self._uuid = uuid.uuid4()

    def acquire(self, blocking=None):
        """Simulate lock acquisition."""
        if blocking is not None:
            self.blocking = blocking

        if not self.locked():
            self.mock_redis_db[self.name] = self._uuid
            return True
        elif self.owned():
            raise LockError("tried to acquire lock we already own")
        else:
            if self.blocking:
                raise RuntimeError("blocked waiting for lock")
            return False

    def locked(self):
        return self.mock_redis_db.get(self.name) is not None

    def owned(self):
        return self.mock_redis_db.get(self.name) == self._uuid

    def release(self):
        """Simulate lock release."""
        if not self.locked():
            raise LockError("tried to release a lock that nobody owns")
        elif not self.owned():
            raise LockNotOwnedError()
        del self.mock_redis_db[self.name]

class MockRedisClient:
    def __init__(self):
        self.mock_redis_db: dict[str, Any] = {}

    def lock(self, name: str, timeout=None, blocking=True):
        return MockRedisLock(self.mock_redis_db, name, timeout, blocking)

class TaskLockAcquireContextManagerTest(TestCase):
    """Test the task_app_installation_lock_acquire context manager directly."""

    def setUp(self):
        self.mock_redis_client = MockRedisClient()
        self.lock_url = 'https://example.com'

    @patch('sponsoredissues.tasks.task_sleep_after_unexpected_exception')
    def test_context_manager_acquires_and_releases_lock(self, mock_sleep):
        """Test normal lock acquire and release flow."""
        mock_lock = self.mock_redis_client.lock(f'lock:{self.lock_url}')

        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            with task_app_installation_lock_acquire(f'{self.lock_url}') as acquired:
                self.assertTrue(acquired)
                self.assertTrue(mock_lock.locked())

        # Lock should be released after exiting context
        self.assertFalse(mock_lock.locked())

    def test_context_manager_handles_failed_acquisition(self):
        """Test behavior when lock cannot be acquired."""
        mock_lock = self.mock_redis_client.lock(f'lock:{self.lock_url}')
        mock_lock.acquire()

        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            with task_app_installation_lock_acquire(f'{self.lock_url}', blocking=False) as acquired:
                self.assertFalse(acquired)

    @patch('sponsoredissues.tasks.task_sleep_after_unexpected_exception')
    def test_context_manager_releases_lock_on_exception(self, mock_sleep):
        """Test that lock is released even when exception occurs."""
        mock_lock = self.mock_redis_client.lock(f'lock:{self.lock_url}')

        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            with task_app_installation_lock_acquire(f'{self.lock_url}') as acquired:
                self.assertTrue(acquired)
                # Simulate error during protected operation.
                # This exception should be caught and logged by
                # `task_app_installation_lock_acquire`, after which
                # the lock should be released.
                raise RuntimeError("Simulated error")

        # Lock should be released despite exception
        self.assertFalse(mock_lock.locked())

        # Sleep should be called after exception
        mock_sleep.assert_called_once()

class TaskIntegrationWithEagerModeTest(TestCase):
    """Test tasks using Celery's eager mode (synchronous execution)."""

    def setUp(self):
        """Set up test fixtures."""
        self.maintainer = Maintainer.objects.create(
            github_account_id=1,
            github_user_json='{}',
            github_sponsors_profile_url='https://github.com/sponsors/maintainer'
        )
        self.installation_id = 12345
        self.mock_redis_client = MockRedisClient()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('sponsoredissues.tasks.github_sync_app_installation')
    def test_task_in_eager_mode(self, mock_sync):
        """Test task execution in eager mode (no broker required)."""
        # Call task - it executes immediately
        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            result = task_sync_github_app_installation.apply_async(args=[self.installation_id])

        # In eager mode, task runs synchronously
        self.assertIsNotNone(result)

        # Verify sync was called
        mock_sync.assert_called_once_with(self.installation_id)

class TaskAppInstallationSyncTest(TestCase):
    """Test normal task execution without Redis."""

    def setUp(self):
        """Set up test fixtures."""
        self.maintainer = Maintainer.objects.create(
            github_account_id=1,
            github_user_json='{}',
            github_sponsors_profile_url='https://github.com/sponsors/maintainer'
        )

        self.installation_id = 12345
        self.installation_url = f'https://github.com/settings/installations/{self.installation_id}'
        self.installation = GitHubAppInstallation.objects.create(
            url=self.installation_url,
            data=f'{{"id": {self.installation_id}}}',
            maintainer=self.maintainer
        )

        self.mock_redis_client = MockRedisClient()

    @patch('sponsoredissues.tasks.github_sync_app_installation')
    def test_task_executes_when_lock_acquired(self, mock_sync):
        """Test that task executes normally when lock is acquired."""
        # Execute task synchronously (eager mode)
        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            with self.settings(CELERY_TASK_ALWAYS_EAGER=True):
                task_sync_github_app_installation(self.installation_id)

        # Verify sync function was called
        mock_sync.assert_called_once_with(self.installation_id)

    @patch('sponsoredissues.tasks.github_sync_app_installation')
    def test_task_retries_when_lock_not_acquired(self, mock_sync):
        """Test that task schedules retry when lock cannot be acquired."""
        # Setup mock lock that fails to acquire
        mock_lock_from_other_task = self.mock_redis_client.lock(f'lock:{self.installation_url}')
        mock_lock_from_other_task.acquire()

        self.assertTrue(mock_lock_from_other_task.locked())

        # Mock the task's apply_async method
        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            with patch.object(task_sync_github_app_installation, 'apply_async') as mock_apply_async:
                # Execute task
                task_sync_github_app_installation(self.installation_id)

                # Verify sync was NOT called
                mock_sync.assert_not_called()

                # Verify retry was scheduled
                mock_apply_async.assert_called_once_with(countdown=TASK_WAIT_RETRY_TIME)

    @patch('sponsoredissues.tasks.task_sleep_after_unexpected_exception')
    @patch('sponsoredissues.tasks.github_sync_app_installation')
    def test_soft_timeout_releases_lock(self, mock_sync, mock_sleep):
        """Test that soft timeout exception releases the lock properly."""
        # Make sync function raise SoftTimeLimitExceeded
        mock_sync.side_effect = SoftTimeLimitExceeded()

        # Execute task - it should handle the exception
        with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
            with self.settings(CELERY_TASK_ALWAYS_EAGER=True):
                task_sync_github_app_installation(self.installation_id)

        # Verify sync was called (and raised exception)
        mock_sync.assert_called_once_with(self.installation_id)

        # Verify lock was released despite exception
        mock_lock = self.mock_redis_client.lock(f'lock:{self.installation_url}')
        self.assertFalse(mock_lock.locked())

        # Verify sleep function was called after exception
        mock_sleep.assert_called_once()

    @patch('sponsoredissues.tasks.task_sleep_after_unexpected_exception')
    @patch('sponsoredissues.tasks.github_sync_app_installation')
    def test_exception_releases_lock(self, mock_sync, mock_sleep):
        """Test that various exception types all release the lock."""
        exceptions_to_test = [
            RuntimeError("Connection error"),
            ValueError("Invalid data"),
            KeyError("Missing key"),
            Exception("Generic exception")
        ]

        for exception in exceptions_to_test:
            # Make sync raise exception
            mock_sync.side_effect = exception

            # Execute task
            with patch('sponsoredissues.tasks.redis_client', self.mock_redis_client):
                with self.settings(CELERY_TASK_ALWAYS_EAGER=True):
                    task_sync_github_app_installation(self.installation_id)

            # Verify lock was released
            mock_lock = self.mock_redis_client.lock(f'lock:{self.installation_url}')
            self.assertFalse(mock_lock.locked(),
                            f"Lock not released for {type(exception).__name__}")
