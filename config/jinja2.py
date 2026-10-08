from django.contrib.messages import get_messages
from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment


def url(viewname, *args, **kwargs):
    return reverse(viewname, args=args or None, kwargs=kwargs or None)


def environment(**options):
    env = Environment(**options)
    env.globals.update({"url": url, "static": static, "get_messages": get_messages})
    return env
