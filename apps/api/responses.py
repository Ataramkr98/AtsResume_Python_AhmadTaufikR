"""Shared response envelope and pagination for the REST API.

Every successful response is wrapped as ``{"success": true, "message": "", "data": ...}`` and every
error is wrapped as ``{"success": false, "error": {...}}`` so a client can branch on a single key.
"""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

ENVELOPE_KEY = "success"


def envelope(data=None, *, message: str = "", status_code: int = 200) -> Response:
    """Build an enveloped success response for hand-written views."""
    return Response({"success": True, "message": message, "data": data}, status=status_code)


class EnvelopedMixin:
    """Wrap any successful payload produced by a generic DRF view.

    Implemented on ``finalize_response`` so it covers list, retrieve, create and update handlers
    without each view repeating the wrapping logic.
    """

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if (
            200 <= response.status_code < 300
            and response.status_code != 204
            and not (isinstance(response.data, dict) and ENVELOPE_KEY in response.data)
        ):
            response.data = {"success": True, "message": "", "data": response.data}
        return response


class EnvelopedAPIView(EnvelopedMixin):
    pass


class StandardPagination(PageNumberPagination):
    """Page-number pagination with a client-tunable size, used by every list endpoint."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
