from django.apps import AppConfig
import logging

class BugpileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bugpile'

    def ready(self):
        from django.conf import settings
        from .utils import detect_paypal_environment

        logger = logging.getLogger('bugpile')

        client_id = settings.PAYPAL_CLIENT_ID
        client_secret = settings.PAYPAL_CLIENT_SECRET

        if not client_id and not client_secret:
            logger.info("PayPal not configured: PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET need to be set")
        elif not client_id:
            logger.info("PayPal not configured: PAYPAL_CLIENT_ID needs to be set")
        elif not client_secret:
            logger.info("PayPal not configured: PAYPAL_CLIENT_SECRET needs to be set")
        else:
            environment = detect_paypal_environment()
            if environment:
                logger.info(f"PayPal configured for {environment} mode")
            else:
                logger.info("PayPal credentials are invalid for both sandbox and live environments")