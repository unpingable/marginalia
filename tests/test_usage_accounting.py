# SPDX-License-Identifier: Apache-2.0
"""Estimated and observed cost stay distinct, and malformed usage never raises."""

from __future__ import annotations

import pytest

from gov_webui.usage_accounting import (
    EstimatedUsage,
    ObservedUsage,
    RequestAccounting,
    observed_from_normalized_usage,
    observed_from_provider_usage,
    message_accounting,
)


def test_openrouter_usage_is_read_including_reasoning_and_cost() -> None:
    """Reasoning bills as completion tokens and must be visible separately."""
    observed = observed_from_provider_usage(
        {
            "prompt_tokens": 15937,
            "completion_tokens": 5526,
            "total_tokens": 21463,
            "cost": 0.0466262,
            "completion_tokens_details": {"reasoning_tokens": 5310, "audio_tokens": 0},
        }
    )
    assert observed.prompt_tokens == 15937
    assert observed.completion_tokens == 5526
    assert observed.reasoning_tokens == 5310
    assert observed.cost_usd == 0.0466262
    assert observed.reported is True


def test_legacy_normalized_usage_is_labelled_and_missing_is_unavailable() -> None:
    observed = observed_from_normalized_usage(
        {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    )
    assert observed.source == "normalized"
    assert observed.reasoning_tokens is None
    assert observed.cost_usd is None

    missing = observed_from_normalized_usage(
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    assert missing.source == "unavailable"
    assert missing.reported is False


def test_negative_provider_counts_are_unavailable() -> None:
    assert observed_from_provider_usage({"prompt_tokens": -1}).prompt_tokens is None
    assert observed_from_provider_usage({"cost": -0.1}).cost_usd is None


def test_absent_fields_are_none_and_never_zero() -> None:
    """A zero would claim a free request and corrupt any aggregate built on it."""
    observed = observed_from_provider_usage({"prompt_tokens": 10})
    assert observed.prompt_tokens == 10
    assert observed.completion_tokens is None
    assert observed.reasoning_tokens is None
    assert observed.cost_usd is None


def test_malformed_usage_never_raises() -> None:
    """Billing data is not a reason to fail a request that already succeeded."""
    for payload in (
        None,
        [],
        "usage",
        7,
        {"prompt_tokens": "many"},
        {"completion_tokens_details": 3},
    ):
        observed = observed_from_provider_usage(payload)
        assert observed.reported is False


def test_booleans_are_not_token_counts() -> None:
    assert observed_from_provider_usage({"prompt_tokens": True}).prompt_tokens is None


def test_float_token_counts_are_coerced_to_int() -> None:
    """Some gateways report counts as floats; the field contract says int."""
    observed = observed_from_provider_usage({"prompt_tokens": 1024.0})
    assert observed.prompt_tokens == 1024
    assert isinstance(observed.prompt_tokens, int)


def test_prompt_delta_makes_the_budget_model_measurable() -> None:
    """The gap between estimate and reality is the point of recording both."""
    accounting = RequestAccounting(
        provider_id="openrouter",
        model_id="z-ai/glm-5.3",
        estimated=EstimatedUsage(prompt_tokens=19625),
        observed=ObservedUsage(prompt_tokens=15937),
        latency_ms=55234.5,
    )
    assert accounting.prompt_delta == -3688

    payload = accounting.to_dict()
    # The two sources are never merged into one field.
    assert payload["estimated_prompt_tokens"] == 19625
    assert payload["observed_prompt_tokens"] == 15937
    assert set(payload) >= {"estimated_prompt_tokens", "observed_prompt_tokens", "prompt_delta"}


def test_prompt_delta_is_none_when_the_provider_reported_nothing() -> None:
    accounting = RequestAccounting(
        provider_id="ollama-local",
        model_id="orion",
        estimated=EstimatedUsage(prompt_tokens=100),
        observed=ObservedUsage(),
        latency_ms=1.0,
    )
    assert accounting.prompt_delta is None
    assert accounting.to_dict()["observed_prompt_tokens"] is None


# =============================================================================
# Liveness probes: a deleted model is a configuration fault, not a dead backend
# =============================================================================


def test_unknown_probe_model_is_configuration_not_liveness() -> None:
    """Health telemetry that is knowingly false is worse than none.

    A probe naming a model the catalog no longer contains returns 422. Recording
    that as an ordinary FAIL claims the backend is down when it was merely
    deleted — which made three of four production probes false for two days
    after a model cleanup.
    """
    import httpx

    from gov_webui.synthetic_worker import _failure_class

    request = httpx.Request("POST", "http://marginalia:8000/v1/internal/synthetic-governor")
    unknown_model = httpx.HTTPStatusError(
        "422", request=request, response=httpx.Response(422, request=request)
    )
    backend_down = httpx.HTTPStatusError(
        "502", request=request, response=httpx.Response(502, request=request)
    )
    generation_paused = httpx.HTTPStatusError(
        "423", request=request, response=httpx.Response(423, request=request)
    )

    assert _failure_class(unknown_model) == "configuration_error"
    assert _failure_class(generation_paused) == "generation_paused"
    assert _failure_class(backend_down) == "http_502"


@pytest.mark.asyncio
async def test_synthetic_probe_uses_the_declared_read_deadline(monkeypatch) -> None:
    import httpx

    from gov_webui import synthetic_worker

    observed = {}

    class FakeClient:
        def __init__(self, *, timeout):
            observed["read"] = timeout.read

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            request = httpx.Request("POST", "http://marginalia/probe")
            return httpx.Response(200, request=request, json={"status": "PASS", "receipt_id": "r"})

    monkeypatch.setattr(synthetic_worker.httpx, "AsyncClient", FakeClient)
    result = await synthetic_worker.probe_once(
        base_url="http://marginalia", model="orion", timeout_seconds=600
    )
    assert result["result"] == "PASS"
    assert observed["read"] == 600


@pytest.mark.asyncio
async def test_synthetic_probe_reports_operator_pause_without_backend_failure(
    monkeypatch,
) -> None:
    import httpx

    from gov_webui import synthetic_worker

    class PausedClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            request = httpx.Request("POST", "http://marginalia/probe")
            return httpx.Response(423, request=request)

    monkeypatch.setattr(synthetic_worker.httpx, "AsyncClient", PausedClient)

    result = await synthetic_worker.probe_once(
        base_url="http://marginalia", model="orion", timeout_seconds=10
    )

    assert result["result"] == "PAUSED"
    assert result["failure_class"] == "generation_paused"


def test_transport_failures_remain_liveness_failures() -> None:
    """The distinction must not blunt real outage detection."""
    import httpx

    from gov_webui.synthetic_worker import _failure_class

    request = httpx.Request("POST", "http://marginalia:8000/")
    assert _failure_class(httpx.ConnectTimeout("x", request=request)) == "connect_timeout"
    assert _failure_class(httpx.ReadTimeout("x", request=request)) == "read_timeout"


def test_each_scheduled_probe_window_has_a_fresh_durable_identity() -> None:
    from gov_webui.synthetic_worker import _scheduled_marker

    assert _scheduled_marker(1000) == "scheduled-1000"
    assert _scheduled_marker(1001) != _scheduled_marker(1000)


def test_accounting_without_an_estimate_still_records_cost() -> None:
    """An unbudgeted request has no estimate, but still has a bill."""
    accounting = RequestAccounting(
        provider_id="openrouter",
        model_id="z-ai/glm-5.3",
        estimated=None,
        observed=ObservedUsage(prompt_tokens=15937, cost_usd=0.0466262),
        latency_ms=55234.5,
    )
    payload = accounting.to_dict()
    assert payload["estimated_prompt_tokens"] is None
    assert payload["prompt_delta"] is None
    assert payload["observed_cost_usd"] == 0.0466262


def test_message_cost_is_estimated_only_from_explicit_rates() -> None:
    payload = message_accounting(
        provider_id="gateway",
        model_id="writer",
        usage={"prompt_tokens": 10_000, "completion_tokens": 2_000, "total_tokens": 12_000},
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=3.0,
    )
    assert payload["cost_status"] == "estimated"
    assert payload["cost_usd"] == 0.016
    assert payload["reported_total_tokens"] == 12_000


def test_local_provider_cost_is_known_zero_but_compute_is_not_priced() -> None:
    payload = message_accounting(
        provider_id="ollama",
        model_id="local",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        inference="local",
    )
    assert payload["cost_status"] == "known"
    assert payload["cost_usd"] == 0
    assert "electricity" in payload["cost_note"]


def test_unpriced_hosted_provider_cost_is_explicitly_unavailable() -> None:
    payload = message_accounting(
        provider_id="hosted",
        model_id="writer",
        usage={"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    )
    assert payload["cost_status"] == "unavailable"
    assert payload["cost_usd"] is None
