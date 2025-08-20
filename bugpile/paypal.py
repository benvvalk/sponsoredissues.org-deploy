import requests
import base64
import json
import logging
from typing import Optional, Literal
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST

# Global variable to cache PayPal environment, set during app startup
PAYPAL_MODE: Optional[Literal['sandbox', 'live']] = None

def get_api_url(mode: Literal['sandbox', 'live']) -> str:
    """
    Returns the base URL for PayPal API requests based on the mode.

    Args:
        mode: PayPal environment ('sandbox' or 'live')

    Returns:
        Base URL for the specified PayPal environment
    """
    if mode == 'sandbox':
        return 'https://api-m.sandbox.paypal.com'
    else:  # live
        return 'https://api-m.paypal.com'

def get_api_token(
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

    # Use the get_api_url method to determine URL based on mode
    url = f"{get_api_url(mode)}/v1/oauth2/token"

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
    global PAYPAL_MODE

    if not client_id:
        print("PayPal not configured: PAYPAL_CLIENT_ID needs to be set")

    if not client_secret:
        print("PayPal not configured: PAYPAL_CLIENT_SECRET needs to be set")

    if not client_id or not client_secret:
        PAYPAL_MODE = None
        return PAYPAL_MODE

    # Test sandbox first (more common during development)
    if get_api_token('sandbox', client_id, client_secret):
        PAYPAL_MODE = 'sandbox'
    elif get_api_token('live', client_id, client_secret):
        PAYPAL_MODE = 'live'
    else:
        PAYPAL_MODE = None

    if PAYPAL_MODE:
        print(f"PayPal configured for {PAYPAL_MODE} mode")
    else:
        print("PayPal not configured: invalid PAYPAL_CLIENT_ID/PAYPAL_CLIENT_SECRET")

    return PAYPAL_MODE

"""
Capture a PayPal payment.

This is a webhook that gets invoked immediately after the user has
approved a payment in the PayPal browser pop-up window.

PayPal API documentation:
https://developer.paypal.com/docs/api/orders/v2/#orders_capture

Security note:

In the code below, the `order_id` field from the incoming POST data is
directly injected into the URLs that we use for our PayPal REST API
calls. This doesn't feel great from a security standpoint, but I think
it's fine actually. The PayPal API calls are authenticated using our
API token (`api_token` below), and we're only allowed to make API calls
against PayPal order IDs that were created with ourselves (with our
PAYPAL_CLIENT_ID). On top of that, this method uses Django's usual CSRF
token checking.
"""
@require_POST
def capture_order(request):
    global PAYPAL_MODE

    # Check if PayPal settings (`PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET`)
    # are correctly configured.

    if PAYPAL_MODE is None:
        return JsonResponse({'reason': 'paypal settings not correctly configured'}, status=500)

    # Get PayPal order details (e.g. amount, currency).

    paypal_order_id = json.loads(request.body)['paypal_order_id']

    api_token = get_api_token(
        PAYPAL_MODE, settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET)

    headers = {
        'Authorization': 'Bearer ' + api_token,
        'Content-Type': 'application/json'
    }

    url = f'{get_api_url(PAYPAL_MODE)}/v2/checkout/orders/{paypal_order_id}'

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return JsonResponse({'reason': 'paypal API call failed'}, status=500)

    order = response.json()
    if settings.DEBUG:
        print(f"PayPal order data: {json.dumps(order, indent=2)}")

    # sanity check (might not be necessary)
    if order['status'] != 'APPROVED':
        return JsonResponse({'reason': 'paypal order is not APPROVED'}, status=500)

    # TODO: Update the donation total for the target GitHub issue
    # in our database.

    return JsonResponse({'status': 'success'})