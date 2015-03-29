from django.conf import settings


# Pluggable backend for bill generation.
ORDERS_BILLING_BACKEND = getattr(settings, 'ORDERS_BILLING_BACKEND',
        'orchestra.apps.orders.billing.BillsBackend')


# Pluggable service class
ORDERS_SERVICE_MODEL = getattr(settings, 'ORDERS_SERVICE_MODEL', 'services.Service')


# Prevent inspecting these apps for service accounting
ORDERS_EXCLUDED_APPS = getattr(settings, 'ORDERS_EXCLUDED_APPS', (
    'orders',
    'admin',
    'contenttypes',
    'auth',
    'migrations',
    'sessions',
    'orchestration',
    'bills',
    'services',
))


# Only account for significative changes
# metric_storage new value: lastvalue*(1+threshold) > currentvalue or lastvalue*threshold < currentvalue
ORDERS_METRIC_THRESHOLD = getattr(settings, 'ORDERS_METRIC_THRESHOLD', 0.4)
