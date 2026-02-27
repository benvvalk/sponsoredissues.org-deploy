from django.db import migrations, models

def github_app_installation_maintainer_replace_nulls(apps, schema_editor):
    """
    Populate any NULL values for `GitHubAppInstallation.maintainer`,
    so we can set `null=False` for `GitHubAppInstallation.maintainer`.
    """
    from sponsoredissues.github_sync import github_sync_app_installation

    GitHubAppInstallation = apps.get_model('sponsoredissues', 'GitHubAppInstallation')
    installations_with_null_maintainer = GitHubAppInstallation.objects.filter(maintainer__isnull=True)

    for installation in installations_with_null_maintainer:
        installation_id = installation.url.split('/')[-1]
        github_sync_app_installation(installation_id)
    
    print(f"Success: Fixed {len(installations_with_null_maintainer)} NULL values for `GitHubAppInstallation.maintainer`")

class Migration(migrations.Migration):

    dependencies = [
        ('sponsoredissues', '0021_githubappinstallation_maintainer'),
    ]

    operations = [
        # Note: `reverse_code` is a no-op here because it does no harm
        # to leave the newly-generated `maintainer` values in place when
        # reversing the migration.
        migrations.RunPython(
            github_app_installation_maintainer_replace_nulls,
            reverse_code=migrations.RunPython.noop),
        # Remove `null=True` from field definition
        migrations.AlterField(
            model_name='githubappinstallation',
            name='maintainer',
            field=models.ForeignKey(on_delete=models.CASCADE, to='sponsoredissues.maintainer')
        )
    ]
