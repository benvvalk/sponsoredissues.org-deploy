from django.apps import AppConfig
import logging
import sys

class SponsoredIssuesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sponsoredissues'

    def ready(self):
        # Only call `init_paypal` when starting the Django server
        # with the `./manage.py runserver` command, not during other
        # management commands (e.g. `./manage.py migrate`).
        if 'runserver' in sys.argv:
            import os
            # Prevent `init_paypal` from being called twice due to
            # Django's file-watcher/reloader process. See the
            # following notes for further explanation:
            # https://programmersought.com/article/49721374106/
            if os.environ.get('RUN_MAIN') == 'true':
                from django.conf import settings
                from .paypal import init_paypal
                init_paypal(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET)