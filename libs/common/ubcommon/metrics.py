"""Prometheus metrics for HTTP (FastAPI) and gRPC servers.

Exposes the three numbers the dashboards are built on, per service:
request rate, error rate and latency.
"""

from __future__ import annotations

import time

import grpc
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "path", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency",
                         ["service", "method", "path"], buckets=_BUCKETS)
HTTP_IN_FLIGHT = Gauge("http_requests_in_flight", "HTTP requests being served", ["service"])

GRPC_REQUESTS = Counter("grpc_requests_total", "gRPC requests", ["service", "method", "code"])
GRPC_LATENCY = Histogram("grpc_request_duration_seconds", "gRPC request latency",
                         ["service", "method"], buckets=_BUCKETS)

_SKIP = {"/metrics", "/healthz", "/readyz"}


def instrument(app, service: str) -> None:
    """Add request metrics and a /metrics endpoint to a FastAPI app."""
    from fastapi import Request, Response

    @app.middleware("http")
    async def _metrics(request: Request, call_next):
        if request.url.path in _SKIP:
            return await call_next(request)
        start = time.perf_counter()
        HTTP_IN_FLIGHT.labels(service).inc()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            HTTP_IN_FLIGHT.labels(service).dec()
            route = request.scope.get("route")
            # The route template, not the raw URL, keeps label cardinality low.
            # A proxy can set request.state.metrics_path to name the upstream route.
            path = getattr(request.state, "metrics_path", None) or getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(service, request.method, path, str(status)).inc()
            HTTP_LATENCY.labels(service, request.method, path).observe(time.perf_counter() - start)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class GrpcMetricsInterceptor(grpc.ServerInterceptor):
    """Counts and times unary gRPC calls."""

    def __init__(self, service: str) -> None:
        self.service = service

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        method = handler_call_details.method.rsplit("/", 1)[-1]
        inner, service = handler.unary_unary, self.service

        def wrapper(request, context):
            start = time.perf_counter()
            code = "OK"
            try:
                return inner(request, context)
            except BaseException:
                state_code = context.code() if hasattr(context, "code") else None
                code = state_code.name if state_code else "UNKNOWN"
                raise
            finally:
                GRPC_REQUESTS.labels(service, method, code).inc()
                GRPC_LATENCY.labels(service, method).observe(time.perf_counter() - start)

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
