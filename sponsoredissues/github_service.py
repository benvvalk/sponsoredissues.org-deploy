import requests
import logging
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Sum
from typing import Dict, List, Optional
from decimal import Decimal

logger = logging.getLogger(__name__)

class GitHubSponsorService:
    """Service for fetching GitHub Sponsors data via GraphQL API using user access tokens"""

    GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
    GITHUB_WEB_BASE = "https://github.com"
    CACHE_TTL_SECONDS = 3600  # 1 hour default
    REQUEST_TIMEOUT = 10

    def _get_user_access_token(self, user: User):
        """Get GitHub access token from user's social account"""
        from allauth.socialaccount.models import SocialToken, SocialAccount
        github_account = user.socialaccount_set.get(provider='github')
        social_token = SocialToken.objects.get(account=github_account)
        return social_token.token

    def _make_graphql_request(self, query: str, access_token: str, variables: Dict = None) -> Optional[Dict]:
        """Make a GraphQL request to GitHub API"""
        if not access_token:
            return None

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }

        payload = {'query': query}
        if variables:
            payload['variables'] = variables

        try:
            response = requests.post(
                self.GITHUB_GRAPHQL_URL,
                json=payload,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'errors' in data:
                logger.error(f"GitHub GraphQL API errors: {data['errors']}")
                return None

            return data.get('data')
        except requests.exceptions.RequestException as e:
            logger.error(f"GitHub API request failed: {e}")
            return None

    def calculate_total_sponsor_cents_given(self, sponsor_user: User, recipient_github_username: str) -> Decimal:
        """
        Calculate total sponsor cents given by sponsor_user to recipient_github_username.
        This represents the cumulative amount available for allocation.
        """
        # Get access token for the logged-in user
        access_token = self._get_user_access_token(sponsor_user)

        query = """
        query($recipient_github_username: String!) {
           viewer {
              totalSponsorshipAmountAsSponsorInCents(sponsorableLogins: [$recipient_github_username])
           }
        }
        """

        variables = {'recipient_github_username': recipient_github_username}
        response = self._make_graphql_request(query, access_token, variables)

        return response['viewer']['totalSponsorshipAmountAsSponsorInCents']

    def calculate_allocated_sponsor_cents(self, sponsor_user: User, recipient_github_username: str) -> (Decimal, Decimal):
        """
        Return (allocated_sponsor_cents, total_sponsor_cents), where:

        * `allocated_sponsor_cents` is the total number of cents (USD)
        that `sponsor_user` has assigned to GitHub issues owned by
        `recipient_github_username` (the donee).

        * `total_sponsor_cents`: The total number of cents (USD) that
        `sponsor_user` has donated to `recipient_github_username` (the donee) on
        GitHub Sponsors, since the beginning of time.
        """
        from .models import SponsorAmount, GitHubIssue

        # Get all sponsor amounts allocated by `sponsor_user` to
        # issues owned by `recipient_github_username`.
        allocated_amounts = SponsorAmount.objects.filter(
            sponsor_user_id=sponsor_user,
            target_github_issue__url__contains=f"github.com/{recipient_github_username}/"
        ).aggregate(total=Sum('cents_usd'))
        allocated_sponsor_cents = allocated_amounts['total'] or Decimal('0')

        # Query GitHub GraphQL API for total cents given by
        # `sponsor_user` to `recipient_github_username`, since the beginning of time.
        total_sponsor_cents = self.calculate_total_sponsor_cents_given(sponsor_user, recipient_github_username)

        return (allocated_sponsor_cents, total_sponsor_cents)

    def has_sponsors_profile(self, username: str) -> bool:
        """
        Check if a GitHub user has a public sponsors profile.

        This method checks for redirects from the sponsors URL. If the user
        does not have a sponsors profile, GitHub redirects to their regular
        profile page.

        Args:
            username: GitHub username to check

        Returns:
            True if user has a sponsors profile, False otherwise
        """
        # Generate cache key
        cache_key = f'github:has_sponsors_profile:{username}'

        # Check cache first
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Cache HIT: {cache_key} = {cached_result}")
            return cached_result

        logger.debug(f"Cache MISS: {cache_key}")

        # Check sponsors profile by detecting redirects
        sponsors_url = f"{self.GITHUB_WEB_BASE}/sponsors/{username}"

        try:
            # Make HEAD request with allow_redirects=False to detect redirects
            response = requests.head(
                sponsors_url,
                allow_redirects=False,
                timeout=self.REQUEST_TIMEOUT
            )

            has_sponsors = False

            # If status is 200, sponsors page exists
            if response.status_code == 200:
                has_sponsors = True
                logger.debug(f"User {username} has sponsors profile (200 OK)")
            # If redirect status (301, 302), check where it redirects to
            elif response.status_code in (301, 302):
                location = response.headers.get('Location', '')
                # If redirects back to user profile (not sponsors page), no sponsors profile
                if f"github.com/{username}" in location and "/sponsors/" not in location:
                    has_sponsors = False
                    logger.debug(f"User {username} does not have sponsors profile (redirected to profile)")
                else:
                    has_sponsors = True
                    logger.debug(f"User {username} has sponsors profile (redirect to: {location})")
            else:
                has_sponsors = False
                logger.debug(f"User {username} sponsors check returned status {response.status_code}")

            # Update cache
            cache.set(cache_key, has_sponsors, timeout=self.CACHE_TTL_SECONDS)
            logger.debug(f"Cached {cache_key} = {has_sponsors} (TTL: {self.CACHE_TTL_SECONDS}s)")

            return has_sponsors

        except requests.Timeout:
            logger.error(f"Timeout checking sponsors profile for {username}")
            # Don't cache failures
            return False
        except requests.RequestException as e:
            logger.error(f"Failed to check sponsors profile for {username}: {e}")
            # Don't cache failures
            return False

    def _get_github_username(self, user: User) -> Optional[str]:
        """Get GitHub username from user's social account"""
        from allauth.socialaccount.models import SocialAccount

        github_account = user.socialaccount_set.get(provider='github')
        return github_account.extra_data.get('login')