"""M5.2: preview artwork for the cinema pickers. The script plans one
generation per choice (the manifest's subject with that single control), prices
the set, refuses a run above its budget, installs the first candidate of each
and lets another be picked; the API lists and serves what is installed."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.cinema import previews as pv
from framestudio.cinema import vocab
from framestudio.config import Config
from framestudio.media.tools import ffmpeg_path, ffprobe_path

from .conftest import AUTH, BASE_URL, TOKEN
from .contracts.byteplus_wire import FakeBytePlus
from .contracts.gemini_image_wire import FakeGemini

pytestmark = pytest.mark.skipif(not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg is required")


def test_the_plan_covers_every_choice() -> None:
    manifest = pv.load_manifest()
    jobs = pv.plan(manifest)
    words = vocab()
    counts = {f: sum(1 for j in jobs if j.family == f) for f in manifest.families}
    assert counts == {
        "genres": len(words.genres),
        "eras": len(words.eras),
        "tempos": len(words.tempos),
        "bodies": len(words.bodies),
        "lenses": len(words.lenses),
        "palettes": len(words.palettes),
        "lighting": len(words.lighting),
        "moves": len(words.moves),
    }
    noir = next(j for j in jobs if j.term == "noir")
    assert noir.shot.film.genre == "noir" and noir.model == "nano-banana" and noir.kind == "image"
    orbit = next(j for j in jobs if j.term == "orbit")
    assert orbit.shot.moves == ["orbit"] and orbit.kind == "video"
    assert orbit.params["resolution"] == "480p"
    with pytest.raises(ValueError, match="unknown family"):
        pv.plan(manifest, ["sounds"])


class Network:
    def __init__(self, gemini: FakeGemini, byteplus: FakeBytePlus) -> None:
        self.gemini, self.byteplus = gemini, byteplus

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host.endswith("bytepluses.com"):
            return self.byteplus(request)
        return self.gemini(request)


@pytest.fixture
def connected(tmp_path: Path) -> Iterator[TestClient]:
    gemini, byteplus = FakeGemini(), FakeBytePlus()
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, http_transport=httpx2.MockTransport(Network(gemini, byteplus)))
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        client.put("/api/connections/google", json={"api_key": gemini.key})
        client.put("/api/connections/byteplus", json={"api_key": byteplus.key})
        yield client


def test_the_full_set_is_priced(connected: TestClient) -> None:
    """The budget to agree before making the artwork: two candidates of every
    still on Nano Banana and of every clip on Seedance 2.0 Fast at 480p."""
    manifest = pv.load_manifest()
    jobs = pv.plan(manifest)
    total, problems = pv.estimate(connected, jobs, manifest.candidates)
    assert problems == []
    stills = sum(1 for j in jobs if j.kind == "image")
    clips = sum(1 for j in jobs if j.kind == "video")
    expected = stills * 2 * 0.039 + clips * 2 * 4 * 0.06
    assert total == pytest.approx(expected, abs=0.01)
    assert (stills, clips) == (42, 34) and total == pytest.approx(19.60, abs=0.01)


def offline_jobs(families: list[str]) -> list[pv.PreviewJob]:
    """The plan with the offline test double in place of the paid models."""
    manifest = pv.load_manifest().model_copy(
        update={"image_model": "mock-cloud", "video_model": "mock-cloud"}
    )
    jobs = pv.plan(manifest, families)
    for job in jobs:
        job.params = {"aspect": "1:1"} if job.kind == "image" else {"duration": 2}
    return jobs


def test_a_run_installs_the_first_candidate(authed: TestClient, tmp_path: Path) -> None:
    authed.patch("/api/settings", json={"mock_latency_seconds": 0})
    jobs = offline_jobs(["lighting"])
    with pytest.raises(pv.PreviewRunError, match=r"over the \$0\.00 budget"):
        pv.run(authed, jobs, candidates=2, budget_usd=0, out_dir=tmp_path, log=lambda _: None)
    made = pv.run(
        authed,
        jobs,
        candidates=2,
        budget_usd=1,
        out_dir=tmp_path,
        log=lambda _: None,
        poll_seconds=0.05,
    )
    assert sorted(p.name for p in made) == sorted(f"{t}.webp" for t in pv.terms("lighting"))
    assert (tmp_path / "lighting" / "window.2.webp").exists()
    assert pv.installed(tmp_path)["lighting"]["window"] == {"image": "window.webp"}
    # Another candidate can be picked.
    first = (tmp_path / "lighting" / "window.webp").read_bytes()
    pv.pick(tmp_path, "lighting", "window", 2)
    assert (tmp_path / "lighting" / "window.webp").read_bytes() == (
        tmp_path / "lighting" / "window.2.webp"
    ).read_bytes()
    assert first != b""
    with pytest.raises(pv.PreviewRunError, match="No candidate 9"):
        pv.pick(tmp_path, "lighting", "window", 9)


def test_clip_previews_get_a_poster(authed: TestClient, tmp_path: Path) -> None:
    authed.patch("/api/settings", json={"mock_latency_seconds": 0})
    jobs = [j for j in offline_jobs(["tempos"]) if j.term == "calm"]
    pv.run(
        authed,
        jobs,
        candidates=1,
        budget_usd=1,
        out_dir=tmp_path,
        log=lambda _: None,
        poll_seconds=0.05,
    )
    assert pv.installed(tmp_path)["tempos"]["calm"] == {"image": "calm.webp", "video": "calm.mp4"}


def test_the_api_lists_and_serves_installed_previews(
    authed: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pv, "PREVIEW_DIR", tmp_path)
    assert authed.get("/api/cinema/vocab").json()["previews"] == {}
    (tmp_path / "genres").mkdir()
    (tmp_path / "genres" / "noir.webp").write_bytes(b"RIFF....WEBP")
    (tmp_path / "genres" / "noir.2.webp").write_bytes(b"candidate")
    previews = authed.get("/api/cinema/vocab").json()["previews"]
    assert previews == {
        "genres": {"noir": {"image": "/api/cinema/previews/genres/noir.webp", "video": None}}
    }
    served = authed.get("/api/cinema/previews/genres/noir.webp")
    assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
    for bad in ("genres/noir.2.webp", "genres/..%2Fx.webp", "sounds/noir.webp", "genres/nope.webp"):
        assert authed.get(f"/api/cinema/previews/{bad}").status_code == 404
