import requests
import base64
from typing import Optional, Literal
from django.conf import settings

# Global variable to cache PayPal environment, set during app startup
PAYPAL_MODE: Optional[Literal['sandbox', 'live']] = None

def detect_paypal_environment(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None
) -> Optional[Literal['sandbox', 'live']]:
    """
    Determines if PayPal credentials are for sandbox or live environment.

    Tests the credentials against PayPal's OAuth token endpoints, checking
    sandbox first since it's the more common use case during development.

    Args:
        client_id: PayPal client ID. If None, uses settings.PAYPAL_CLIENT_ID
        client_secret: PayPal client secret. If None, uses settings.PAYPAL_CLIENT_SECRET

    Returns:
        'sandbox' if credentials work with sandbox API
        'live' if credentials work with live API
        None if credentials don't work with either API or are missing
    """
    # Use provided credentials or fall back to settings
    client_id = client_id or settings.PAYPAL_CLIENT_ID
    client_secret = client_secret or settings.PAYPAL_CLIENT_SECRET

    if not client_id or not client_secret:
        return None

    # Prepare authentication header
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        'Authorization': f'Basic {encoded_credentials}',
        'Accept': 'application/json',
        'Accept-Language': 'en_US',
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    data = 'grant_type=client_credentials'

    # Test sandbox first (more common during development)
    sandbox_url = 'https://api-m.sandbox.paypal.com/v1/oauth2/token'
    try:
        response = requests.post(sandbox_url, headers=headers, data=data, timeout=10)
        if response.status_code == 200 and 'access_token' in response.json():
            return 'sandbox'
    except (requests.RequestException, KeyError, ValueError):
        pass

    # Test live environment
    live_url = 'https://api-m.paypal.com/v1/oauth2/token'
    try:
        response = requests.post(live_url, headers=headers, data=data, timeout=10)
        if response.status_code == 200 and 'access_token' in response.json():
            return 'live'
    except (requests.RequestException, KeyError, ValueError):
        pass

    return None