"""
Middleware for Flask API.
"""

import logging
import time
from functools import wraps

from flask import jsonify, request

logger = logging.getLogger(__name__)


def log_request_middleware(app):
    """
    Add request logging middleware to Flask app.

    Args:
        app: Flask application instance
    """

    @app.before_request
    def before_request():
        """Log incoming requests."""
        request.start_time = time.time()
        logger.info(
            f"Request: {request.method} {request.path}",
            extra={
                "method": request.method,
                "path": request.path,
                "remote_addr": request.remote_addr,
                "user_agent": request.user_agent.string,
            },
        )

    @app.after_request
    def after_request(response):
        """Log request completion with timing."""
        if hasattr(request, "start_time"):
            duration_ms = (time.time() - request.start_time) * 1000
            logger.info(
                f"Response: {request.method} {request.path} - "
                f"{response.status_code} ({duration_ms:.2f}ms)",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        return response


def error_handler_middleware(app):
    """
    Add global error handlers to Flask app.

    Args:
        app: Flask application instance
    """

    @app.errorhandler(400)
    def bad_request(error):
        """Handle 400 Bad Request errors."""
        logger.warning(f"Bad request: {error}")
        return jsonify({"error": "Bad request", "message": str(error)}), 400

    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 Not Found errors."""
        logger.warning(f"Not found: {request.path}")
        return (
            jsonify(
                {"error": "Not found", "message": f"Endpoint {request.path} not found"}
            ),
            404,
        )

    @app.errorhandler(405)
    def method_not_allowed(error):
        """Handle 405 Method Not Allowed errors."""
        logger.warning(f"Method not allowed: {request.method} {request.path}")
        return (
            jsonify(
                {
                    "error": "Method not allowed",
                    "message": f"{request.method} not allowed for {request.path}",
                }
            ),
            405,
        )

    @app.errorhandler(500)
    def internal_server_error(error):
        """Handle 500 Internal Server Error."""
        logger.error(f"Internal server error: {error}", exc_info=True)
        return (
            jsonify(
                {
                    "error": "Internal server error",
                    "message": "An unexpected error occurred",
                }
            ),
            500,
        )

    @app.errorhandler(Exception)
    def handle_exception(error):
        """Handle uncaught exceptions."""
        logger.error(f"Unhandled exception: {error}", exc_info=True)
        return (
            jsonify(
                {
                    "error": "Internal server error",
                    "message": "An unexpected error occurred",
                }
            ),
            500,
        )


def cors_middleware(app):
    """
    Add CORS headers to all responses.

    Args:
        app: Flask application instance
    """

    @app.after_request
    def add_cors_headers(response):
        """Add CORS headers to response."""
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Max-Age"] = "3600"
        return response

    @app.route("/<path:path>", methods=["OPTIONS"])
    @app.route("/", methods=["OPTIONS"])
    def handle_options(path=None):
        """Handle OPTIONS preflight requests."""
        return "", 204


def rate_limit_decorator(max_requests: int = 100, window_seconds: int = 60):
    """
    Simple in-memory rate limiting decorator.

    Args:
        max_requests: Maximum requests per window
        window_seconds: Time window in seconds

    Returns:
        Decorator function

    Note:
        This is a basic implementation. For production, consider using
        Redis-based rate limiting (e.g., Flask-Limiter).
    """
    from collections import defaultdict
    from datetime import datetime, timedelta

    # Store: {ip: [(timestamp, count)]}
    request_counts = defaultdict(list)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get client IP
            client_ip = request.remote_addr

            # Clean old entries
            now = datetime.utcnow()
            cutoff = now - timedelta(seconds=window_seconds)
            request_counts[client_ip] = [
                (ts, count) for ts, count in request_counts[client_ip] if ts > cutoff
            ]

            # Count requests in window
            total_requests = sum(count for _, count in request_counts[client_ip])

            if total_requests >= max_requests:
                logger.warning(
                    f"Rate limit exceeded for {client_ip}: "
                    f"{total_requests} requests in {window_seconds}s"
                )
                return (
                    jsonify(
                        {
                            "error": "Rate limit exceeded",
                            "message": f"Maximum {max_requests} requests per {window_seconds} seconds",
                        }
                    ),
                    429,
                )

            # Add this request
            request_counts[client_ip].append((now, 1))

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def require_json(f):
    """
    Decorator to ensure request has JSON content type.

    Args:
        f: Function to decorate

    Returns:
        Decorated function
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not request.is_json:
            return jsonify({"error": "Content-Type must be application/json"}), 400
        return f(*args, **kwargs)

    return decorated_function


def validate_datetime_params(f):
    """
    Decorator to validate datetime query parameters.

    Args:
        f: Function to decorate

    Returns:
        Decorated function
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        from dateutil.parser import isoparse

        start_str = request.args.get("start")
        end_str = request.args.get("end")

        if start_str:
            try:
                isoparse(start_str)
            except ValueError as e:
                return (
                    jsonify(
                        {"error": "Invalid start datetime format", "message": str(e)}
                    ),
                    400,
                )

        if end_str:
            try:
                isoparse(end_str)
            except ValueError as e:
                return (
                    jsonify(
                        {"error": "Invalid end datetime format", "message": str(e)}
                    ),
                    400,
                )

        return f(*args, **kwargs)

    return decorated_function


def setup_middleware(app, config: dict):
    """
    Setup all middleware for Flask app.

    Args:
        app: Flask application instance
        config: Configuration dictionary
    """
    # Always add logging and error handling
    log_request_middleware(app)
    error_handler_middleware(app)

    # Add CORS if enabled
    if config.get("cors_enabled", False):
        cors_middleware(app)
        logger.info("CORS middleware enabled")

    logger.info("Middleware setup complete")
