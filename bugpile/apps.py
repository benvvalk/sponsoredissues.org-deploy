from django.apps import AppConfig
import logging
import sys

class BugpileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bugpile'

    def ready(self):
        # Only initialize PayPal when running the server
        if 'runserver' in sys.argv:
            from django.conf import settings
            from .paypal import init_paypal

            init_paypal(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET)