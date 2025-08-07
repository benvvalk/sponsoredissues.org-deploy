from django.db import models

class GitHubIssue(models.Model):
    url = models.URLField(primary_key=True, max_length=500)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.url