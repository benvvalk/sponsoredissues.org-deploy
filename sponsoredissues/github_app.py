import requests
import jwt
import logging
from datetime import datetime, timedelta
from django.conf import settings
from sponsoredissues.github_api import github_api, github_graphql
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

def github_app_token():
    """Generate GitHub App JWT token"""

    app_id = settings.GITHUB_APP_ID
    private_key = settings.GITHUB_APP_PRIVATE_KEY

    if not app_id or not private_key:
        raise RuntimeError("Failed to generate GitHub App token: GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY not set")

    # Handle both single-line (with \\n) and multiline PEM formats
    if '\\n' in private_key:
        private_key = private_key.replace('\\n', '\n')

    payload = {
        'iat': int(datetime.utcnow().timestamp()),
        'exp': int((datetime.utcnow() + timedelta(minutes=5)).timestamp()),
        'iss': app_id
    }

    try:
        return jwt.encode(payload, private_key.encode(), algorithm='RS256')
    except Exception as e:
        raise RuntimeError("Failed to generate GitHub App token: Check format of GITHUB_APP_PRIVATE_KEY") from e

def github_app_request_headers(**kwargs):
    app_token = github_app_token()
    return {
        'Authorization': f'Bearer {app_token}',
        'Accept': 'application/vnd.github.v3+json'
    } | kwargs


def github_app_query_installation_for_github_account(github_account_name):
    """Get app installation for GitHub account name (username or orgname)"""
    # TODO: Handle case where `github_account_name` is an orgname
    # rather than a username. (We need to do a separate query for
    # that.)
    response = requests.get(
        f'https://api.github.com/users/{github_account_name}/installation',
        headers=github_app_request_headers(username=github_account_name),
        timeout=30
    )
    response.raise_for_status()

    return response.json()

