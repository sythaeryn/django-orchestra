from django import forms
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers

from orchestra.forms import widgets

from .options import SoftwareService, SoftwareServiceForm

from .. import settings


class GitLabForm(SoftwareServiceForm):
    email = forms.EmailField(label=_("Email"),
        help_text=_("Initial email address, changes on the GitLab server are not reflected here."))


class GitLaChangeForm(GitLabForm):
    user_id = forms.IntegerField(label=("User ID"), widget=widgets.SpanWidget,
        help_text=_("ID of this user on the GitLab server, the only attribute that not changes."))


class GitLabSerializer(serializers.Serializer):
    email = serializers.EmailField(label=_("Email"))
    user_id = serializers.IntegerField(label=_("User ID"), required=False)


class GitLabService(SoftwareService):
    name = 'gitlab'
    form = GitLabForm
    change_form = GitLaChangeForm
    serializer = GitLabSerializer
    site_domain = settings.SAAS_GITLAB_DOMAIN
    change_readonly_fileds = ('email', 'user_id',)
    verbose_name = "GitLab"
    icon = 'orchestra/icons/apps/gitlab.png'

