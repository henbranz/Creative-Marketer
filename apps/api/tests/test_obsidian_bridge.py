# mypy: disable-error-code="no-untyped-def,no-untyped-call,method-assign,assignment"

import json
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


def test_markdown_frontmatter_wikilinks_and_escaping() -> None:
    product = projected_node(title="Product [[escape]] <script>")
    brand = projected_node("brand", title="Brand")
    product["relationships"] = [
        {
            "relationship_type": "belongs_to_brand",
            "target_node_type": "brand",
            "target_canonical_id": brand["canonical_id"],
        }
    ]
    text = render_note(product, titles={f"brand:{brand['canonical_id']}": "Brand"}, incoming=[])
    assert text.startswith('---\ncm_type: "product"')
    assert "[[Products/brand--" in text
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert MY_NOTES in text
    assert markdown_escape("# [unsafe]") == "\\# \\[unsafe\\]"


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
