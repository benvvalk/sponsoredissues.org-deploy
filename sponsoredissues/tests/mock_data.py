from typing import Final


class MockData:
    APP_INSTALLATION_TOKEN : Final = 'dummy-token'
    DEFAULT_USER_ID : Final[int] = 1234
    DEFAULT_USER_NAME : Final = 'test-user'
    DEFAULT_REPO_NAME : Final = 'test-repo'

    @staticmethod
    def user_json(
            user_id:int = DEFAULT_USER_ID,
            user_name = DEFAULT_USER_NAME
    ):
        return {
            'login': user_name,
            'id': user_id,
            'html_url': f'https://github.com/{user_name}'
        }

    @staticmethod
    def installation_json(
        installation_id=1111,
        user_id=DEFAULT_USER_ID,
        user_name=DEFAULT_USER_NAME,
        suspended_at=None,
    ):
        json = {
            'id': installation_id,
            'account': MockData.user_json(user_id, user_name),
            'html_url': f'https://github.com/settings/installations/{installation_id}'
        }

        if suspended_at:
            json['suspended_at'] = suspended_at

        return json

    @staticmethod
    def repo_json(
        user_name=DEFAULT_USER_NAME,
        repo_name=DEFAULT_REPO_NAME,
        private=False
    ):
        return {
            'name': f'{repo_name}',
            'html_url': f'https://github.com/{user_name}/{repo_name}',
            'private': private
        }

    @staticmethod
    def issue_json(
        repo_name=DEFAULT_REPO_NAME,
        issue_number=1,
        issue_state='open',
        user_id=DEFAULT_USER_ID,
        user_name=DEFAULT_USER_NAME
    ):
        return {
            'number': issue_number,
            'title': 'Test Issue',
            'body': 'Test body',
            'state': issue_state,
            'html_url': f'https://github.com/{user_name}/{repo_name}/issues/{issue_number}',
            'created_at': '2024-01-01T00:00:00Z',
            'updated_at': '2024-01-01T00:00:00Z',
            'labels': [
                {'name': 'sponsoredissues.org', 'color': '000000'}
            ],
            'user': MockData.user_json(user_id, user_name),
            'repository' : {
                'html_url': f'https://github.com/{user_name}/{repo_name}'
            }
        }
