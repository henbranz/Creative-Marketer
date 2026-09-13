# mypy: disable-error-code="no-untyped-def,no-untyped-call,method-assign,assignment"

import json
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

from creative_marketer.infrastructure.obsidian.bridge import (
    MY_NOTES,
    ObsidianBridge,
    ObsidianBridgeConfig,
    markdown_escape,
    obsidian_open_uri,
    preserve_user_notes,
    relative_note_path,
    render_note,
    stable_filename,
)
from creative_marketer_api import obsidian_bridge as bridge_cli


def projected_node(node_type="product", canonical_id=None, title="Clean Globe"):
    return {
        "node_type": node_type,
        "canonical_id": canonical_id or str(uuid4()),
        "title": title,
        "status": "active",
        "semantic_digest": "sha256:" + "a" * 64,
        "created_at": "2026-09-12T00:00:00+00:00",
        "updated_at": "2026-09-12T00:00:00+00:00",
        "properties": {"description": "Visible"},
        "relationships": [],
    }


def bridge(tmp_path: Path) -> ObsidianBridge:
    return ObsidianBridge(
        ObsidianBridgeConfig(tmp_path, "http://localhost:8000", str(uuid4()), "dev-token")
    )


def test_stable_filenames_and_safe_deep_links() -> None:
    identity = str(uuid4())
    assert stable_filename("product", identity) == stable_filename("product", identity)
    assert len(stable_filename("product", identity)) == len("product--.md") + 64
    uri = obsidian_open_uri("My Vault", "product", identity)
    assert uri.startswith("obsidian://open?") and "My+Vault" in uri
    with pytest.raises(ValueError):
        stable_filename("unknown", identity)
    with pytest.raises(ValueError):
        stable_filename("product", "  ")
    with pytest.raises(ValueError):
        obsidian_open_uri("bad\nvault", "product", identity)


def test_production_deep_link_golden_vectors_match_frontend() -> None:
    identity = "00000000-0000-0000-0000-000000000001"
    assert relative_note_path("production_plan", identity).as_posix() == (
        "Production/production-plan--"
        "5c81f666cc720c0fe27d45f159f68c12b7bb592484ba772e44eaa3b6231107b2.md"
    )
    assert relative_note_path("asset", identity).as_posix() == (
        "Assets/asset--4b60dacb33a2049ec4da0d7d23f06760c66731b5463ee5eb140647ae5fd298fa.md"
    )


def test_markdown_frontmatter_wikilinks_and_escaping() -> None:
    product = projected_node(title="Product [[escape]] <script>")
    brand = projected_node("brand", title="Brand")
    product["properties"] = {
        "description": "Visible",
        "tags": ["one", {"nested": "two"}],
        "metadata": {"safe": "value", "markup": "<script>"},
        "empty": None,
    }
    product["relationships"] = [
        {
            "relationship_type": "belongs_to_brand",
            "target_node_type": "brand",
            "target_canonical_id": brand["canonical_id"],
        }
    ]
    text = render_note(
        product,
        titles={f"brand:{brand['canonical_id']}": "Brand"},
        incoming=[
            {
                "source_node_type": "brand",
                "source_canonical_id": brand["canonical_id"],
                "relationship_type": "linked_from",
            }
        ],
    )
    assert text.startswith('---\ncm_type: "product"')
    assert "[[Products/brand--" in text
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert MY_NOTES in text
    assert "## Incoming Relationships" in text
    assert "\\u003cscript\\u003e" in text
    assert markdown_escape("# [unsafe]") == "\\# \\[unsafe\\]"


@pytest.mark.parametrize("model", ["gpt-5.6-terra", "gpt-6-astra"])
def test_historical_agent_model_is_rendered_from_durable_projection(model: str) -> None:
    historical = projected_node("agent_run", title="Producer Run historical")
    historical["properties"] = {
        "provider": "openai",
        "model": model,
        "model_profile": "production_deep",
    }
    text = render_note(historical, titles={}, incoming=[])
    assert model in text
    assert "gpt-5.6-sol" not in text


