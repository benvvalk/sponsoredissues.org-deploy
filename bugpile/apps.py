from django.apps import AppConfig
import logging

class BugpileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bugpile'

    def ready(self):
        from django.conf import settings
        from .paypal import init_paypal

        init_paypal(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET)