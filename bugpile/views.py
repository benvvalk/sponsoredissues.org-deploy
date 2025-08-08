from django.shortcuts import render
from .models import GitHubIssue
import json

def index(request):
    return render(request, 'index.html')

def repo_issues(request, owner, repo):
    # Filter issues for this repository
    repo_url_pattern = f"https://github.com/{owner}/{repo}"
    issues = GitHubIssue.objects.filter(url__contains=repo_url_pattern)

    # Parse issue data
    parsed_issues = []
    for issue in issues:
        try:
            issue_data = issue.data
            parsed_issue = {
                'rank': len(parsed_issues) + 1,  # Simple ranking by order
                'title': issue_data.get('title', 'No title'),
                'number': issue_data.get('number', 0),
                'state': issue_data.get('state', 'open'),
                'labels': issue_data.get('labels', []),
                'url': issue.url,
                'donation_amount': 0,  # Hard-coded for now
                'contributors': 0,     # Hard-coded for now
            }
            parsed_issues.append(parsed_issue)
        except (json.JSONDecodeError, AttributeError):
            continue

    # Calculate repository stats
    open_issues_count = sum(1 for issue in parsed_issues if issue['state'] == 'open')
    total_issues_count = len(parsed_issues)

    context = {
        'owner': owner,
        'repo': repo,
        'issues': parsed_issues,
        'open_issues_count': open_issues_count,
        'total_issues_count': total_issues_count,
        'stars': 0,          # Hard-coded as requested
        'forks': 0,          # Hard-coded as requested
        'total_funding': 0,  # Hard-coded as requested
    }

    return render(request, 'repo_issues.html', context)