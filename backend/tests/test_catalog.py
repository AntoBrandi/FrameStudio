"""M2.1: the catalog loads, refuses broken entries with readable messages,
validates requests, and agrees with the JSON Schema it publishes."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from framestudio.catalog import (
    CatalogError,
    ParamError,
    estimate_cost,
    load_catalog,
    to_json_schema,
    validate_params,
)
from framestudio.cli import main as cli

MOCK = yaml.safe_load(
    (Path(__file__).parents[1] / "framestudio/catalog/models/mock.yaml").read_text()
)

HOSTED: dict[str, Any] = {
    "id": "acme-video",
    "name": "Acme Video",
    "provider": "acme",
    "runtime": "api",
    "modes": ["video"],
    "capabilities": ["text_to_video", "image_to_video"],
    "params": {
        "resolution": {
            "type": "enum",
            "label": "Resolution",
            "values": ["480p", "720p", "1080p"],
            "default": "720p",
        },
        "duration": {"type": "integer", "label": "Duration", "min": 4, "max": 12, "default": 5},
    },
    "rules": [
        {
            "when": {"resolution": "1080p"},
            "allow": {"duration": [5, 10]},
            "reason": "1080p clips are 5 or 10 seconds.",
        }
    ],
    "pricing": {
        "unit": "video_second",
        "usd": 0.05,
        "by": "resolution",
        "table": {"480p": 0.02, "720p": 0.05, "1080p": 0.1},
        "source_url": "https://example.com/pricing",
        "checked_at": "2026-11-13",
    },
    "license": {
        "kind": "hosted_service",
        "name": "Acme terms of service",
        "commercial_use": "yes",
        "credit": "Acme",
        "source_url": "https://example.com/terms",
    },
    "fixtures": [{"mode": "video", "params": {"resolution": "1080p", "duration": 10}}],
}


def write(tmp_path: Path, *entries: dict[str, Any], name: str = "models.yaml") -> Path:
    (tmp_path / name).write_text(yaml.safe_dump(list(entries)))
    return tmp_path


def broken(tmp_path: Path, mutate: Any) -> str:
    entry = copy.deepcopy(HOSTED)
    mutate(entry)
    with pytest.raises(CatalogError) as caught:
        load_catalog(write(tmp_path, entry))
    return str(caught.value)


def test_the_shipped_catalog_loads() -> None:
    catalog = load_catalog()
    assert "mock" in catalog
    for model in catalog.all():
        assert model.license.credit  # every entry has a license record…
        assert model.fixtures  # …and at least one fixture request
        for fixture in model.fixtures:
            validate_params(model, fixture.mode, fixture.params)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda e: e.pop("license"), "acme-video › license: Field required"),
        (lambda e: e.update(fixtures=[]), "acme-video › fixtures"),
        (lambda e: e.pop("pricing"), "hosted models need 'pricing'"),
        (lambda e: e["params"]["resolution"].update(default="4k"), "default '4k' is not one of"),
        (lambda e: e["rules"][0]["when"].update(fps=24), "names unknown parameter 'fps'"),
        (
            lambda e: e["fixtures"][0]["params"].update(duration=12),
            "fixture for video is refused: 1080p clips are 5 or 10 seconds.",
        ),
        (lambda e: e.update(id="Acme Video"), "use lowercase letters"),
        (lambda e: e.update(colour="red"), "colour: Extra inputs are not permitted"),
        (
            lambda e: e["params"]["duration"].update(modes=["image"]),
            "for mode 'image' the model lacks",
        ),
    ],
)
def test_broken_entries_fail_with_a_readable_message(
    tmp_path: Path, mutate: Any, expected: str
) -> None:
    message = broken(tmp_path, mutate)
    assert "models.yaml" in message
    assert expected in message


def test_duplicate_ids_and_bad_yaml(tmp_path: Path) -> None:
    write(tmp_path, HOSTED, name="a.yaml")
    write(tmp_path, HOSTED, name="b.yaml")
    (tmp_path / "c.yaml").write_text("id: [unclosed")
    with pytest.raises(CatalogError) as caught:
        load_catalog(tmp_path)
    assert "b.yaml › acme-video: id already used in a.yaml" in str(caught.value)
    assert "c.yaml: not valid YAML" in str(caught.value)


def test_cli_check_reports_problems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    entry = copy.deepcopy(HOSTED)
    entry.pop("pricing")
    assert cli(["catalog", "--dir", str(write(tmp_path, entry))]) == 1
    assert "hosted models need 'pricing'" in capsys.readouterr().err
    assert cli(["catalog"]) == 0


def test_validation_fills_defaults_and_refuses_bad_requests() -> None:
    model = load_catalog().get("mock")
    assert model is not None
    assert validate_params(model, "video", {}) == {"aspect": "16:9", "duration": 3, "audio": True}
    assert validate_params(model, "image", {}) == {"aspect": "1:1"}
    with pytest.raises(ParamError) as caught:
        validate_params(model, "image", {"duration": 5, "aspect": "2:1", "latency_seconds": "fast"})
    assert caught.value.problems == [
        "'duration' is not a parameter of Mock engine",
        "Aspect ratio is not one of ['1:1', '16:9', '9:16', '4:3', '3:4', '21:9']",
        "Simulated latency must be a number",
    ]
    with pytest.raises(ParamError, match="at most 10 seconds"):
        validate_params(model, "video", {"aspect": "21:9", "duration": 20})


def test_json_schema_agrees_with_the_validator(tmp_path: Path) -> None:
    catalog = load_catalog(write(tmp_path, HOSTED, MOCK))
    for model in catalog.all():
        for fixture in model.fixtures:
            schema = to_json_schema(model, fixture.mode)
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(validate_params(model, fixture.mode, fixture.params), schema)
    hosted = catalog.get("acme-video")
    assert hosted is not None
    schema = to_json_schema(hosted, "video")
    bad = {"resolution": "1080p", "duration": 7}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    with pytest.raises(ParamError):
        validate_params(hosted, "video", bad)


def test_cost_estimates(tmp_path: Path) -> None:
    catalog = load_catalog(write(tmp_path, HOSTED, MOCK))
    hosted, mock = catalog.get("acme-video"), catalog.get("mock")
    assert hosted is not None and mock is not None
    assert estimate_cost(hosted, "video", {"resolution": "1080p", "duration": 10}) == 1.0
    assert estimate_cost(hosted, "video", {"resolution": "480p", "duration": 5}, batch=2) == 0.2
    assert estimate_cost(mock, "image", {}) is None


def test_models_endpoint(authed: TestClient) -> None:
    models = authed.get("/api/models").json()
    mock = next(m for m in models if m["id"] == "mock")
    assert mock["available"] is True
    assert mock["params"]["aspect"]["default_by_mode"] == {"video": "16:9"}
    assert set(mock["parameter_schema"]) == {"image", "video"}
    assert "fixtures" not in mock
    assert authed.get("/api/models/nope").status_code == 404


def test_hosted_model_without_a_connection_is_unavailable(tmp_path: Path, data_dir: Path) -> None:
    from framestudio.app import create_app
    from framestudio.config import Config

    from .conftest import AUTH, BASE_URL, TOKEN

    config = Config(data_dir=data_dir, token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, catalog=load_catalog(write(tmp_path, HOSTED, MOCK)))
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        hosted = client.get("/api/models/acme-video").json()
        assert hosted["available"] is False
        assert "Connect acme" in hosted["unavailable_reason"]
        refused = client.post("/api/generations", json={"mode": "video", "model": "acme-video"})
        assert refused.status_code == 422
        assert "not available" in refused.json()["detail"]


def test_generation_requests_are_validated(authed: TestClient) -> None:
    refused = authed.post(
        "/api/generations",
        json={"mode": "video", "params": {"aspect": "21:9", "duration": 20, "colour": "red"}},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == [
        "'colour' is not a parameter of Mock engine",
        "Widescreen mock clips are at most 10 seconds.",
    ]
    [ok] = authed.post(
        "/api/generations", json={"mode": "video", "params": {"latency_seconds": 0}}
    ).json()
    assert ok["params"] == {"aspect": "16:9", "duration": 3, "audio": True, "latency_seconds": 0}
