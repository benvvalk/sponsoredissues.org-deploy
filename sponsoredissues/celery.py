# Standard Celery configuration for Django.
# See: https://docs.celeryq.dev/en/v5.5.3/django/first-steps-with-django.html

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sponsoredissues.settings')
app = Celery('sponsoredissues')
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load `tasks.py` from all registered Django apps
app.autodiscover_tasks()