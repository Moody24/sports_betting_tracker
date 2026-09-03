"""Safe, negotiated HTTP error responses for browser and API clients."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flask import g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorDefinition:
    code: str
    title: str
    message: str
    retry_guidance: str | None = None


ERRORS = {
    400: ErrorDefinition('bad_request', 'Request not understood', 'Check the submitted values and try again.'),
    401: ErrorDefinition('authentication_required', 'Sign in required', 'Sign in to continue to this page.'),
    403: ErrorDefinition('forbidden', 'Access denied', 'You do not have permission to perform this action.'),
    404: ErrorDefinition('not_found', 'Page not found', 'The address may be incorrect or the page may have moved.'),
    405: ErrorDefinition('method_not_allowed', 'Action not allowed', 'This address does not accept that type of request.'),
    429: ErrorDefinition('rate_limited', 'Too many requests', 'Please slow down before trying again.', 'Wait for the retry window shown by the server.'),
    500: ErrorDefinition('internal_error', 'Something went wrong', 'The request could not be completed safely.', 'Try again in a moment.'),
    502: ErrorDefinition('upstream_error', 'Data provider unavailable', 'A required data provider returned an invalid response.', 'Refresh after the provider recovers.'),
    503: ErrorDefinition('service_unavailable', 'Service temporarily unavailable', 'A required service is not ready.', 'Try again shortly.'),
    505: ErrorDefinition('http_version_unsupported', 'Browser protocol unsupported', 'This HTTP protocol version is not supported.'),
}


def _wants_json() -> bool:
    """Prefer JSON only when the request explicitly behaves like an API call."""
    if request.is_json:
        return True
    best = request.accept_mimetypes.best_match(['text/html', 'application/json'])
    return best == 'application/json' and (
        request.accept_mimetypes['application/json']
        > request.accept_mimetypes['text/html']
    )


def _definition(status: int) -> ErrorDefinition:
    return ERRORS.get(status, ERRORS[500])


def _retry_after(error: Exception) -> str | None:
    if not isinstance(error, HTTPException):
        return None
    return error.get_response().headers.get('Retry-After')


def register_error_handlers(app, database) -> None:
    """Register the shared HTML/JSON error contract for supported statuses."""

    def handle(error):
        status = int(getattr(error, 'code', None) or 500)
        definition = _definition(status)
        request_id = getattr(g, 'request_id', '')
        retry_after = _retry_after(error)

        if status >= 500:
            database.session.rollback()
            logger.error(
                'http_error status=%s code=%s request_id=%s',
                status,
                definition.code,
                request_id,
            )

        if _wants_json():
            response = jsonify(
                version='ApiErrorV1',
                code=definition.code,
                message=definition.message,
                request_id=request_id,
                details={},
            )
        else:
            response = render_template(
                'errors/error.html',
                error_status=status,
                error_title=definition.title,
                error_message=definition.message,
                error_request_id=request_id,
                error_retry_guidance=definition.retry_guidance,
            )
        response = app.make_response((response, status))
        if retry_after:
            response.headers['Retry-After'] = retry_after
        return response

    for status in ERRORS:
        app.register_error_handler(status, handle)