def github_app_query_installations(target_installation_id: Optional[int] = None):
    """Get all GitHub App installations"""
    try:
        response = requests.get(
            'https://api.github.com/app/installations',
            headers=github_app_request_headers(),
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

def github_app_installation_query_token(installation_id: int):
    response = requests.post(
        f'https://api.github.com/app/installations/{installation_id}/access_tokens',
        headers=github_app_request_headers(),
        timeout=30
    )
    response.raise_for_status()
    return response.json()['token']

def github_app_query_installation_token_any():
    """
    Get GitHub App access token for API calls.

    Attempts to get token from any available installation.
    Returns None if no installations are available.
    """
    installations = github_app_query_installations()
    if not installations:
        logger.warning("No GitHub App installations available")
        return None

    # Use the first available installation
    installation_json = installations[0].installation_json
    assert installation_json
    installation_id = installation_json['id']
    access_token = github_app_installation_query_token(installation_id)
    if not access_token:
        logger.warning("Failed to get GitHub App access token")
        return None

    return access_token

def github_app_installation_query_json(installation_id):
    response = requests.get(
        f'https://api.github.com/app/installations/{installation_id}',
        headers=github_app_request_headers(),
        timeout=30
    )
    response.raise_for_status()
    return response.json()

def github_app_installation_query_issues_with_sponsoredissues_label(installation_token, github_username):
    """Query user's public repositories and issues with sponsoredissues.org label"""
    query = """
    query($username: String!, $issueFirst: Int!, $cursor: String) {
        user(login: $username) {
            repositories(
                first: 30
                after: $cursor
                privacy: PUBLIC
                orderBy: {field: UPDATED_AT, direction: DESC}
            ) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    name
                    owner {
                        login
                    }
                    issues(
                        first: $issueFirst
                        states: [OPEN, CLOSED]
                        labels: ["sponsoredissues.org"]
                    ) {
                        nodes {
                            number
                            title
                            body
                            repository {
                                homepageUrl
                                url
                            }
                            state
                            url
                            createdAt
                            updatedAt
                            labels(first: 20) {
                                nodes {
                                    name
                                    color
                                }
                            }
                            author {
                                login
                            }
                        }
                    }
                }
            }
        }
    }
    """

    variables = {
        'username': github_username,
        'issueFirst': 100,  # Get up to 100 issues per repo
        'cursor': None
    }

    issues = []
    repos_processed = 0
    page_info = {'hasNextPage': True, 'endCursor': None}

    while page_info.get('hasNextPage'):
        variables['cursor'] = page_info.get('endCursor')

        logger.info(f'Querying repos (processed {repos_processed} repos so far)...')

        data = github_graphql(query, installation_token, variables=variables, timeout=30)

        user_data = data.get('user')
        if not user_data:
            break

        repositories = user_data.get('repositories', {})
        repos = repositories.get('nodes', [])

        # Process issues from each repository
        for repo in repos:
            repo_name = repo['name']
            owner_login = repo['owner']['login']
            repo_issues = repo.get('issues', {}).get('nodes', [])

            if repo_issues:
                logger.info(f'  {owner_login}/{repo_name}: {len(repo_issues)} issues')

            for issue in repo_issues:
                # Convert GraphQL response to REST API format for compatibility
                issue_data = {
                    'number': issue['number'],
                    'title': issue['title'],
                    'body': issue['body'],
                    'state': issue['state'].lower(),
                    'repository': {
                        'html_url': issue['repository']['homepageUrl'],
                        'url': issue['repository']['url'],
                    },
                    'html_url': issue['url'],
                    'created_at': issue['createdAt'],
                    'updated_at': issue['updatedAt'],
                    'labels': [
                        {
                            'name': label['name'],
                            'color': label['color']
                        }
                        for label in issue.get('labels', {}).get('nodes', [])
                    ],
                    'user': {
                        'login': issue.get('author', {}).get('login', '')
                    }
                }
                issues.append(issue_data)

        repos_processed += len(repos)

        # Update info about next page of query results (if any)
        page_info = repositories.get('pageInfo')

    return issues

def _github_app_installation_build_query_for_issue_urls(issue_urls):
    """
    Build a GitHub GraphQL query that gets the latest data for
    given issue URLs.
    """
    from urllib.parse import urlparse

    # Build a dictionary that groups issues by repo.
    repos = dict()
    for issue_url in issue_urls:
        url_path = urlparse(issue_url).path.strip('/')
        repo_url = '/'.join(url_path.split('/')[:-2])
        if repo_url not in repos:
            repos[repo_url] = []
        repos[repo_url].append(issue_url)

    # Monotonically-increasing indices for GraphQL aliases.
    repo_index = 0
    issue_index = 0

    query = """query {"""
    for (repo_url, issue_urls) in repos.items():
        path = urlparse(repo_url).path.strip('/')
        owner = path.split('/')[-2]
        repo_name = path.split('/')[-1]

        query += f"""
        repo{repo_index}: repository(owner: "{owner}", name: "{repo_name}") {{"""
        repo_index += 1

        for issue_url in issue_urls:
            path = urlparse(issue_url).path.strip('/')
            issue_number = path.split('/')[-1]

            query += f"""
            issue{issue_index}: issue(number: {issue_number}) {{"""
            query += """
                number
                title
                body
                repository {
                    homepageUrl
                    url
                }
                state
                url
                createdAt
                updatedAt
                labels(first: 30) {
                    nodes {
                        name
                        color
                    }
                }
                author {
                    login
                }
            }
            """
            issue_index += 1
        query += """
        }
        """
    query += """
    }
    """
    return query

def github_app_installation_query_issue_urls(installation_token, issue_urls):
    """
    Get latest issue data queries for GitHub issues that have received
    non-zero user funding on sponsoredissues.org.
    """
    from itertools import islice

    # Query in batches to avoid exceeding GitHub API limits.
    queries = []
    iterator = iter(issue_urls)
    while True:
        batch = list(islice(iterator, 100))
        if not batch:
            break
        query = _github_app_installation_build_query_for_issue_urls(batch)
        queries.append(query)

    issues = []
    for query in queries:
        try:
            data = github_graphql(query, installation_token, timeout=30)
        except requests.RequestException as e:
            logger.error(f'GraphQL request failed: {e}')
            continue

        for i in range(len(data)):
            repo = data.get(f'repo{i}')
            for j in range(len(repo)):
                issue = repo.get(f'issue{j}')
                # Convert GraphQL response to REST API format for compatibility
                issue_data = {
                    'number': issue['number'],
                    'title': issue['title'],
                    'body': issue['body'],
                    'repository': {
                        'html_url': issue['repository']['homepageUrl'],
                        'url': issue['repository']['url'],
                    },
                    'state': issue['state'].lower(),
                    'html_url': issue['url'],
                    'created_at': issue['createdAt'],
                    'updated_at': issue['updatedAt'],
                    'labels': [
                        {
                            'name': label['name'],
                            'color': label['color']
                        }
                        for label in issue.get('labels', {}).get('nodes', [])
                    ],
                    'user': {
                        'login': issue.get('author', {}).get('login', '')
                    }
                }
                issues.append(issue_data)

    return issues

def github_app_installation_query_repos(installation_token):
    data = github_api(f'/installation/repositories', installation_token)
    return data['repositories']

# Note: Added "Class" suffix to prevent name collision with
# `GitHubAppInstallation` in `models.py`.
class GitHubAppInstallationClass:
    access_token: Optional[str] = None
    installation_id: int
    installation_json: Optional[Dict[str, Any]] = None

    def __init__(self, installation_id: int, installation_json: Optional[Dict[str, Any]] = None):
        self.installation_id = installation_id
        self.installation_json = installation_json

    @classmethod
    def from_json(cls, installation_json):
        installation_id = int(installation_json['id'])
        return cls(installation_id, installation_json)

    @classmethod
    def from_id(cls, installation_id):
        return cls(installation_id)