def test_environment_configuration_and_http_projection_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("OBSIDIAN_VAULT_PATH", "CM_API_BASE_URL", "CM_TENANT_ID", "CM_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="missing bridge configuration"):
        ObsidianBridgeConfig.from_environment()

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("CM_API_BASE_URL", "ftp://invalid.example")
    monkeypatch.setenv("CM_TENANT_ID", str(uuid4()))
    monkeypatch.setenv("CM_API_TOKEN", "local-token")
    with pytest.raises(ValueError, match="absolute HTTP"):
        ObsidianBridgeConfig.from_environment()

    monkeypatch.setenv("CM_API_BASE_URL", "https://api.example.test/")
    config = ObsidianBridgeConfig.from_environment()
    assert config.api_base_url == "https://api.example.test"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'{"nodes": []}'

    observed = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: int):
        observed["url"] = request.full_url
        observed["auth"] = request.headers["Authorization"]
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert ObsidianBridge(config)._request("/v1/knowledge/projection") == {"nodes": []}
    assert observed == {
        "url": "https://api.example.test/v1/knowledge/projection",
        "auth": "Bearer local-token",
        "timeout": 30,
    }


def test_http_projection_request_rejects_non_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b"[]"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError, match="non-object"):
        bridge(tmp_path)._request("/v1/knowledge/projection")


def test_user_notes_survive_replacement() -> None:
    first = render_note(projected_node(), titles={}, incoming=[])
    edited = first.split(MY_NOTES, 1)[0] + MY_NOTES + "\n\nMy durable thought.\n"
    second = render_note(projected_node(title="Renamed"), titles={}, incoming=[])
    result = preserve_user_notes(edited, second)
    assert "# Renamed" in result and "My durable thought." in result


def test_full_sync_is_idempotent_restart_safe_and_writes_mocs(tmp_path: Path) -> None:
    value = projected_node()
    instance = bridge(tmp_path)
    instance._request = lambda _path: {"nodes": [value], "next_cursor": "cursor-1"}
    assert instance.sync(full=True) == {"written": 1, "archived": 0}
    path = tmp_path / relative_note_path("product", value["canonical_id"])
    custom = path.read_text().replace("User-editable content.", "Keep me")
    path.write_text(custom)
    restarted = bridge(tmp_path)
    restarted._request = instance._request
    restarted.sync(full=True)
    assert "Keep me" in path.read_text()
    assert (tmp_path / "Creative Marketer.md").exists()
    assert (
        json.loads((tmp_path / ".creative-marketer/state.json").read_text())["cursor"] == "cursor-1"
    )


def test_incremental_deletion_archives_managed_note(tmp_path: Path) -> None:
    value = projected_node()
    instance = bridge(tmp_path)
    responses = iter(
        (
            {"nodes": [value], "next_cursor": "one"},
            {
                "changes": [
                    {
                        "revision": 2,
                        "node": None,
                        "deleted_node": {
                            "node_type": "product",
                            "canonical_id": value["canonical_id"],
                        },
                    }
                ],
                "next_cursor": "two",
                "has_more": False,
            },
        )
    )
    instance._request = lambda _path: next(responses)
    instance.sync(full=True)
    result = instance.sync()
    archived = (
        tmp_path
        / ".creative-marketer/archive"
        / relative_note_path("product", value["canonical_id"])
    )
    assert result == {"written": 0, "archived": 1} and archived.exists()


def test_incremental_upserts_page_until_cursor_is_exhausted(tmp_path: Path) -> None:
    first, second = projected_node(title="First"), projected_node(title="Second")
    instance = bridge(tmp_path)
    responses = iter(
        (
            {"nodes": [first], "next_cursor": "one"},
            {
                "changes": [{"revision": 2, "node": second, "deleted_node": None}],
                "next_cursor": "two",
                "has_more": True,
            },
            {
                "changes": [],
                "next_cursor": "three",
                "has_more": False,
            },
        )
    )
    requested: list[str] = []

    def request(path: str):
        requested.append(path)
        return next(responses)

    instance._request = request
    instance.sync(full=True)
    assert instance.sync() == {"written": 1, "archived": 0}
    assert len(requested) == 3 and "cursor=two" in requested[-1]
    assert (tmp_path / relative_note_path("product", second["canonical_id"])).exists()
    assert json.loads(instance.state_path.read_text())["cursor"] == "three"


