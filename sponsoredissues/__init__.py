default_app_config = 'sponsoredissues.apps.SponsoredIssuesConfig'

# Standard Celery configuration for Django.
# See: https://docs.celeryq.dev/en/v5.5.3/django/first-steps-with-django.html
#
# Note: `__all__` determines which symbols are imported by
# `from sponsoredissues import *`.
from .celery import app as celery_app
__all__ = ('celery_app',)