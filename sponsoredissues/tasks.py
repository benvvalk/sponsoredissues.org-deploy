import logging
import redis
import time

from contextlib import contextmanager
from celery.utils.log import get_task_logger
from django.conf import settings
from sponsoredissues.celery import app
from sponsoredissues.github_api import github_api
from sponsoredissues.github_app import github_app_token
from sponsoredissues.github_sync import github_sync_app_installation, github_sync_app_installation_remove
from sponsoredissues.models import GitHubAppInstallation

redis_client = redis.Redis.from_url(url=settings.REDIS_URL, decode_responses=True)

logger = get_task_logger(__name__)

@contextmanager
def task_app_installation_lock_acquire(installation_url: str, **kwargs):
    lock = redis_client.lock(name=f'lock:{installation_url}', **kwargs)

    acquired = lock.acquire()
    if not acquired:
        yield False
        return

    # Lock successfully acquired

    exception = False
    try:
        # Note: The body of the `with` block is executed here,
        # and any exceptions that occur will be raised here.
        # See excellent explanation of control flow at:
        # https://docs.python.org/3/library/contextlib.html#contextlib.contextmanager
        yield True
    except:
        logging.exception('unexpected exception during lock-protected operation')
        exception = True
    finally:
        lock.release()

    if exception:
        task_sleep_after_unexpected_exception()

@app.task(ignore_result=True)
def task_sync_github_app_installation(installation_id: int):
    installation_url = f'https://github.com/settings/installations/{installation_id}'
    with task_app_installation_lock_acquire(installation_url, timeout=300):
        github_sync_app_installation(installation_id)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    logger.info(f'Request: {self.request!r}')

def task_sleep_after_unexpected_exception():
    seconds = 600
    logger.info(f'sleeping for {seconds} seconds before continuing')
    time.sleep(seconds)

@app.task(bind=True, ignore_result=True)
def task_sync_github_app_installation_least_recently_updated(self):
    installations = GitHubAppInstallation.objects.all().order_by("updated_at")
    logger.info(f'database contains {installations.count()} app installations')

    for installation in installations:
        with task_app_installation_lock_acquire(installation.url, blocking=False, timeout=300) as acquired:
            if acquired:
                github_sync_app_installation(installation.installation_id())
            else:
                logger.info(f'skipped sync of app installation {installation.url}: failed to acquire lock')

    logger.info('starting next task iteration')
    self.apply_async()

@app.task(bind=True, ignore_result=True)
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

    for installation_url, installation_json in installations_from_github.items():
        with task_app_installation_lock_acquire(installation_url, blocking=False, timeout=300) as acquired:
            if acquired:
                GitHubAppInstallation.objects.create(url=installation_url)
                logger.info(f'created GitHubAppInstallation: {installation_url}')
                installation_id = int(installation_json['id'])
                github_sync_app_installation(installation_id)
            else:
                logger.info(f'skipped adding installation {installation_url}: failed to acquire lock')

    installation_urls_to_remove = installation_urls_in_db - installations_from_github.keys()
    logger.info(f'found {len(installation_urls_to_remove)} installations to remove')

    for installation_url in installation_urls_to_remove:
        with task_app_installation_lock_acquire(installation_url, blocking=False, timeout=300) as acquired:
            if acquired:
                installation = GitHubAppInstallation.objects.get(url=installation_url)
                github_sync_app_installation_remove(installation)
            else:
                logger.info(f'skipped removing installation {installation_url}: failed to acquire lock')

    logger.info('starting next task iteration')
    self.apply_async()