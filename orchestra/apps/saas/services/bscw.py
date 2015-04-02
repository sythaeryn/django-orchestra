from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers

from .. import settings
from .options import SoftwareService, SoftwareServiceForm


class BSCWForm(SoftwareServiceForm):
    email = forms.EmailField(label=_("Email"), widget=forms.TextInput(attrs={'size':'40'}))


class BSCWDataSerializer(serializers.Serializer):
    email = serializers.EmailField(label=_("Email"))


class BSCWService(SoftwareService):
    name = 'bscw'
    verbose_name = "BSCW"
    form = BSCWForm
    serializer = BSCWDataSerializer
    icon = 'orchestra/icons/apps/BSCW.png'
    site_domain = settings.SAAS_BSCW_DOMAIN
    change_readonly_fileds = ('email',)
