"""
Service for validating GitHub resources (users, repos, issues) exist on GitHub.

Uses GitHub REST API with caching to minimize API calls and respect rate limits.
"""

import requests
import logging
from typing import Optional, Tuple
from django.core.cache import cache
from django.conf import settings
from .github_auth import GitHubAppAuth

logger = logging.getLogger(__name__)


class GitHubValidationService:
    """Service for validating GitHub resources (users, repos, issues) exist on GitHub"""

    GITHUB_API_BASE = "https://api.github.com"
    CACHE_TTL_SECONDS = 3600  # 1 hour default
    REQUEST_TIMEOUT = 10

    def __init__(self):
        self.github_auth = GitHubAppAuth()
        self.access_token = self._get_access_token()

    def _get_access_token(self) -> Optional[str]:
        """
        Get GitHub App access token for API calls.

        Attempts to get token from any available installation.
        Returns None if no installations are available.
        """
        installations = self.github_auth.get_app_installations()
        if not installations:
            logger.warning("No GitHub App installations available for validation")
            return None

        # Use the first available installation
        access_token = self.github_auth.get_installation_access_token(installations[0]['id'])
        if not access_token:
            logger.warning("Failed to get GitHub App access token for validation")
            return None

        return access_token

    def validate_user_exists(self, username: str) -> bool:
        """
        Check if a GitHub user exists.

        Args:
            username: GitHub username to validate

        Returns:
            True if user exists, False otherwise
        """
        return self._validate_resource('user', username, f"/users/{username}")

    def validate_repo_exists(self, owner: str, repo: str) -> bool:
        """
        Check if a GitHub repository exists.

        Args:
            owner: Repository owner username
            repo: Repository name

        Returns:
            True if repository exists, False otherwise
        """
        resource_id = f"{owner}/{repo}"
        return self._validate_resource('repo', resource_id, f"/repos/{owner}/{repo}")

    def validate_issue_exists(self, owner: str, repo: str, issue_number: int) -> bool:
        """
        Check if a GitHub issue exists.

        Args:
            owner: Repository owner username
            repo: Repository name
            issue_number: Issue number

        Returns:
            True if issue exists, False otherwise
        """
        resource_id = f"{owner}/{repo}/{issue_number}"
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}"
        return self._validate_resource('issue', resource_id, endpoint)

    def _validate_resource(self, resource_type: str, identifier: str, endpoint: str) -> bool:
        """
        Validate a resource exists on GitHub.

        Uses cache to avoid repeated API calls within TTL window.

        Args:
            resource_type: Type of resource ('user', 'repo', 'issue')
            identifier: Unique identifier for the resource
            endpoint: GitHub API endpoint to check

        Returns:
            True if resource exists, False otherwise
        """
        # Generate cache key
        cache_key = self._get_cache_key(resource_type, identifier)

        # Check cache first
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Cache HIT: {cache_key} = {cached_result}")
            return cached_result

        logger.debug(f"Cache MISS: {cache_key}")

        # Make API call
        exists, status_code, error_msg = self._call_github_api(endpoint)

        # Update cache
        cache.set(cache_key, exists, timeout=self.CACHE_TTL_SECONDS)
        logger.debug(f"Cached {cache_key} = {exists} (TTL: {self.CACHE_TTL_SECONDS}s)")

        return exists

    def _call_github_api(self, endpoint: str) -> Tuple[bool, Optional[int], str]:
        """
        Make REST API call to GitHub to validate resource exists.

        Args:
            endpoint: API endpoint path (e.g., "/users/octocat")

        Returns:
            Tuple of (exists, status_code, error_message)
            - exists: True if resource found (200), False otherwise
            - status_code: HTTP status code from response
            - error_message: Error details if not found
        """
        if not self.access_token:
            logger.error("Cannot validate: No GitHub access token available")
            return False, None, "No GitHub access token available"

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'sponsoredissues.org'
        }

        try:
            url = self.GITHUB_API_BASE + endpoint
            response = requests.get(
                url,
                headers=headers,
                timeout=self.REQUEST_TIMEOUT
            )

            # Log rate limit info
            remaining = response.headers.get('X-RateLimit-Remaining')
            reset_time = response.headers.get('X-RateLimit-Reset')
            if remaining:
                logger.debug(f"GitHub API rate limit: {remaining} remaining (resets at {reset_time})")

            # 200 = exists, 404 = not found
            if response.status_code == 200:
                return True, 200, ""
            elif response.status_code == 404:
                return False, 404, "Resource not found on GitHub"
            elif response.status_code == 403:
                error_msg = "Access forbidden (possibly private repository)"
                logger.warning(f"{endpoint}: {error_msg}")
                return False, 403, error_msg
            else:
                error_msg = f"GitHub API returned {response.status_code}: {response.text}"
                logger.error(error_msg)
                return False, response.status_code, error_msg

        except requests.Timeout:
            error_msg = "GitHub API request timed out"
            logger.error(error_msg)
            return False, None, error_msg
        except requests.RequestException as e:
            error_msg = f"GitHub API request failed: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg

    def _get_cache_key(self, resource_type: str, identifier: str) -> str:
        """
        Generate cache key for a resource.

        Args:
            resource_type: Type of resource ('user', 'repo', 'issue')
            identifier: Unique identifier for the resource

        Returns:
            Cache key string
        """
        return f'github:validation:{resource_type}:{identifier}'
