"""
Open API support.
"""

import textwrap

from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema as drf_swagger_auto_schema

openapi_info = openapi.Info(
    title="Open edX API",
    default_version="v1",
    description="APIs for access to Open edX information",
    #terms_of_service="https://www.google.com/policies/terms/",         # TODO: Do we have these?
    contact=openapi.Contact(email="oscm@edx.org"),
    #license=openapi.License(name="BSD License"),                       # TODO: What does this mean?
)

schema_view = get_schema_view(
    openapi_info,
    public=True,
    permission_classes=(permissions.AllowAny,),
)


def dedent(text):
    """
    Dedent multiline text nicely.

    An initial empty line is ignored so that triple-quoted strings don't need
    to start with a backslash.
    """
    if "\n" in text:
        first, rest = text.split("\n", 1)
        if not first.strip():
            # First line is blank, discard it.
            text = rest
    return textwrap.dedent(text)


def swagger_auto_schema(**kwargs):
    """
    Decorator for documenting an OpenAPI endpoint.

    Identical to `drf_yasg.utils.swagger_auto_schema`__ except that
    description fields will be dedented properly.  All description fields
    should be in Markdown.

    __ https://drf-yasg.readthedocs.io/en/stable/drf_yasg.html#drf_yasg.utils.swagger_auto_schema

    """
    if 'operation_description' in kwargs:
        kwargs['operation_description'] = dedent(kwargs['operation_description'])
    for param in kwargs.get('manual_parameters', ()):
        param.description = dedent(param.description)
    return drf_swagger_auto_schema(**kwargs)
