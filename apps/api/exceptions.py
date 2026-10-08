from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
        response = Response(detail, status=400)
    else:
        response = exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    detail = response.data
    if isinstance(detail, dict) and "detail" in detail:
        message = str(detail["detail"])
        fields = {}
    elif isinstance(detail, dict):
        message = "The request could not be processed."
        fields = detail
    elif isinstance(detail, (list, tuple)):
        message = "; ".join(str(item) for item in detail)
        fields = {}
    else:
        message = str(detail)
        fields = {}
    response.data = {
        "success": False,
        "error": {
            "code": getattr(exc, "default_code", "validation_error"),
            "message": message,
            "fields": fields,
            "request_id": getattr(request, "request_id", ""),
        },
    }
    return response
