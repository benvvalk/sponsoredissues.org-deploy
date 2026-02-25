from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import pre_delete
from django.dispatch import receiver

class GitHubAppInstallationQuerySet(models.QuerySet):
    def delete(self):
        """
        Override the `delete()` method for `GitHubAppInstallation`
        query sets
        (e.g. `GitHubAppInstallation.objects.all().delete()`), so that
        unfunded issues are deleted but funded issues are preserved.

        When we delete an app installation, we preserve funded issues
        in a "frozen state" [1] because:

        (1) We don't want to throw away the work/data of users that
        already contributed to the GitHub issue(s).

        (2) Uninstalling/suspending the GitHub App was likely a
        mistake on the part of the maintainer. By preserving the
        issue(s) in a frozen state, we allow the maintainer to easily
        undo the mistake by reinstalling/unsuspending the app.

        Implementation notes:

        * In order for `delete()` to behave consistently for both
        query sets and model instances (e.g. `installation.delete()`),
        I needed to implement two `delete()` overrides: this method
        and `GitHubAppInstallation.delete()` (see below).

        * The tests for both `delete()` overrides are located in
        `sponsoredissues/tests/test_models.py`.

        * I tried to implement the desired `delete()` behaviour using
        just `on_delete` constraints (`on_delete=models.CASCADE`,
        `on_delete=models.PROTECT`, etc.), but I don't think it's
        possible. If a single issue deletion fails due to an
        `on_delete=models.PROTECT` constraint, the whole `delete()`
        operation query set is aborted and rolled
        back. `on_delete=models.RESTRICT` doesn't seem like the right
        thing either.

        * I also tried implementing my custom `delete()` behaviour
        using a `pre_delete` signal. That approach works fine and is
        simpler to implement. However, the downside is that number of
        deleted issues is not included in the count/dictionary that is
        returned by the main `delete()` call, and I think I'm going
        to need/want that information.

        [1]: When an issue is put in the "frozen" state, the issue
        continues to be listed on the maintainer's issues page, but
        the "Add or Remove Funds" button is disabled, and a warning is
        shown that explains why the issue was frozen and how the
        maintainer can unfreeze it.
        """
        # import here to avoid circular dependency
        from sponsoredissues.models import GitHubIssue

        # delete unfunded issues
        unfunded_issues_deleted, _ = GitHubIssue.objects.filter(
            repo__app_installation__in=self.all(),
            sponsor_amounts__isnull=True
        ).delete()

        # delete installations (and repos via `on_delete=models.CASCADE`)
        objects_deleted, deleted_by_model = super().delete()

        # add unfunded issues count to the `deleted_by_model` dictionary
        if unfunded_issues_deleted > 0:
            deleted_by_model['sponsoredissues.GitHubIssue'] = unfunded_issues_deleted
            objects_deleted += unfunded_issues_deleted

        return (objects_deleted, deleted_by_model)

class GitHubAppInstallationManager(models.Manager):
    """
    Custom manager for `GitHubAppInstallation`, which is used to
    implement a custom `delete()` operation for `GitHubAppInstallation`
    query sets.
    """
    def get_queryset(self):
        return GitHubAppInstallationQuerySet(self.model, using=self._db)

class GitHubAppInstallation(models.Model):
    """
    The set of installed and active installations for the
    `sponsoredissues-maintainer` GitHub App.

    We delete an app installation from this table if we learn
    (during a sync or webhook) that the owning GitHub account has
    uninstalled or suspended the app.
    """
    url = models.URLField(primary_key=True, max_length=500)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GitHubAppInstallationManager()

    def installation_id(self):
        """
        Return the GitHub App installation ID, by parsing it out of
        the installation URL.
        """
        return int(self.url.split('/')[-1])

    def delete(self, *args, **kwargs):
        """
        Override `GitHubAppInstallation.delete()` method so that it
        deletes unfunded issues associated with the app installation,
        but preserves any funded issues.

        For more details about what I'm doing here (and why), see the
        comments for `GitHubAppInstallationQuerySet.delete()` above,
        which is the corresponding method for query sets.
        """
        # import here to avoid circular dependency
        from sponsoredissues.models import GitHubIssue

        # delete unfunded issues
        unfunded_issues_deleted, _ = GitHubIssue.objects.filter(
            repo__app_installation=self,
            sponsor_amounts__isnull=True
        ).delete()

        # delete installations (and repos via `on_delete=models.CASCADE`)
        objects_deleted, deleted_by_model = super().delete(*args, **kwargs)

        # add unfunded issues count to the `deleted_by_model` dictionary
        if unfunded_issues_deleted > 0:
            deleted_by_model['sponsoredissues.GitHubIssue'] = unfunded_issues_deleted
            objects_deleted += unfunded_issues_deleted

        return (objects_deleted, deleted_by_model)

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

    @staticmethod
    def get_by_issue_url(issue_url: str):
        repo_url = '/'.join(issue_url.split('/')[:-2])
        return GitHubRepo.objects.filter(url=repo_url).first()

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

    @staticmethod
    def get_by_repo_url(repo_url):
        return GitHubIssue.objects.filter(url__startswith=f'{repo_url}/')

    def __str__(self):
        return self.url

    def delete_force(self):
        """
        Delete this issue from the database, along with its associated
        funding data (if any).

        Normally, attempting to delete an issue with funding data will
        throw an error due to the `on_delete=models.PROTECT`
        constraint on `IssueSponsorship.issue`. However,
        there are rare circumstances where we actually do want to
        delete an issue and all of its associated funding data (if
        any), e.g.  when the maintainer clicks the red `Delete issue`
        link in the bottom right corner of the GitHub issue page.
        """
        IssueSponsorship.objects.filter(issue=self).delete()
        self.delete()

    def is_funded(self):
        """
        Return true if this issue has a non-zero amount of funding
        from users.
        """
        return self.sponsor_amounts.exists()

class IssueSponsorship(models.Model):
    cents_usd = models.IntegerField()
    currency = models.CharField(max_length=3, default='USD')
    sponsor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sponsor_amounts')
    issue = models.ForeignKey(GitHubIssue, on_delete=models.PROTECT, related_name='sponsor_amounts')
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
        return f"{self.cents_usd} from {self.sponsor.username} for {self.issue.url}"
