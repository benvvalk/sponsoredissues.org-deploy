import logging

from django.contrib.auth import logout
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.conf import settings

from requests_oauthlib import OAuth2Session
from allauth.socialaccount.models import SocialToken
from allauth.socialaccount.providers.github.views import GitHubOAuth2Adapter

def github_autorefresh_token(get_response):
    """
    This is middleware that automatically refreshes a user's GitHub
    access token if it has expired.

    Note: This code is based on the example at [1]. The surrounding
    discussion is also helpful for understanding why this middleware
    is needed in the first place. (TLDR: `django-allauth` doesn't provide
    any functionality for refreshing OAuth tokens.)

    [1]: https://codeberg.org/allauth/django-allauth/issues/420#issuecomment-2295790
    """
    github_provider = settings.SOCIALACCOUNT_PROVIDERS['github']
    logger = logging.getLogger(f"{__name__}.{github_autorefresh_token.__name__}")

    def middleware(request):
        if not hasattr(request, 'user'):
            raise ImproperlyConfigured("github_autorefresh_token must be included in middlewares after django.contrib.auth.middleware.AuthenticationMiddleware")
        user = request.user
        # If website user is not signed in with GitHub, no need to do anything
        if not user.is_authenticated:
            return get_response(request)
        try:
            social_token = SocialToken.objects.get(account__user_id=user.id)
        except SocialToken.DoesNotExist:
            # We assume that `SOCIALACCOUNT_ONLY = True` and
            # `SOCIALACCOUNT_STORE_TOKENS = True`, which means that
            # there should always be an access token in the database
            # for the currently logged-in user.
            logger.exception("Failed to retrieve access_token for user")
            logout(request)
        if social_token.expires_at > timezone.now():
            return get_response(request)
        adapter = GitHubOAuth2Adapter(request)
        try:
            logger.debug("refreshing access token for %s", user)
            new_social_token = adapter.parse_token(
                OAuth2Session(
                    client_id=github_provider['APP']['client_id'],
                    token=dict(
                        access_token=social_token.token,
                        refresh_token=social_token.token_secret,
                        token_type="Bearer",
                    )
                ).refresh_token(
                    token_url=adapter.access_token_url,
                    client_id=github_provider['APP']['client_id'],
                    client_secret=github_provider['APP']['secret'],
                )
            )
            new_social_token.id = social_token.id  # replace the existing token instead of creating a new one
            new_social_token.app_id = social_token.app_id
            new_social_token.account_id = social_token.account_id
            new_social_token.save()
        except:
            logger.exception('Failed to refresh expired access_token')
            logout(request)
        return get_response(request)

    return middleware