import re
import requests
from django.core.management.base import BaseCommand, CommandError
from sponsoredissues.models import GitHubIssue

class Command(BaseCommand):
    help = 'Add a GitHub issue to the database by fetching data from GitHub API'

    def add_arguments(self, parser):
        parser.add_argument('url', type=str, help='GitHub issue URL')
        parser.add_argument(
            '--force',
            action='store_true',
            help='Update existing issue if it already exists',
        )

    def handle(self, *args, **options):
        url = options['url']
        force = options['force']

        # Validate GitHub issue URL format
        github_pattern = r'https://github\.com/([^/]+)/([^/]+)/issues/(\d+)'
        match = re.match(github_pattern, url)

        if not match:
            raise CommandError(f'Invalid GitHub issue URL: {url}')

        owner, repo, issue_number = match.groups()

        # Check if issue already exists
        if GitHubIssue.objects.filter(url=url).exists():
            if not force:
                self.stdout.write(
                    self.style.WARNING(f'Issue already exists: {url}')
                )
                self.stdout.write('Use --force to update existing issue')
                return
            else:
                self.stdout.write(f'Updating existing issue: {url}')

        # Fetch issue data from GitHub API
        api_url = f'https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}'

        try:
            response = requests.get(api_url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise CommandError(f'Failed to fetch issue data: {e}')

        issue_data = response.json()

        # Verify this is actually an issue (not a pull request)
        if 'pull_request' in issue_data:
            raise CommandError(f'URL points to a pull request, not an issue: {url}')

        # Save or update the issue
        github_issue, created = GitHubIssue.objects.update_or_create(
            url=url,
            defaults={'data': issue_data}
        )

        if created:
            self.stdout.write(
                self.style.SUCCESS(f'Successfully added issue: {url}')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Successfully updated issue: {url}')
            )

        # Display basic issue info
        title = issue_data.get('title', 'No title')
        state = issue_data.get('state', 'unknown')
        self.stdout.write(f'Title: {title}')
        self.stdout.write(f'State: {state}')