import requests
import base64
from typing import Optional, Literal
from django.conf import settings

# Global variable to cache PayPal environment, set during app startup
PAYPAL_MODE: Optional[Literal['sandbox', 'live']] = None

def get_access_token(
    mode: Literal['sandbox', 'live'],
    client_id: str,
    client_secret: str
) -> Optional[str]:
    """
    Gets an access token from PayPal for the specified environment.

    Args:
        mode: PayPal environment ('sandbox' or 'live')
        client_id: PayPal client ID (required)
        client_secret: PayPal client secret (required)

    Returns:
        Access token string if successful, None if failed
    """
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

    # Determine URL based on mode
    if mode == 'sandbox':
        url = 'https://api-m.sandbox.paypal.com/v1/oauth2/token'
    else:  # live
        url = 'https://api-m.paypal.com/v1/oauth2/token'

    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        if response.status_code == 200:
            response_data = response.json()
            return response_data.get('access_token')
    except (requests.RequestException, KeyError, ValueError):
        pass

    return None

def init_paypal(
    client_id: str,
    client_secret: str
) -> Optional[Literal['sandbox', 'live']]:
    """
    Determines if PayPal credentials are for sandbox or live environment.

    Tests the credentials against PayPal's OAuth token endpoints, checking
    sandbox first since it's the more common use case during development.

    Args:
        client_id: PayPal client ID (required)
        client_secret: PayPal client secret (required)

    Returns:
        'sandbox' if credentials work with sandbox API
        'live' if credentials work with live API
        None if credentials don't work with either API or are missing
    """
    if not client_id or not client_secret:
        return None

    # Test sandbox first (more common during development)
    if get_access_token('sandbox', client_id, client_secret):
        return 'sandbox'

    # Test live environment
    if get_access_token('live', client_id, client_secret):
        return 'live'

    return None