import requests
import logging
import time
import random
import re

from urllib.parse import urljoin

logger = logging.getLogger(__name__)

def random_sleep_for_rate_limiting():
    seconds = random.uniform(2, 10)
    logger.info(f"Sleeping {seconds:.1f}s (for rate limiting)...")
    time.sleep(seconds)

def _parse_link_header(link_header):
    """
    Extract pagination URLs from GitHub's `Link` HTTP header, which is
    included in responses to GitHub's REST API queries.

    Args:
        link_header: The Link header value from GitHub API response

    Returns: Dict mapping rel types to URLs (e.g., {'next': 'https://...', 'last': '...'})
    """
    links = {}
    if not link_header:
        return links

    # Link header format: <url>; rel="next", <url>; rel="last", ...
    for link in link_header.split(','):
        match = re.match(r'<([^>]+)>;\s*rel="([^"]+)"', link.strip())
        if match:
            url, rel = match.groups()
            links[rel] = url

    return links

def github_api(endpoint, access_token=None, auto_paginate=True, max_pages=10, per_page=100):
    """
    Make REST API call to GitHub with automatic pagination support.

    Args:
        endpoint: API endpoint path (e.g. "/users/octocat")
        access_token (optional): GitHub user/app access token
        auto_paginate (bool): If True, automatically fetch all pages. Default: True
        max_pages (int): Maximum number of pages to fetch. Default: 10
        per_page (int): Items per page (max 100). Default: 100

    Returns: Tuple of (status_code, data)
        - status_code: HTTP status code from first request
        - data: Response JSON data. If auto_paginate=True and response is a list or
                dict with 'repositories', 'items', etc., all pages are merged.
    """
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'sponsoredissues.org'
    }

    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'

    # Add per_page parameter to endpoint if auto_paginate is enabled
    if auto_paginate and '?' in endpoint:
        endpoint = f"{endpoint}&per_page={per_page}"
    elif auto_paginate:
        endpoint = f"{endpoint}?per_page={per_page}"

    try:
        url = urljoin("https://api.github.com", endpoint)
        response = requests.get(
            url,
            headers=headers,
            timeout=10 # seconds
        )
        response.raise_for_status()

        # Log GitHub API rate limit info
        remaining = response.headers.get('X-RateLimit-Remaining')
        reset_time = response.headers.get('X-RateLimit-Reset')
        if remaining:
            logger.debug(f"GitHub API rate limit: {remaining} remaining (resets at {reset_time})")

        # Parse initial response
        data = response.json()

        # If not auto-paginating or request failed, return as-is
        if not auto_paginate:
            return data

        # Check if response is paginated (has Link header with 'next')
        link_header = response.headers.get('Link')
        if not link_header:
            # No pagination, return as-is
            return data

        links = _parse_link_header(link_header)

        # Determine if we need to merge results
        # GitHub typically returns lists or dicts with a key containing items
        if isinstance(data, list):
            all_items = data
            is_list_response = True
        elif isinstance(data, dict):
            # Check for common pagination keys
            pagination_key = None
            for key in ['repositories', 'items', 'issues', 'pulls', 'users']:
                if key in data and isinstance(data[key], list):
                    pagination_key = key
                    break

            if pagination_key:
                all_items = data[pagination_key]
                is_list_response = False
            else:
                # Dict but not a recognized paginated format
                return data
        else:
            # Not a format we can paginate
            return data

        # Fetch additional pages
        page_count = 1
        next_url = links.get('next')

        while next_url and page_count < max_pages:
            # Rate limiting delay
            random_sleep_for_rate_limiting(logger=logger)
            logger.debug(f"Fetching page {page_count + 1}")

            response = requests.get(
                next_url,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()

            # Log rate limit info
            remaining = response.headers.get('X-RateLimit-Remaining')
            if remaining:
                logger.debug(f"GitHub API rate limit: {remaining} remaining")

            if response.status_code != 200:
                logger.warning(f"Pagination failed on page {page_count + 1}: status {response.status_code}")
                break

            page_data = response.json()

            # Merge results
            if is_list_response:
                all_items.extend(page_data)
            else:
                all_items.extend(page_data[pagination_key])

            page_count += 1

            # Check for next page
            link_header = response.headers.get('Link')
            if link_header:
                links = _parse_link_header(link_header)
                next_url = links.get('next')
            else:
                next_url = None

        if page_count >= max_pages and next_url:
            logger.warning(f"Reached max_pages limit ({max_pages}). More pages available but not fetched.")

        logger.info(f"Fetched {page_count} page(s), total items: {len(all_items)}")

        # Return merged results
        if is_list_response:
            return all_items
        else:
            # Update the dict with all items
            data[pagination_key] = all_items
            return data

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"GitHub API request failed") from e

def github_graphql(query, access_token, variables=None, timeout=30):
    """
    Send a query to the GitHub GraphQL API.

    Args:
        query: A GraphQL query [required]
        access_token:  GitHub user/app access token [required]
        variables: Dictionary of GraphQL variable values [None]
        timeout: Request timeout in seconds [30]

    Returns:
        data: The value of the `data` key in the response JSON
    """
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }

    payload = {
        'query': query,
        'variables': variables
    }

    response = requests.post(
        'https://api.github.com/graphql',
        json=payload,
        headers=headers,
        timeout=timeout,
    )

    response.raise_for_status()

    response_json = response.json()

    graphql_errors = response_json.get('errors')
    if graphql_errors:
        raise RuntimeError(f'GraphQL errors: {graphql_errors}')

    return response_json.get('data')

def github_issue_has_sponsoredissues_label(issue_data):
    """
    Return true if the given issue has the 'sponsoredissues.org' label.

    Args:
        issue_data: The JSON data for the issue, as returned by the
                    GitHub API.
    """
    labels = issue_data.get('labels', [])
    for label in labels:
        if label.get('name') == 'sponsoredissues.org':
            return True
    return False

def github_app_installation_is_suspended(installation_data):
    # Note: It is possible for `installation['suspended_at']`
    # to exist but have a value of `None`, which means that
    # the app installation is active.
    return 'suspended_at' in installation_data and installation_data['suspended_at']
