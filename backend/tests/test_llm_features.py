"""M2.6: Enhance prompt, Describe image (captions and tags fed into search) and
name suggestions, on the scripted model."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.generation import GenerateRemoteJob
from framestudio.llm.base import ImagePart
from framestudio.llm.fake import FakeLLM, Turn
from framestudio.llm.registry import FAKE_MODELS
from framestudio.providers.base import ProviderError

from .conftest import AUTH, BASE_URL, TOKEN, wait_until


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)


def claude() -> FakeLLM:
    """The scripted model standing in for Anthropic, with its model names."""
    return FakeLLM(id="anthropic", models=FAKE_MODELS["anthropic"])


@pytest.fixture
def fake() -> FakeLLM:
    return claude()


@pytest.fixture
def client(tmp_path: Path, fake: FakeLLM) -> Iterator[TestClient]:  # overrides conftest's client
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, llms={"anthropic": fake})
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as test_client:
        yield test_client


def png(colour: tuple[int, int, int], size: tuple[int, int] = (1600, 900)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, name: str, colour: tuple[int, int, int], **form: str) -> str:
    response = client.post("/api/assets/upload", files=[("files", (name, png(colour)))], data=form)
    assert response.status_code == 200, response.text
    return response.json()[0]["asset"]["id"]  # type: ignore[no-any-return]


def describe_reply(caption: str, tags: list[str]) -> Turn:
    return Turn(text=json.dumps({"caption": caption, "tags": tags}))


def finished(client: TestClient, kind: str) -> list[dict[str, Any]]:
    return [
        job
        for job in client.get("/api/jobs").json()
        if job["kind"] == kind and job["state"] == "succeeded"
    ]


# ---------------------------------------------------------------- Enhance prompt


def test_enhance_returns_an_improved_prompt_and_changes(client: TestClient, fake: FakeLLM) -> None:
    fake.queue(
        Turn(
            text=json.dumps(
                {
                    "prompt": "A weathered red door at dusk, rain streaking the glass, "
                    "warm light from inside.",
                    "changes": ["Added the time of day", "Described the light"],
                }
            )
        )
    )
    response = client.post(
        "/api/llm/enhance", json={"prompt": "red door", "mode": "image", "model": "mock"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt"].startswith("A weathered red door")
    assert body["changes"] == ["Added the time of day", "Described the light"]
    assert body["llm"].startswith("anthropic/")
    [request] = fake.requests
    assert request.response_schema is not None and request.effort == "low"
    assert "red door" in request.messages[0].text
    assert "Mock engine" in request.messages[0].text  # the target model is named
    usage = client.get("/api/usage").json()["providers"]
    assert usage and usage[0]["provider"] == "anthropic"


def test_enhance_reports_a_refusal_and_missing_models(
    client: TestClient, fake: FakeLLM, tmp_path: Path
) -> None:
    fake.queue(Turn(stop_reason="refusal"))
    refused = client.post("/api/llm/enhance", json={"prompt": "something", "mode": "image"})
    assert refused.status_code == 422
    assert "declined" in refused.json()["detail"]
    config = Config(data_dir=tmp_path / "other", token=TOKEN, ui_dist=tmp_path / "ui")
    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as bare:
        missing = bare.post("/api/llm/enhance", json={"prompt": "something"})
    assert missing.status_code == 409
    assert "Connections" in missing.json()["detail"]


def test_enhance_needs_a_prompt(client: TestClient) -> None:
    assert client.post("/api/llm/enhance", json={"prompt": ""}).status_code == 422


# ---------------------------------------------------------------- Describe image


def test_described_images_can_be_found_by_what_they_show(client: TestClient, fake: FakeLLM) -> None:
    lighthouse = upload(client, "IMG_0042.png", (200, 120, 40))
    upload(client, "IMG_0043.png", (20, 40, 200))
    assert client.get("/api/assets", params={"q": "lighthouse"}).json()["items"] == []
    fake.queue(
        describe_reply(
            "A lighthouse on a rocky coast at dusk.", ["Lighthouse", "coast", "dusk", "dusk"]
        )
    )
    response = client.post("/api/assets/describe", json={"asset_ids": [lighthouse]})
    assert response.status_code == 202, response.text
    wait_until(lambda: finished(client, "describe"))
    asset = client.get(f"/api/assets/{lighthouse}").json()
    assert asset["caption"] == "A lighthouse on a rocky coast at dusk."
    assert asset["tags"] == ["lighthouse", "coast", "dusk"]
    found = client.get("/api/assets", params={"q": "rocky coast"}).json()["items"]
    assert [item["id"] for item in found] == [lighthouse]
    by_tag = client.get("/api/assets", params={"q": "dusk"}).json()["items"]
    assert [item["id"] for item in by_tag] == [lighthouse]
    # The model saw a downscaled JPEG, not the 1600 px original.
    [request] = fake.requests
    image = next(p for p in request.messages[0].parts if isinstance(p, ImagePart))
    assert image.mime == "image/jpeg"
    assert max(Image.open(io.BytesIO(image.data)).size) == 1024


def test_projects_that_opt_in_describe_new_assets(client: TestClient, fake: FakeLLM) -> None:
    project = client.post("/api/projects", json={"name": "Coast"}).json()
    other = client.post("/api/projects", json={"name": "Private"}).json()
    response = client.patch(f"/api/projects/{project['id']}", json={"describe_images": True})
    assert response.status_code == 200 and response.json()["describe_images"] is True
    fake.queue(describe_reply("A blue square.", ["blue"]))
    described = upload(client, "a.png", (10, 10, 200), project_id=project["id"])
    upload(client, "b.png", (10, 200, 10), project_id=other["id"])
    upload(client, "c.png", (200, 10, 10))  # the library outside projects: off by default
    wait_until(lambda: finished(client, "describe"))
    assert len(fake.requests) == 1
    assert client.get(f"/api/assets/{described}").json()["caption"] == "A blue square."


def test_the_library_setting_describes_generated_images(client: TestClient, fake: FakeLLM) -> None:
    client.patch("/api/settings", json={"describe_library": True})
    fake.queue(describe_reply("A placeholder card with text.", ["placeholder"]))
    generated = client.post(
        "/api/generations",
        json={"model": "mock", "prompt": "a card", "params": {"latency_seconds": 0}},
    ).json()[0]
    wait_until(lambda: finished(client, "describe"), timeout=20)
    done = client.get(f"/api/generations/{generated['id']}").json()
    assert done["asset"]["caption"] == "A placeholder card with text."


def test_a_failed_description_leaves_the_asset_alone(client: TestClient, fake: FakeLLM) -> None:
    asset_id = upload(client, "x.png", (1, 2, 3))
    fake.queue(Turn(error=ProviderError("auth", provider="anthropic")))
    client.post("/api/assets/describe", json={"asset_ids": [asset_id]})

    def failed() -> list[dict[str, Any]]:
        return [
            j
            for j in client.get("/api/jobs").json()
            if j["kind"] == "describe" and j["state"] == "failed"
        ]

    [job] = wait_until(failed)
    assert job["error_code"] == "provider_auth"
    assert client.get(f"/api/assets/{asset_id}").json()["caption"] is None


def test_describe_refuses_without_a_model_and_for_unknown_assets(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as bare:
        asset_id = upload(bare, "x.png", (1, 2, 3))
        assert bare.post("/api/assets/describe", json={"asset_ids": [asset_id]}).status_code == 409
    config2 = Config(data_dir=tmp_path / "d2", token=TOKEN, ui_dist=tmp_path / "ui")
    with TestClient(
        create_app(config2, llms={"anthropic": claude()}),
        base_url=BASE_URL,
        headers=AUTH,
    ) as c:
        assert c.post("/api/assets/describe", json={"asset_ids": ["nope"]}).status_code == 404


# ---------------------------------------------------------------- names


def test_name_suggestions_use_what_is_known(client: TestClient, fake: FakeLLM) -> None:
    asset_id = upload(client, "IMG_0001.png", (90, 90, 90))
    fake.queue(
        Turn(text=json.dumps({"names": ["Grey study", "Overcast slate", "Quiet grey", "extra"]}))
    )
    response = client.post(f"/api/assets/{asset_id}/name-suggestions")
    assert response.status_code == 200, response.text
    assert response.json() == {"names": ["Grey study", "Overcast slate", "Quiet grey"]}
    [request] = fake.requests
    # No caption or prompt yet: the picture itself goes along.
    assert any(isinstance(p, ImagePart) for p in request.messages[0].parts)
    assert client.post("/api/assets/nope/name-suggestions").status_code == 404


def test_offline_demo_gives_plausible_answers(tmp_path: Path) -> None:
    """FRAMESTUDIO_FAKE_PROVIDERS: the scripted model answers these features
    sensibly, so the whole flow can be tried without a key."""
    config = Config(
        data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui", fake_providers=True
    )
    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as demo:
        demo.put("/api/connections/anthropic", json={"api_key": "fs-test-ok-anthropic-123456"})
        body = demo.post("/api/llm/enhance", json={"prompt": "red door", "mode": "image"}).json()
        assert body["prompt"].startswith("red door") and len(body["prompt"]) > len("red door")
        assert body["changes"]
        asset_id = upload(demo, "x.png", (200, 10, 10))
        demo.post("/api/assets/describe", json={"asset_ids": [asset_id]})
        wait_until(lambda: finished(demo, "describe"))
        assert demo.get(f"/api/assets/{asset_id}").json()["caption"]