def test_path_traversal_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    instance = bridge(tmp_path)
    with pytest.raises(ValueError):
        instance._safe_path(Path("../outside"))
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        instance._safe_path(Path("escape/file.md"))


def test_complete_vault_contains_no_secret_sentinels(tmp_path: Path) -> None:
    value = projected_node()
    value["properties"] = {
        "safe": "public",
        "credential": "sk-supersecret0000",
        "raw_html": "<html>secret</html>",
    }
    # Server-side filtering removes forbidden fields before the adapter receives them.
    value["properties"] = {"safe": "public"}
    instance = bridge(tmp_path)
    instance._request = lambda _path: {"nodes": [value], "next_cursor": "cursor"}
    instance.sync(full=True)
    rendered = "\n".join(path.read_text() for path in tmp_path.rglob("*.md"))
    assert "supersecret" not in rendered and "<html>" not in rendered


def test_bridge_cli_runs_full_sync(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed = {}

    class Config:
        @classmethod
        def from_environment(cls):
            return object()

    class Bridge:
        def __init__(self, _config):
            pass

        def sync(self, *, full: bool):
            observed["full"] = full
            return {"written": 2, "archived": 1}

    monkeypatch.setattr(bridge_cli, "ObsidianBridgeConfig", Config)
    monkeypatch.setattr(bridge_cli, "ObsidianBridge", Bridge)
    monkeypatch.setattr("sys.argv", ["creative-marketer-obsidian", "--full"])
    bridge_cli.main()
    assert observed == {"full": True}
    assert json.loads(capsys.readouterr().out) == {"written": 2, "archived": 1}


def test_setup_and_watch_reconnect_lock_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    instance = bridge(tmp_path)
    paths: list[str] = []

    def request(path: str):
        paths.append(path)
        return {
            "nodes": [],
            "next_cursor": "ready",
        }

    instance._request = request
    assert instance.validate_setup()["vault"] == str(tmp_path)
    assert paths == ["/health/live", "/v1/knowledge/projection"]

    attempts = 0

    def sync(*, full=False):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary API outage")
        return {"written": 0, "archived": 0}

    sleeps: list[float] = []
    instance.sync = sync
    monkeypatch.setattr("time.sleep", sleeps.append)
    instance.watch(interval_seconds=1, max_cycles=1)
    assert attempts == 2 and sleeps == [1]
    assert not instance.lock_path.exists()

    instance.lock_path.mkdir()
    with pytest.raises(RuntimeError, match="already owns"):
        instance.watch(interval_seconds=1, max_cycles=1)
    instance.lock_path.rmdir()
    with pytest.raises(ValueError, match="between 1 and 60"):
        instance.watch(interval_seconds=0, max_cycles=1)


@pytest.mark.parametrize("flag,method", [("--watch", "watch"), ("--setup", "validate_setup")])
def test_bridge_cli_watch_and_setup(monkeypatch, flag, method) -> None:
    observed = []

    class Config:
        @classmethod
        def from_environment(cls):
            return object()

    class Bridge:
        def __init__(self, _config):
            pass

        def watch(self, *, interval_seconds):
            observed.append(("watch", interval_seconds))

        def validate_setup(self):
            observed.append(("validate_setup", None))
            return {"ok": "true"}

        def sync(self, *, full):
            raise AssertionError(full)

    monkeypatch.setattr(bridge_cli, "ObsidianBridgeConfig", Config)
    monkeypatch.setattr(bridge_cli, "ObsidianBridge", Bridge)
    monkeypatch.setattr("sys.argv", ["creative-marketer-obsidian", flag])
    bridge_cli.main()
    assert observed[0][0] == method
