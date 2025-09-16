from django.db import models

class GitHubIssue(models.Model):
    url = models.URLField(primary_key=True, max_length=500)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'GitHub Issue'
        verbose_name_plural = 'GitHub Issues'

    def __str__(self):
        return self.url

class Donation(models.Model):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    target_github_issue = models.ForeignKey(GitHubIssue, on_delete=models.CASCADE, related_name='donations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Donation'
        verbose_name_plural = 'Donations'

    def __str__(self):
        return f"{self.currency} {self.amount} for {self.target_github_issue.url}"
