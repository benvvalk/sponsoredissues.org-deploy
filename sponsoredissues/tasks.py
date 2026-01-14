from celery.utils.log import get_task_logger
from .celery import app

logger = get_task_logger(__name__)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    logger.info(f'Request: {self.request!r}')