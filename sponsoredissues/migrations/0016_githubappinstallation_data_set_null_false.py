from django.db import migrations, models

def github_app_installation_data_replace_nulls(apps, schema_editor):
    """
    Fill in any NULL value for `GitHubAppInstallation.data` with the
    correct JSON data, so we set `null=False` for
    `GitHubAppInstallation.data`.
    """
    from sponsoredissues.github_app import github_app_installation_query_json

    GitHubAppInstallation = apps.get_model('sponsoredissues', 'GitHubAppInstallation')
    installations_with_null_data = GitHubAppInstallation.objects.filter(data__isnull=True)

    for installation in installations_with_null_data:
        installation_id = installation.url.split('/')[-1]
        installation.data = github_app_installation_query_json(installation_id)
        installation.save()
    
    print(f"Success: Fixed {len(installations_with_null_data)} NULL values for `GitHubAppInstallation.data`")

class Migration(migrations.Migration):

    dependencies = [
        ('sponsoredissues', '0015_githubappinstallation_data'),
    ]

    operations = [
        # Note: `reverse_code` is a no-op here because it does no harm
        # to leave the newly-generated `data` values in place when
        # reversing the migration.
        migrations.RunPython(
            github_app_installation_data_replace_nulls,
            reverse_code=migrations.RunPython.noop),
        # Remove `null=True` from field definition
        migrations.AlterField(
            model_name='githubappinstallation',
            name='data',
            field=models.JSONField() 
        )
    ]
