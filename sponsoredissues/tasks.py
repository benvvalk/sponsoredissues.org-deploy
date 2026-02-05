import redis

from celery.utils.log import get_task_logger
from django.conf import settings
from sponsoredissues.celery import app
from sponsoredissues.github_app import GitHubApp
from sponsoredissues.github_sync import github_sync_app_installation
from sponsoredissues.models import GitHubAppInstallation

redis_client = redis.Redis.from_url(url=settings.REDIS_URL, decode_responses=True)

logger = get_task_logger(__name__)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    logger.info(f'Request: {self.request!r}')

@app.task(bind=True, ignore_result=True)
def task_sync_github_app_installation_least_recently_updated(self):
    installations = GitHubAppInstallation.objects.all().order_by("updated_at")
    logger.info(f'database contains {installations.count()} app installations')

    for installation in installations:
        lock = redis_client.lock(
            name=f'lock:{installation.url}',
            timeout=300,        # task must complete before this expires
            blocking_timeout=0  # return immediately if lock not available
        )
        if lock.acquire():
            try:
                github_sync_app_installation(installation.installation_id())
            finally:
                lock.release()
