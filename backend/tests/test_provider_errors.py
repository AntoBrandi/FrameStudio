"""M2.2: one error taxonomy for every provider, and retries only where safe."""

from __future__ import annotations

import pytest

from framestudio.providers.base import ProviderError, classify_http, with_retries

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, "", "auth"),
        (403, "forbidden", "auth"),
        (400, '{"error": {"message": "API key not valid.", "status": "INVALID_ARGUMENT"}}', "auth"),
        (402, "", "quota"),
        (429, '{"error": "insufficient_quota: check your billing"}', "quota"),
        (429, "rate limited", "transient"),
        (408, "", "transient"),
        (500, "", "transient"),
        (503, "overloaded", "transient"),
        (404, "", "not_found"),
        (400, "The prompt was flagged by our safety system", "content_policy"),
        (422, "duration must be at most 12", "invalid_params"),
        (418, "teapot", "unknown"),
    ],
)
def test_classify_http(status: int, body: str, kind: str) -> None:
    error = classify_http(status, body)
    assert error.kind == kind
    assert error.status == status
    assert error.code == f"provider_{kind}"
    assert error.message


def test_classify_keeps_the_provider_reason_for_refused_settings() -> None:
    error = classify_http(400, "  duration must be at most 12  ")
    assert error.message == "duration must be at most 12"


def test_retry_after_is_read() -> None:
    assert classify_http(429, "", retry_after="7").retry_after == 7.0
    assert classify_http(429, "", retry_after="Wed, 21 Oct 2026 07:28:00 GMT").retry_after is None


def test_job_error_keeps_code_and_transience() -> None:
    error = ProviderError("transient", provider="x", code="rate_limited")
    job_error = error.to_job_error()
    assert (job_error.code, job_error.transient) == ("rate_limited", True)
    assert not ProviderError("auth").to_job_error().transient


class Flaky:
    def __init__(self, failures: list[ProviderError]) -> None:
        self.failures = failures
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


async def test_transient_errors_are_retried() -> None:
    call = Flaky([ProviderError("transient"), ProviderError("transient")])
    retries: list[int] = []
    result = await with_retries(call, base_delay=0, on_retry=lambda n, _e: retries.append(n))
    assert result == "ok"
    assert call.calls == 3
    assert retries == [1, 2]


async def test_other_errors_are_not_retried() -> None:
    call = Flaky([ProviderError("auth")])
    with pytest.raises(ProviderError) as caught:
        await with_retries(call, base_delay=0)
    assert caught.value.kind == "auth"
    assert call.calls == 1


async def test_retries_give_up() -> None:
    call = Flaky([ProviderError("transient")] * 5)
    with pytest.raises(ProviderError):
        await with_retries(call, attempts=3, base_delay=0)
    assert call.calls == 3


async def test_retry_after_overrides_backoff() -> None:
    call = Flaky([ProviderError("transient", retry_after=0.01)])
    assert await with_retries(call, base_delay=60) == "ok"
