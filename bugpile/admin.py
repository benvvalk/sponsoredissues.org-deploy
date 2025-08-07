from django.contrib import admin
from .models import GitHubIssue

@admin.register(GitHubIssue)
class GitHubIssueAdmin(admin.ModelAdmin):
    list_display = ('url', 'created_at', 'updated_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('url',)
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)