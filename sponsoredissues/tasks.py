import logging
import redis
import time

from contextlib import contextmanager
from celery import chord
from celery.utils.log import get_task_logger
from django.conf import settings
from sponsoredissues.celery import app
from sponsoredissues.github_api import github_api
from sponsoredissues.github_app import github_app_token
from sponsoredissues.github_sync import github_sync_app_installation, github_sync_app_installation_remove
from sponsoredissues.models import GitHubAppInstallation
from typing import Any

# Default task timeout in seconds.
#
# The soft timeouts handle the case where a task is stalled because
# the GitHub API is failing to respond (e.g. GitHub outage, network
# outage).
#
# In Celery terminology, a "soft" timeout triggers an exception,
# whereas a "hard" timeout will kill the worker process with a
# SIGTERM. Using a soft timeout allows us to catch the exception and
# release the Redis lock we are holding (if any).
TASK_SOFT_TIME_LIMIT = 60 * 5

# Default time to wait before retrying a task, in seconds.
#
# We want to retry a task after a delay, if:
# (1) There is no available work to do (e.g. no GitHub App
# installations in database)
# (2) Another task is holding the lock that we need
# (3) An unexpected error occurred (e.g. GitHub API outage)
TASK_WAIT_RETRY_TIME = 60 * 5

# Default timeout for app installation locks (Redis-based distributed
# locks).
#
# The lock timeout handles the case where the Celery worker processes
# crash while still holding on to their locks, which should
# (hopefully) be a rare occurrence.
TASK_LOCK_TIMEOUT = 60 * 60

redis_client = redis.Redis.from_url(url=settings.REDIS_URL, decode_responses=True)

logger = get_task_logger(__name__)

@app.task(bind=True, ignore_result=True)
def task_debug(self):
    logger.info(f'Request: {self.request!r}')

@contextmanager
def task_app_installation_lock_acquire(installation_url: str, **kwargs):
    lock_params: dict[str, Any] = {
        'timeout': TASK_LOCK_TIMEOUT
    }
    lock_params.update(kwargs)
    lock = redis_client.lock(name=f'lock:{installation_url}', **lock_params)

    acquired = lock.acquire()
    if not acquired:
        yield lock
        return

    # Lock successfully acquired

    exception = False
    try:
        # Note: The body of the `with` block is executed here,
        # and any exceptions that occur will be raised here.
        # See excellent explanation of control flow at:
        # https://docs.python.org/3/library/contextlib.html#contextlib.contextmanager
        yield lock
    except:
        logging.exception('unexpected exception during lock-protected operation')
        exception = True
    finally:
        lock.release()

    if exception:
        task_sleep_after_unexpected_exception()

@app.task(bind=True, ignore_result=True, soft_time_limit=TASK_SOFT_TIME_LIMIT)
def task_sync_github_app_installation(self, installation_id: int):
    installation_url = f'https://github.com/settings/installations/{installation_id}'
    with task_app_installation_lock_acquire(installation_url, blocking=False) as lock:
        if lock.owned():
            github_sync_app_installation(installation_id)
        else:
            logger.info(f'postponing sync of installation {installation_url}: failed to acquire lock (will retry in {TASK_WAIT_RETRY_TIME} seconds)')
            self.apply_async(countdown=TASK_WAIT_RETRY_TIME)

def task_sleep_after_unexpected_exception():
    seconds = 600
    logger.info(f'sleeping for {seconds} seconds before continuing')
    time.sleep(seconds)

@app.task(bind=True, ignore_result=True, soft_time_limit=TASK_SOFT_TIME_LIMIT)
def task_sync_github_app_installation_least_recently_updated(self):
    installations = GitHubAppInstallation.objects.all().order_by("updated_at")
    logger.info(f'database contains {installations.count()} app installations')

    did_work = False
    for installation in installations:
        with task_app_installation_lock_acquire(installation.url, blocking=False) as lock:
            if lock.owned():
                did_work = True
                github_sync_app_installation(installation.installation_id())
            else:
                logger.info(f'skipped sync of app installation {installation.url}: failed to acquire lock')

    # If there's no work to do (e.g. no app installations in the
    # database), prevent spinning by introducing a delay before the
    # next task iteration.
    if not did_work:
        logger.info(f'no work to do, delaying next task iteration by {TASK_WAIT_RETRY_TIME} seconds')
        self.apply_async(countdown=TASK_WAIT_RETRY_TIME)
    else:
        logger.info(f'scheduling next task iteration')
        self.apply_async()

@app.task(ignore_result=True)
def task_sync_github_app_installations_new_and_removed_callback():
    """
    Callback task that runs after all subtasks complete.  Schedules
    the next iteration of the
    `task_sync_github_app_installations_new_and_removed` task.
    """
    logger.info('all subtasks completed, starting next task iteration')
    task_sync_github_app_installations_new_and_removed.apply_async()

@app.task(bind=True, ignore_result=True, soft_time_limit=TASK_SOFT_TIME_LIMIT)
def task_sync_github_app_installations_new_and_removed(self):
    """
    Query the latest set of app installations from the GitHub API,
    sync any previously unknown installations to the database, and
    remove any installations from the database that no longer exist
    on GitHub.
    """
    app_token = github_app_token()

    installations_from_github_array = github_api('/app/installations', access_token=app_token)
    logger.info(f'found {len(installations_from_github_array)} app installations in total')
    installations_from_github = {
        installation['html_url']: installation for installation in installations_from_github_array
    }

    # Compare the app installation URLs in our database to the
    # installation URLs we retrieved from the GitHub API, to
    # identify which installations need to be added or removed.
    installation_urls_in_db = set(
        GitHubAppInstallation.objects.values_list('url', flat=True)
    )

    # Add app installations that don't yet exist in DB.
    installation_urls_to_add = installations_from_github.keys() - installation_urls_in_db
    logger.info(f'found {len(installation_urls_to_add)} new installations')

    # Collect all subtasks to wait for
    subtasks = []
    for installation_url in installation_urls_to_add:
        installation_json = installations_from_github[installation_url]
        installation_id = installation_json['id']
        # Create a signature for each subtask
        subtasks.append(task_sync_github_app_installation.s(installation_id))

    installation_urls_to_remove = installation_urls_in_db - installations_from_github.keys()
    logger.info(f'found {len(installation_urls_to_remove)} installations to remove')

    for installation_url in installation_urls_to_remove:
        with task_app_installation_lock_acquire(installation_url, blocking=False) as lock:
            if lock.owned():
                installation = GitHubAppInstallation.objects.get(url=installation_url)
                github_sync_app_installation_remove(installation)
            else:
                logger.info(f'skipped removing installation {installation_url}: failed to acquire lock')

    # Use chord to wait for all subtasks to complete before scheduling next iteration
    if subtasks:
        logger.info(f'scheduling {len(subtasks)} subtasks with callback')
        chord(subtasks)(task_sync_github_app_installations_new_and_removed_callback.s())
    else:
        logger.info(f'no work to do, scheduling next task iteration with a delay of {TASK_WAIT_RETRY_TIME} seconds')
        self.apply_async(countdown=TASK_WAIT_RETRY_TIME)