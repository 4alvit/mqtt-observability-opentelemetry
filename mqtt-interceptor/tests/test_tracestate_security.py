"""Regression coverage for GHSA-xcq7-p3pf-p88h on the inbound metadata path."""

from unittest.mock import patch

import pytest

from mqtt_interceptor.app import TraceContextPropagator

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.mark.parametrize(("padding", "retained"), [(0, True), (8180, True), (8192, False)])
def test_inbound_tracestate_bound(padding: int, retained: bool) -> None:
    """Reject oversized metadata before parsing while preserving its parent span."""
    state = "vendor=value" + " " * padding
    span = TraceContextPropagator().extract(
        None, [("traceparent", TRACEPARENT), ("tracestate", state)]
    )
    assert span is not None
    assert span.is_valid
    assert span.trace_id == int(TRACEPARENT.split("-")[1], 16)
    assert dict(span.trace_state.items()) == ({"vendor": "value"} if retained else {})


def test_oversized_state_never_reaches_parser() -> None:
    with patch("mqtt_interceptor.app.TraceState.from_header") as parse:
        span = TraceContextPropagator().extract(
            None, [("traceparent", TRACEPARENT), ("tracestate", "x" * 8193)]
        )
    parse.assert_not_called()
    assert span is not None
    assert span.is_valid
    assert not span.trace_state
