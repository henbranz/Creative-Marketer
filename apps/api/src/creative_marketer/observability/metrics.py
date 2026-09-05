"""Reviewed low-cardinality metric contract for Phase 0."""

ALLOWED_METRIC_DIMENSIONS = frozenset(
    {
        "decision",
        "reason",
        "result",
        "risk",
        "state",
        "tool.key",
        "http.request.method",
        "url.route",
    }
)

OPERATIONAL_METRICS = frozenset(
    {
        "approval.decisions",
        "approval.requests",
        "approval.revocations",
        "http.server.requests",
        "idempotency.attempts",
        "idempotency.completions",
        "idempotency.reconciliations",
        "idempotency.reservations",
        "inbox.deliveries",
        "inbox.processing_delay",
        "outbox.publication_delay",
        "outbox.publication_results",
        "outbox.publish_attempts",
        "outbox.pending",
        "outbox.oldest_pending_age",
        "outbox.terminal_failures",
        "permission.decisions",
        "tool_gateway.duration",
        "tool_gateway.invocations",
        "tool_gateway.replays",
        "tool_gateway.unknown_outcomes",
    }
)
