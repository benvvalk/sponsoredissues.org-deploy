import logging
import redis
import time

from celery.utils.log import get_task_logger
from django.conf import settings
from sponsoredissues.celery import app
from sponsoredissues.github_api import github_api
from sponsoredissues.github_app import github_app_token
from sponsoredissues.github_sync import github_sync_app_installation, github_sync_app_installation_remove
from sponsoredissues.models import GitHubAppInstallation

redis_client = redis.Redis.from_url(url=settings.REDIS_URL, decode_responses=True)

logger = get_task_logger(__name__)

@app.task(ignore_result=True)
def task_sync_github_app_installation(installation_id: int):
    installation_url = f'https://github.com/settings/installations/{installation_id}'
    lock = redis_client.lock(
        name=f'lock:{installation_url}',
        blocking=True,
        timeout=300, # lock will be released after timeout
    )
    with lock:
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
        lock = redis_client.lock(
            name=f'lock:{installation.url}',
            blocking=False,  # return immediately if lock not available
            timeout=300      # lock will be released after timeout
        )
        if lock.acquire():
            try:
                github_sync_app_installation(installation.installation_id())
            except Exception:
                logging.exception('unexpected exception during `github_sync_app_installation`')
                task_sleep_after_unexpected_exception()
            finally:
                lock.release()

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
        lock = redis_client.lock(
            name=f'lock:{installation_url}',
            blocking=False,  # return immediately if lock not available
            timeout=300      # lock will be released after timeout
        )
        if lock.acquire():
            try:
                GitHubAppInstallation.objects.create(url=installation_url)
                logger.info(f'created GitHubAppInstallation: {installation_url}')
                installation_id = int(installation_json['id'])
                github_sync_app_installation(installation_id)
            except Exception:
                logging.exception('unexpected exception during `github_sync_app_installation`')
                task_sleep_after_unexpected_exception()
            finally:
                lock.release()
        else:
            logger.info(f'skipped adding installation {installation_url}: failed to acquire lock')

    installation_urls_to_remove = installation_urls_in_db - installations_from_github.keys()
    logger.info(f'found {len(installation_urls_to_remove)} installations to remove')

    for installation_url in installation_urls_to_remove:
        lock = redis_client.lock(
            name=f'lock:{installation_url}',
            blocking=False,  # return immediately if lock not available
            timeout=300      # lock will be released after timeout
        )
        if lock.acquire():
            try:
                installation = GitHubAppInstallation.objects.get(url=installation_url)
                github_sync_app_installation_remove(installation)
            finally:
                lock.release()
        else:
            logger.info(f'skipped removing installation {installation_url}: failed to acquire lock')

    logger.info('starting next task iteration')
    self.apply_async()