from decimal import Decimal
from django.shortcuts import render
from django.conf import settings
from django.db.models import Sum, Count
from .models import GitHubIssue, SponsorAmount
from .github_service import GitHubSponsorService
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

            # Calculate donation amount and contributors for this issue
            donation_stats = issue.sponsor_amounts.aggregate(
                total_amount=Sum('amount'),
                contributor_count=Count('id')
            )

            # Amount that current user has donated to the issue (if any).
            user_amount = 0
            if request.user.is_authenticated:
                user_amount = issue.sponsor_amounts.filter(
                    sponsor_user=request.user
                ).aggregate(
                    total=Sum('amount')
                )['total'] or 0

            # Note: `or 0` is needed below because `Sum('amount')`
            # returns `None` when there are no `SponsorAmount` records for
            # the GitHub issue.
            parsed_issue = {
                'rank': len(parsed_issues) + 1,  # Simple ranking by order
                'title': issue_data.get('title', 'No title'),
                'number': issue_data.get('number', 0),
                'state': issue_data.get('state', 'open'),
                'labels': issue_data.get('labels', []),
                'url': issue.url,
                'donation_amount': donation_stats['total_amount'] or 0,
                'user_amount': user_amount,
                'contributors': donation_stats['contributor_count'],
            }
            parsed_issues.append(parsed_issue)
        except (json.JSONDecodeError, AttributeError):
            continue

    # Sort issues by donation amount in descending order
    parsed_issues.sort(key=lambda issue: issue['donation_amount'], reverse=True)

    # Update ranks after sorting
    for i, issue in enumerate(parsed_issues):
        issue['rank'] = i + 1

    # Calculate repository stats
    open_issues_count = sum(1 for issue in parsed_issues if issue['state'] == 'open')
    total_issues_count = len(parsed_issues)

    # Calculate sponsor dollars for current user and repo owner
    allocated_sponsor_dollars = 0  # Hard-coded as requested - will be updated in future iterations
    total_sponsor_dollars = 0

    if request.user.is_authenticated:
        github_service = GitHubSponsorService()
        (allocated_sponsor_cents, total_sponsor_cents) = github_service.calculate_allocated_sponsor_cents(request.user, owner)
        allocated_sponsor_dollars = allocated_sponsor_cents / Decimal(100)
        total_sponsor_dollars = total_sponsor_cents / Decimal(100)

    context = {
        'owner': owner,
        'repo': repo,
        'issues': parsed_issues,
        'allocated_sponsor_dollars': allocated_sponsor_dollars,
        'total_sponsor_dollars': total_sponsor_dollars,
    }

    return render(request, 'repo_issues.html', context)