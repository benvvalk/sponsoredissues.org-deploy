from django.db import models
from django.contrib.auth.models import User

class GitHubAppInstallation(models.Model):
    """
    The set of installed and active installations for the
    `sponsoredissues-maintainer` GitHub App.

    We delete an app installation from this table if we learn
    (during a sync or webhook) that the owning GitHub account has
    uninstalled or suspended the app.
    """
    url = models.URLField(primary_key=True, max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class GitHubRepo(models.Model):
    """
    GitHub repos where the `sponsoredissues-maintainer` GitHub App is
    currently installed and active (i.e. not suspended).

    We delete a repo from this table if we learn (during a sync or
    webhook) that the owning GitHub account has disabled the app on
    the repo. If the app installation as a whole is uninstalled or
    suspended, then all associated repos will automatically removed
    from this table via `on_delete=models.CASCADE`.
    """
    url = models.URLField(primary_key=True, max_length=500)
    app_installation = models.ForeignKey(GitHubAppInstallation, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class GitHubIssue(models.Model):
    """
    GitHub issues that are currently shown on the maintainer's
    sponsored issue page (e.g. https://sponsoredissues.org/benvvalk),
    or issues that previously received funding but are now closed.

    A GitHub issue is only shown on a maintainer's sponsored issue
    page if all of the following are true:

    (1) The issue is open on GitHub.
    (2) The `sponsoredissues-maintainer` GitHub App is installed
    and active (not suspended) on the associated repo.
    (3) The issue has the `sponsoredissues.org` label on GitHub.

    We determine if (1), (2), and (3) are true by examining
    various fields in this model:

    (1) If the issue is open, the `state` field will be equal to
    `open` within `data`, where `data` is the JSON issue data
    retrieved from the GitHub API.
    (2) If the app is installed and active on the repo, the `repo`
    field will be non-null.
    (3) If the issue has the `sponsoredissues.org` label,
    `sponsoredissues.org` will be present in the `labels` array within
    `data`.

    When an issue is closed (i.e. (1) becomes false), the record for
    the issue is kept in this table if it has non-zero funding. This
    allows us to keep historical data about how much funding issues
    received, how long they took to resolve, etc..

    If the issue is open (i.e. (1) is true), but either (2) or
    (3) becomes false (usually due to maintainer error), the issue is
    put into a "frozen" state on the maintainer's sponsored issue
    page. In the frozen state, the "Add or Remove Funds" button is
    disabled and a warning message is displayed, explaining that the
    issue may have become out-of-sync with GitHub (e.g. mismatched
    open/closed state). The maintainer can easily fix the frozen state
    by reinstalling/unsuspending the app and/or re-adding the
    `sponsoredissues.org` label to the GitHub issue, as explained
    by [1] and [2] from the FAQ.

    [1]: http://sponsoredissues.org/site/faq#app-uninstalled
    [2]: http://sponsoredissues.org/site/faq#label-removed
    """
    url = models.URLField(primary_key=True, max_length=500)
    data = models.JSONField()
    repo = models.ForeignKey(GitHubRepo, null=True, on_delete=models.SET_NULL, related_name="issues")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'GitHub Issue'
        verbose_name_plural = 'GitHub Issues'

    def __str__(self):
        return self.url

    def is_funded(self):
        """
        Return true if this issue has a non-zero amount of funding
        from users.
        """
        return self.sponsor_amounts.exists()

class SponsorAmount(models.Model):
    cents_usd = models.IntegerField()
    currency = models.CharField(max_length=3, default='USD')
    sponsor_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sponsor_amounts')
    target_github_issue = models.ForeignKey(GitHubIssue, on_delete=models.CASCADE, related_name='sponsor_amounts')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Sponsor Amount'
        verbose_name_plural = 'Sponsor Amounts'
        # Don't allow zero amounts. We often want to get the subset
        # of issues that have non-zero funding, and this constraint
        # makes that query simpler and more efficient.
        constraints = [
            models.CheckConstraint(
                check=models.Q(cents_usd__gt=0),
                name='cents_usd_positive'
            )
        ]

    def __str__(self):
        return f"{self.cents_usd} from {self.sponsor_user.username} for {self.target_github_issue.url}"
