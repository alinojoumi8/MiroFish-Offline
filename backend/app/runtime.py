"""Request-boundary hardening for the local Flask runtime."""

import hmac
import ipaddress
import math
import re
import uuid

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

from .utils.resource_safety import ResourceValidationError


MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
REQUEST_ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
STATUS_CODES = {
    400: 'bad_request',
    401: 'unauthorized',
    403: 'forbidden',
    404: 'not_found',
    405: 'method_not_allowed',
    409: 'conflict',
    413: 'payload_too_large',
    415: 'unsupported_media_type',
    422: 'validation_error',
    429: 'too_many_requests',
    503: 'dependency_unavailable',
}
SAFE_SERVER_MESSAGES = {
    500: 'An unexpected error occurred',
    503: 'A required local service is unavailable',
}


class RuntimeConfigurationError(ValueError):
    """Raised when network-facing runtime settings are unsafe or incomplete."""


def is_loopback_host(host):
    """Return whether a configured bind host is restricted to this machine."""
    normalized = str(host or '').strip().lower()
    if normalized == 'localhost':
        return True
    if normalized.startswith('[') and normalized.endswith(']'):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_runtime_settings(config):
    """Reject missing credentials and unsafe LAN configurations at startup."""
    errors = []
    for setting in ('LLM_API_KEY', 'NEO4J_URI', 'NEO4J_PASSWORD'):
        if not config.get(setting):
            errors.append(f'{setting} not configured')

    origins = config.get('MIROFISH_ALLOWED_ORIGINS') or []
    if not origins:
        errors.append('MIROFISH_ALLOWED_ORIGINS must not be empty')
    if '*' in origins:
        errors.append('MIROFISH_ALLOWED_ORIGINS must not contain wildcard origins')

    bind_host = config.get('MIROFISH_BIND_HOST', '127.0.0.1')
    if not is_loopback_host(bind_host) and not config.get('MIROFISH_CONTROL_TOKEN'):
        errors.append('MIROFISH_CONTROL_TOKEN is required for non-loopback binding')
    readiness_timeout = config.get('READINESS_TIMEOUT_SECONDS', 2.0)
    if (
        not isinstance(readiness_timeout, (int, float))
        or not math.isfinite(readiness_timeout)
        or readiness_timeout <= 0
    ):
        errors.append('READINESS_TIMEOUT_SECONDS must be finite and positive')
    readiness_disk = config.get('READINESS_MIN_FREE_DISK_BYTES', 0)
    if not isinstance(readiness_disk, int) or readiness_disk < 0:
        errors.append('READINESS_MIN_FREE_DISK_BYTES must be a nonnegative integer')
    readiness_ttl = config.get('READINESS_CACHE_TTL_SECONDS', 1.0)
    if (
        not isinstance(readiness_ttl, (int, float))
        or not math.isfinite(readiness_ttl)
        or readiness_ttl < 0
    ):
        errors.append('READINESS_CACHE_TTL_SECONDS must be finite and nonnegative')
    if errors:
        raise RuntimeConfigurationError('; '.join(errors))


def _request_id():
    return getattr(g, 'request_id', None) or str(uuid.uuid4())


def current_request_id():
    """Return the correlation ID assigned to the active request."""
    return _request_id()


def _error_response(status, code, message):
    return jsonify({
        'success': False,
        'error': {
            'code': code,
            'message': message,
            'request_id': _request_id(),
        },
    }), status


def _default_error_code(status):
    if status in STATUS_CODES:
        return STATUS_CODES[status]
    if 400 <= status < 500:
        return 'http_error'
    return 'internal_server_error'


def _default_error_message(status):
    if status in SAFE_SERVER_MESSAGES:
        return SAFE_SERVER_MESSAGES[status]
    return HTTPException().name if status < 500 else SAFE_SERVER_MESSAGES[500]


def _normalize_error_response(response, logger):
    status = response.status_code
    original_headers = list(response.headers.items())
    payload = response.get_json(silent=True) if response.is_json else None
    if (
        request.path == '/api/status'
        and status == 503
        and isinstance(payload, dict)
        and payload.get('status') == 'not_ready'
        and isinstance(payload.get('components'), dict)
    ):
        return response
    code = _default_error_code(status)
    message = None

    if isinstance(payload, dict):
        error = payload.get('error')
        if isinstance(error, dict):
            code = error.get('code') or code
            message = error.get('message')
        elif isinstance(error, str):
            message = error
        elif isinstance(payload.get('message'), str):
            message = payload['message']

    if status == 500 and isinstance(message, str) and message.startswith(
        'GraphStorage not initialized'
    ):
        status = 503
        code = STATUS_CODES[503]

    if status >= 500:
        if message and message not in SAFE_SERVER_MESSAGES.values():
            logger.error(
                'Caught server error request_id=%s detail=%s',
                _request_id(),
                message,
            )
        message = SAFE_SERVER_MESSAGES.get(status, SAFE_SERVER_MESSAGES[500])
    elif not message:
        message = _default_error_message(status)

    normalized, _ = _error_response(status, code, message)
    normalized.status_code = status
    for name, value in original_headers:
        if name.lower() not in {'content-type', 'content-length'}:
            normalized.headers.add(name, value)
    return normalized


def install_runtime_hardening(app, logger):
    """Install request IDs, LAN mutation protection, and stable JSON errors."""
    validate_runtime_settings(app.config)

    @app.before_request
    def prepare_request():
        supplied_request_id = request.headers.get('X-Request-ID', '')
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id):
            g.request_id = supplied_request_id
        else:
            g.request_id = str(uuid.uuid4())

        request_logger = app.extensions['request_logger']
        request_logger.debug(
            'Request: %s %s request_id=%s',
            request.method,
            request.path,
            g.request_id,
        )

        if (
            request.path.startswith('/api/')
            and request.method in MUTATING_METHODS
            and (
                not is_loopback_host(app.config.get('MIROFISH_BIND_HOST'))
                or not is_loopback_host(request.remote_addr)
            )
        ):
            provided = request.headers.get('X-MiroFish-Control-Token', '')
            configured = app.config.get('MIROFISH_CONTROL_TOKEN') or ''
            if not configured or not hmac.compare_digest(provided, configured):
                return _error_response(
                    403,
                    'control_token_required',
                    'A valid control token is required',
                )
        return None

    @app.errorhandler(ResourceValidationError)
    def handle_resource_validation(error):
        return _error_response(400, 'validation_error', str(error))

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        response, status = _error_response(
            error.code or 500,
            _default_error_code(error.code or 500),
            error.description,
        )
        for name, value in error.get_response().headers.items():
            if name.lower() not in {'content-type', 'content-length'}:
                response.headers.add(name, value)
        return response, status

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        logger.exception('Unhandled exception request_id=%s', _request_id())
        return _error_response(
            500,
            'internal_server_error',
            SAFE_SERVER_MESSAGES[500],
        )

    @app.after_request
    def finalize_response(response):
        if request.path.startswith('/api/') and response.status_code >= 400:
            response = _normalize_error_response(response, logger)
        response.headers['X-Request-ID'] = _request_id()
        app.extensions['request_logger'].debug(
            'Response: %s request_id=%s', response.status_code, _request_id()
        )
        return response
