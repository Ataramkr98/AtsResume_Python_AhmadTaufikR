"""Small pagination helpers shared by the server-rendered list views."""

from __future__ import annotations

from django.core.paginator import Paginator

DEFAULT_PER_PAGE = 20


def paginate(request, queryset, per_page: int = DEFAULT_PER_PAGE):
    """Return ``(page, querystring)`` for the current request.

    ``querystring`` is the existing filter set with ``page`` removed, so page links keep
    the active search and filters without duplicating the page parameter.
    """
    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()
