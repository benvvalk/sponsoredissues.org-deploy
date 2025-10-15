from decimal import Decimal
from django.shortcuts import get_object_or_404, redirect, render
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.db.models import Sum, Count
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from .models import GitHubIssue, SponsorAmount
from .github_service import GitHubSponsorService
import json

def calculate_trending_issues(limit=10):
    """
    Calculate trending issues using a hybrid approach that considers:
    1. Recent funding amount (last 14 days)
    2. Number of unique sponsors (last 14 days)
    3. Recency of last donation
    4. Only open issues

    Returns a list of trending issues with their details and trending score.
    """
    now = timezone.now()
    two_weeks_ago = now - timedelta(days=14)

    # Get all open issues with funding
    open_issues = GitHubIssue.objects.filter(
        data__state='open',
        sponsor_amounts__isnull=False
    ).distinct()

    trending_issues = []

    for issue in open_issues:
        # Get recent donations (last 14 days)
        recent_donations = issue.sponsor_amounts.filter(
            created_at__gte=two_weeks_ago
        )

        # Calculate recent funding amount and unique sponsor count
        recent_stats = recent_donations.aggregate(
            total_cents=Sum('cents_usd'),
            unique_sponsors=Count('sponsor_user', distinct=True)
        )

        recent_funding_cents = recent_stats['total_cents'] or 0
        unique_sponsor_count = recent_stats['unique_sponsors'] or 0

        # Get the most recent donation date for this issue
        latest_donation = issue.sponsor_amounts.order_by('-created_at').first()
        if latest_donation:
            days_since_last_donation = (now - latest_donation.created_at).days
        else:
            continue  # Skip if no donations

        # Calculate trending score
        # Formula: (recent_funding_cents * 1.0) + (unique_sponsors * 50) - (days_since_last_donation * 10)
        trending_score = (
            recent_funding_cents * 1.0 +
            unique_sponsor_count * 50 -
            days_since_last_donation * 10
        )

        # Get total all-time funding for display
        total_stats = issue.sponsor_amounts.aggregate(
            total_cents=Sum('cents_usd'),
            total_sponsors=Count('sponsor_user', distinct=True)
        )

        try:
            issue_data = issue.data
            trending_issues.append({
                'issue': issue,
                'title': issue_data.get('title', 'No title'),
                'number': issue_data.get('number', 0),
                'url': issue.url,
                'trending_score': trending_score,
                'recent_funding_cents': recent_funding_cents,
                'unique_sponsor_count': unique_sponsor_count,
                'total_funding_cents': total_stats['total_cents'] or 0,
                'total_sponsors': total_stats['total_sponsors'] or 0,
                'days_since_last_donation': days_since_last_donation,
            })
        except (json.JSONDecodeError, AttributeError):
            continue

    # Sort by trending score (descending) and limit to top N
    trending_issues.sort(key=lambda x: x['trending_score'], reverse=True)
    return trending_issues[:limit]

def index(request):
    # Calculate total funded amount across all issues
    total_funded_cents = SponsorAmount.objects.aggregate(
        total=Sum('cents_usd')
    )['total'] or 0

    # Get all issues that have been funded (have at least one SponsorAmount)
    funded_issues = GitHubIssue.objects.filter(
        sponsor_amounts__isnull=False
    ).distinct()

    # Count unique repositories that have received funding
    # Extract repo from URL pattern: https://github.com/{owner}/{repo}/issues/{number}
    funded_repos = set()
    for issue in funded_issues:
        url_parts = issue.url.split('/')
        if len(url_parts) >= 5:
            owner = url_parts[3]
            repo = url_parts[4]
            funded_repos.add(f"{owner}/{repo}")

    num_funded_repos = len(funded_repos)

    # Calculate resolved issues statistics
    # An issue is "resolved" if it's closed and has funding
    resolved_issues = funded_issues.filter(data__state='closed')
    num_resolved_issues = resolved_issues.count()

    # Calculate average funding for resolved issues
    if num_resolved_issues > 0:
        resolved_total_cents = 0
        for issue in resolved_issues:
            issue_total = issue.sponsor_amounts.aggregate(
                total=Sum('cents_usd')
            )['total'] or 0
            resolved_total_cents += issue_total
        avg_resolved_cents = resolved_total_cents // num_resolved_issues
    else:
        avg_resolved_cents = 0

    # Get trending issues
    trending_issues = calculate_trending_issues(limit=10)

    context = {
        'total_funded_cents': total_funded_cents,
        'num_funded_repos': num_funded_repos,
        'num_resolved_issues': num_resolved_issues,
        'avg_resolved_cents': avg_resolved_cents,
        'trending_issues': trending_issues,
    }

    return render(request, 'index.html', context)

def faq(request):
    return render(request, 'faq.html')

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
                sponsor_count=Count('id')
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
                'num_sponsors': donation_stats['sponsor_count'],
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

@login_required
@require_POST
def donate_to_issue(request, owner, repo, issue_number):
    donation_dollars_str = request.POST['donation_dollars']

    # Convert dollar value as string to cents as integer.  Use
    # "Banker's Rounding" if the dollar value has more than two
    # decimal places.
    donation_dollars = Decimal(donation_dollars_str).quantize(Decimal('1.00'))
    donation_cents = int(donation_dollars * 100)

    if donation_cents < 0:
        raise BadRequest("You tried to donate a negative amount")

    # Find the GitHub issue
    issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"
    github_issue = get_object_or_404(GitHubIssue, url=issue_url)

    # Get the previous amount that the user (sponsor) has allocated to
    # the target GitHub issue, if any.
    existing_donation = SponsorAmount.objects.filter(
        sponsor_user=request.user,
        target_github_issue=github_issue,
    ).first()

    donation_cents_old = existing_donation.cents_usd if existing_donation else 0

    # Ensure that the user (sponsor) cannot spend more money than
    # they than they have donated on GitHub Sponsors.
    #
    # Call `github_service.calculate_allocated_sponsor_cents` to
    # determine the total amount that the user (sponsor) has donated
    # to the developer on GitHub Sponsors, and also how much of that
    # money has already been allocated to other GitHub issues.
    github_service = GitHubSponsorService()
    (allocated_sponsor_cents, total_sponsor_cents) = github_service.calculate_allocated_sponsor_cents(request.user, owner)
    allocated_sponsor_cents -= donation_cents_old
    unallocated_sponsor_cents = total_sponsor_cents - allocated_sponsor_cents

    if donation_cents > unallocated_sponsor_cents:
        raise BadRequest("You tried to spend more than you've donated on GitHub Sponsors")

    if existing_donation:
        if donation_cents == 0:
            # Remove donation from database if amount == 0
            existing_donation.delete()
            messages.success(request, f"Removed your donation for {owner}/{repo}#{issue_number}.")
        else:
            # Update donation amount.
            existing_donation.cents_usd = donation_cents
            existing_donation.save()
            messages.success(request, f"Updated your amount for {owner}/{repo}#{issue_number} to {donation_dollars} USD.")
    elif donation_cents > 0:
        # Create new donation in database if amount > 0
        SponsorAmount.objects.create(
            cents_usd=donation_cents,
            sponsor_user=request.user,
            target_github_issue=github_issue,
        )
        messages.success(request, f"Updated your amount for {owner}/{repo}#{issue_number} to {donation_dollars} USD.")

    return redirect('repo_issues', owner, repo)
