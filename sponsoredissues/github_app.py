import requests
import jwt
import logging
from datetime import datetime, timedelta
from django.conf import settings
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

# Note: Added "Class" suffix to prevent name collision with
# `GitHubAppInstallation` in `models.py`.
class GitHubAppInstallationClass:
    access_token: Optional[str] = None
    github_app: 'GitHubApp' # quotes indicate forward declaration
    installation_id: int
    installation_json: Optional[Dict[str, Any]] = None

    def __init__(self, installation_id: int, installation_json: Optional[Dict[str, Any]] = None):
        self.github_app = GitHubApp()
        self.installation_id = installation_id
        self.installation_json = installation_json

    @classmethod
    def from_json(cls, installation_json):
        installation_id = int(installation_json['id'])
        return cls(installation_id, installation_json)

    @classmethod
    def from_id(cls, installation_id):
        return cls(installation_id)

    def get_access_token(self):
        if self.access_token:
            return self.access_token

        response = requests.post(
            f'https://api.github.com/app/installations/{self.installation_id}/access_tokens',
            headers=self.github_app.get_request_headers(),
            timeout=30
        )
        response.raise_for_status()

        self.access_token = response.json()['token']
        return self.access_token

class GitHubApp:
    """Shared GitHub App authentication utilities"""

    def __init__(self):
        self.app_id = settings.GITHUB_APP_ID
        self.private_key = settings.GITHUB_APP_PRIVATE_KEY

        if not self.app_id or not self.private_key:
            logger.warning("GitHub App credentials not configured. GitHub App features will not be available.")

    def _get_github_app_token(self) -> Optional[str]:
        """Generate GitHub App JWT token"""
        if not self.app_id or not self.private_key:
            return None

        private_key_str = self.private_key

        # Handle both single-line (with \\n) and multiline PEM formats
        if '\\n' in private_key_str:
            private_key_str = private_key_str.replace('\\n', '\n')

        payload = {
            'iat': int(datetime.utcnow().timestamp()),
            'exp': int((datetime.utcnow() + timedelta(minutes=5)).timestamp()),
            'iss': self.app_id
        }

        try:
            return jwt.encode(payload, private_key_str.encode(), algorithm='RS256')
        except Exception as e:
            raise RuntimeError("Failed to generate GitHub App token. Did you configure GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY?") from e

    def get_request_headers(self, **kwargs):
        app_token = self._get_github_app_token()
        return {
            'Authorization': f'Bearer {app_token}',
            'Accept': 'application/vnd.github.v3+json'
        } | kwargs

    def query_installations(self, target_installation_id: Optional[int] = None):
        """Get all GitHub App installations"""
        try:
            response = requests.get(
                'https://api.github.com/app/installations',
                headers=self.get_request_headers(),
                timeout=30
            )
            response.raise_for_status()

            installation_jsons = response.json()

            if target_installation_id:
                installation_jsons = [i for i in installation_jsons if i['id'] == target_installation_id]

            installations = [ GitHubAppInstallationClass.from_json(i) for i in installation_jsons ]

            return installations

        except requests.RequestException as e:
            logger.error(f'Failed to get GitHub App installations: {e}')
            return []

    def get_installation_for_github_account(self, github_account_name):
        """Get app installation for GitHub account name (username or orgname)"""
        # TODO: Handle case where `github_account_name` is an orgname
        # rather than a username. (We need to do a separate query for
        # that.)
        response = requests.get(
            f'https://api.github.com/users/{github_account_name}/installation',
            headers=self.get_request_headers(username=github_account_name),
            timeout=30
        )
        response.raise_for_status()

        return response.json()

    def get_any_installation_access_token(self):
        """
        Get GitHub App access token for API calls.

        Attempts to get token from any available installation.
        Returns None if no installations are available.
        """
        installations = self.query_installations()
        if not installations:
            logger.warning("No GitHub App installations available")
            return None

        # Use the first available installation
        access_token = installations[0].get_access_token()
        if not access_token:
            logger.warning("Failed to get GitHub App access token")
            return None

        return access_token
