"""M2.3: outbound calls only to a provider's own hosts, only while it is on."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import httpx2
import pytest

from framestudio.net import OutboundBlocked, OutboundPolicy

pytestmark = pytest.mark.anyio

HOSTS = {"openai": frozenset({"api.openai.com"}), "mock-cloud": frozenset()}


class Recorder:
    def __init__(self, response: httpx2.Response | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.response = response

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.response is not None:
            return self.response
        if request.url.path == "/redirect":
            return httpx2.Response(302, headers={"location": "https://evil.example/steal"})
        return httpx2.Response(200, json={"ok": True})


def policy(recorder: Recorder, enabled: set[str]) -> OutboundPolicy:
    return OutboundPolicy(HOSTS, lambda p: p in enabled, httpx2.MockTransport(recorder))


async def test_allowed_host_goes_through() -> None:
    recorder = Recorder()
    async with policy(recorder, {"openai"}).client("openai") as client:
        response = await client.get("https://api.openai.com/v1/models")
    assert response.status_code == 200
    assert [r.url.host for r in recorder.requests] == ["api.openai.com"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/x",
        "http://api.openai.com/v1/models",
        "https://api.openai.com.evil.example/",
    ],
)
async def test_other_hosts_and_plain_http_are_blocked(url: str) -> None:
    recorder = Recorder()
    async with policy(recorder, {"openai"}).client("openai") as client:
        with pytest.raises(OutboundBlocked):
            await client.get(url)
    assert recorder.requests == []


async def test_a_turned_off_provider_sends_nothing() -> None:
    recorder = Recorder()
    rules = policy(recorder, set())
    async with rules.client("openai") as client:
        with pytest.raises(OutboundBlocked, match="turned off"):
            await client.get("https://api.openai.com/v1/models")
    async with rules.client("openai", require_enabled=False) as client:  # checking a new key
        assert (await client.get("https://api.openai.com/v1/models")).status_code == 200
    assert len(recorder.requests) == 1


async def test_redirects_are_checked_hop_by_hop() -> None:
    recorder = Recorder()
    async with policy(recorder, {"openai"}).client("openai", follow_redirects=True) as client:
        with pytest.raises(OutboundBlocked):
            await client.get("https://api.openai.com/redirect")
    assert [r.url.host for r in recorder.requests] == ["api.openai.com"]


async def test_a_provider_without_hosts_reaches_nothing() -> None:
    recorder = Recorder()
    async with policy(recorder, {"mock-cloud"}).client("mock-cloud") as client:
        with pytest.raises(OutboundBlocked):
            await client.get("https://api.openai.com/v1/models")


async def test_logs_leave_out_headers_and_query(caplog: pytest.LogCaptureFixture) -> None:
    recorder = Recorder()
    with caplog.at_level(logging.DEBUG, logger="framestudio"):
        async with policy(recorder, {"openai"}).client("openai") as client:
            await client.get(
                "https://api.openai.com/v1/models?key=QUERYSECRET",
                headers={"Authorization": "Bearer HEADERSECRET"},
            )
    assert "api.openai.com/v1/models" in caplog.text
    assert "QUERYSECRET" not in caplog.text and "HEADERSECRET" not in caplog.text


async def test_closing_a_client_keeps_the_shared_transport() -> None:
    recorder = Recorder()
    rules = policy(recorder, {"openai"})
    async with rules.client("openai") as client:
        await client.get("https://api.openai.com/a")
    async with rules.client("openai") as client:
        await client.get("https://api.openai.com/b")
    assert len(recorder.requests) == 2
    await rules.aclose()


SOURCE = Path(__file__).resolve().parents[1] / "framestudio"
#: HTTP clients may be built only around a transport (from OutboundPolicy).
CLIENTS = {
    "AsyncClient", "Client", "DefaultAsyncHttpxClient", "DefaultAsyncHttpx2Client",
    "DefaultHttpxClient", "DefaultHttpx2Client", "DefaultAioHttpClient",
}  # fmt: skip
#: Nothing outside the policy may create a network transport.
TRANSPORTS = {"AsyncHTTPTransport", "HTTPTransport"}
#: SDK clients must be handed an HTTP client (else they open their own).
SDK_CLIENTS = {"AsyncAnthropic": "http_client", "AsyncOpenAI": "http_client"}
BANNED_IMPORTS = {"requests", "aiohttp", "urllib.request", "urllib3", "http.client"}


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            found += [f"imports {m}" for m in modules if m in BANNED_IMPORTS]
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        owner = _name(node.func.value) if isinstance(node.func, ast.Attribute) else ""
        keywords = {k.arg for k in node.keywords}
        if name == "Client" and owner == "genai":
            if "http_options" not in keywords:
                found.append(f"creates genai.Client without http_options (line {node.lineno})")
        elif name in TRANSPORTS:
            found.append(f"creates {name} (line {node.lineno})")
        elif name in CLIENTS and "transport" not in keywords:
            found.append(f"creates {name} without a transport (line {node.lineno})")
        elif name in SDK_CLIENTS and SDK_CLIENTS[name] not in keywords:
            found.append(f"creates {name} without {SDK_CLIENTS[name]} (line {node.lineno})")
    return found


def test_only_the_policy_opens_connections() -> None:
    """Adapters get their transport from OutboundPolicy; nothing else dials out."""
    offenders = {
        str(path.relative_to(SOURCE)): problems
        for path in SOURCE.rglob("*.py")
        if path.name != "net.py" and (problems := _offences(path))
    }
    assert offenders == {}


def test_the_check_catches_a_bypass(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text(
        "import httpx2, anthropic\n"
        "a = httpx2.AsyncClient()\n"
        "b = anthropic.AsyncAnthropic(api_key='k')\n"
        "c = httpx2.AsyncHTTPTransport()\n"
        "d = httpx2.AsyncClient(transport=t)\n"
        "e = genai.Client(api_key='k')\n"
    )
    assert len(_offences(sample)) == 4
