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
                total_cents=Sum('cents_usd'),
                contributor_count=Count('id')
            )

            # Amount that current user has donated to the issue (if any).
            user_donation_cents = 0
            if request.user.is_authenticated:
                user_donation_cents = issue.sponsor_amounts.filter(
                    sponsor_user=request.user
                ).aggregate(
                    total=Sum('cents_usd')
                )['total'] or 0

            # Note: `or 0` is needed below because `Sum('cents_usd')`
            # returns `None` when there are no `SponsorAmount` records for
            # the GitHub issue.
            parsed_issue = {
                'rank': len(parsed_issues) + 1,  # Simple ranking by order
                'title': issue_data.get('title', 'No title'),
                'number': issue_data.get('number', 0),
                'state': issue_data.get('state', 'open'),
                'labels': issue_data.get('labels', []),
                'url': issue.url,
                'donation_total_cents': donation_stats['total_cents'] or 0,
                'user_donation_cents': user_donation_cents,
                'contributors': donation_stats['contributor_count'],
            }
            parsed_issues.append(parsed_issue)
        except (json.JSONDecodeError, AttributeError):
            continue

    # Sort issues by donation amount in descending order
    parsed_issues.sort(key=lambda issue: issue['donation_total_cents'], reverse=True)


    # Update ranks after sorting
    for i, issue in enumerate(parsed_issues):
        issue['rank'] = i + 1

    # Calculate repository stats
    open_issues_count = sum(1 for issue in parsed_issues if issue['state'] == 'open')
    total_issues_count = len(parsed_issues)

    # Calculate sponsor dollars for current user and repo owner
    total_sponsor_cents = 0
    allocated_sponsor_cents = 0
    unallocated_sponsor_cents = 0
    if request.user.is_authenticated:
        github_service = GitHubSponsorService()
        (allocated_sponsor_cents, total_sponsor_cents) = github_service.calculate_allocated_sponsor_cents(request.user, owner)
        unallocated_sponsor_cents = total_sponsor_cents - allocated_sponsor_cents

    context = {
        'owner': owner,
        'repo': repo,
        'issues': parsed_issues,
        'total_sponsor_cents': total_sponsor_cents,
        'allocated_sponsor_cents': allocated_sponsor_cents,
        'unallocated_sponsor_cents': unallocated_sponsor_cents,
    }

    return render(request, 'repo_issues.html', context)