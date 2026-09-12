import hashlib
import json
import os
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BEGIN_GENERATED = "<!-- CM:BEGIN GENERATED -->"
END_GENERATED = "<!-- CM:END GENERATED -->"
MY_NOTES = "## My Notes"

TYPE_DIRECTORIES = {
    "brand": "Products",
    "product": "Products",
    "product_knowledge_snapshot": "Products",
    "agent_definition": "Agents",
    "agent_version": "Agents",
    "agent_run": "Runs",
    "research_source": "Research",
    "evidence_snapshot": "Research/Evidence",
    "research_snapshot": "Research",
    "research_finding": "Research/Findings",
    "creative_concept_set": "Creative",
    "creative_concept": "Creative",
    "creative_concept_decision": "Creative",
    "asset": "Assets",
}


def stable_filename(node_type: str, canonical_id: str) -> str:
    if node_type not in TYPE_DIRECTORIES or not canonical_id.strip():
        raise ValueError("unsupported or empty knowledge node identity")
    digest = hashlib.sha256(f"{node_type}:{canonical_id}".encode()).hexdigest()
    return f"{node_type.replace('_', '-')}--{digest}.md"


def relative_note_path(node_type: str, canonical_id: str) -> Path:
    return Path(TYPE_DIRECTORIES[node_type]) / stable_filename(node_type, canonical_id)


def obsidian_open_uri(vault_name: str, node_type: str, canonical_id: str) -> str:
    if not vault_name.strip() or any(char in vault_name for char in "\r\n"):
        raise ValueError("vault name is invalid")
    path = relative_note_path(node_type, canonical_id).with_suffix("").as_posix()
    return "obsidian://open?" + urllib.parse.urlencode({"vault": vault_name, "file": path})


def markdown_escape(value: object) -> str:
    rendered = str(value).replace("\r", " ").replace("\x00", "")
    rendered = rendered.replace(BEGIN_GENERATED, "CM BEGIN GENERATED")
    rendered = rendered.replace(END_GENERATED, "CM END GENERATED")
    rendered = rendered.replace("`", "\\`")
    for char in "\\[]*_|#":
        rendered = rendered.replace(char, "\\" + char)
    return rendered.replace("<", "&lt;").replace(">", "&gt;")


def _frontmatter_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _safe_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        .replace("```", "` ` `")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(BEGIN_GENERATED, "CM BEGIN GENERATED")
        .replace(END_GENERATED, "CM END GENERATED")
    )


def _wikilink(node_type: str, canonical_id: str, title: str) -> str:
    path = relative_note_path(node_type, canonical_id).with_suffix("").as_posix()
    return f"[[{path}|{markdown_escape(title)}]]"


def render_note(
    node: dict[str, Any],
    *,
    titles: dict[str, str],
    incoming: list[dict[str, str]],
) -> str:
    frontmatter = [
        "---",
        f"cm_type: {_frontmatter_string(node['node_type'])}",
        f"canonical_id: {_frontmatter_string(node['canonical_id'])}",
        f"status: {_frontmatter_string(node['status'])}",
    ]
    if node.get("semantic_digest"):
        frontmatter.append(f"semantic_digest: {_frontmatter_string(node['semantic_digest'])}")
    frontmatter.extend(["cm_managed: true", "---", ""])
    body = [BEGIN_GENERATED, f"# {markdown_escape(node['title'])}", ""]
    properties = node.get("properties") or {}
    if properties:
        body.extend(["## Details", ""])
        for key in sorted(properties):
            value = properties[key]
            if value in (None, "", [], {}):
                continue
            label = markdown_escape(key.replace("_", " ").title())
            if isinstance(value, list):
                body.append(f"### {label}")
                for item in value:
                    rendered = (
                        json.dumps(item, ensure_ascii=False, sort_keys=True)
                        if isinstance(item, dict)
                        else str(item)
                    )
                    body.append(f"- {markdown_escape(rendered)}")
            elif isinstance(value, dict):
                body.extend(
                    [
                        f"### {label}",
                        "```json",
                        _safe_json(value),
                        "```",
                    ]
                )
            else:
                body.append(f"- **{label}:** {markdown_escape(value)}")
        body.append("")
    relationships = node.get("relationships") or []
    if relationships:
        body.extend(["## Outgoing Relationships", ""])
        for relationship in relationships:
            key = f"{relationship['target_node_type']}:{relationship['target_canonical_id']}"
            title = titles.get(key, relationship["target_canonical_id"])
            link = _wikilink(
                relationship["target_node_type"], relationship["target_canonical_id"], title
            )
            body.append(
                f"- {markdown_escape(relationship['relationship_type'].replace('_', ' '))} → {link}"
            )
        body.append("")
    if incoming:
        body.extend(["## Incoming Relationships", ""])
        for relationship in incoming:
            key = f"{relationship['source_node_type']}:{relationship['source_canonical_id']}"
            title = titles.get(key, relationship["source_canonical_id"])
            link = _wikilink(
                relationship["source_node_type"], relationship["source_canonical_id"], title
            )
            body.append(
                f"- {markdown_escape(relationship['relationship_type'].replace('_', ' '))} ← {link}"
            )
        body.append("")
    body.extend([END_GENERATED, "", MY_NOTES, "", "User-editable content.", ""])
    return "\n".join(frontmatter + body)


def preserve_user_notes(existing: str | None, generated: str) -> str:
    if not existing or MY_NOTES not in existing:
        return generated
    user_content = existing.split(MY_NOTES, 1)[1]
    return generated.split(MY_NOTES, 1)[0] + MY_NOTES + user_content


@dataclass(frozen=True, slots=True)
class ObsidianBridgeConfig:
    vault_path: Path
    api_base_url: str
    tenant_id: str
    bearer_token: str

    @classmethod
    def from_environment(cls) -> "ObsidianBridgeConfig":
        required = {
            name: os.environ.get(name, "").strip()
            for name in ("OBSIDIAN_VAULT_PATH", "CM_API_BASE_URL", "CM_TENANT_ID", "CM_API_TOKEN")
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"missing bridge configuration: {', '.join(missing)}")
        parsed = urllib.parse.urlparse(required["CM_API_BASE_URL"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("CM_API_BASE_URL must be an absolute HTTP(S) URL")
        return cls(
            Path(required["OBSIDIAN_VAULT_PATH"]),
            required["CM_API_BASE_URL"].rstrip("/"),
            required["CM_TENANT_ID"],
            required["CM_API_TOKEN"],
        )


class ObsidianBridge:
    def __init__(self, config: ObsidianBridgeConfig) -> None:
        self.config = config
        self.root = config.vault_path.expanduser().resolve()
        self.state_path = self.root / ".creative-marketer" / "state.json"

    def _safe_path(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("vault path traversal rejected")
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("vault path escapes configured root")
        return candidate

    def _request(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.config.api_base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.config.bearer_token}",
                "X-Tenant-ID": self.config.tenant_id,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.loads(response.read(4 * 1024 * 1024))
            if not isinstance(value, Mapping):
                raise ValueError("projection API returned a non-object response")
            return {str(key): child for key, child in value.items()}

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"cursor": None, "nodes": {}}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"cursor": None, "nodes": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _write_text(target: Path, content: str) -> None:
        temporary = target.with_suffix(target.suffix + ".cm-tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)

    def _write_graph(self, nodes: list[dict[str, Any]], state: dict[str, Any]) -> None:
        titles = {f"{node['node_type']}:{node['canonical_id']}": node["title"] for node in nodes}
        incoming: dict[str, list[dict[str, str]]] = {}
        for node in nodes:
            for relationship in node.get("relationships", []):
                key = f"{relationship['target_node_type']}:{relationship['target_canonical_id']}"
                incoming.setdefault(key, []).append(
                    {
                        "source_node_type": node["node_type"],
                        "source_canonical_id": node["canonical_id"],
                        "relationship_type": relationship["relationship_type"],
                    }
                )
        for node in nodes:
            key = f"{node['node_type']}:{node['canonical_id']}"
            relative = relative_note_path(node["node_type"], node["canonical_id"])
            target = self._safe_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.read_text(encoding="utf-8") if target.exists() else None
            rendered = render_note(node, titles=titles, incoming=incoming.get(key, []))
            self._write_text(target, preserve_user_notes(existing, rendered))
            state["nodes"][key] = {
                "path": relative.as_posix(),
                "title": node["title"],
                "node_type": node["node_type"],
            }

    def _archive(self, node: dict[str, str], state: dict[str, Any]) -> None:
        key = f"{node['node_type']}:{node['canonical_id']}"
        entry = state["nodes"].pop(key, None)
        if not entry:
            return
        source = self._safe_path(Path(entry["path"]))
        if source.exists():
            archive = self._safe_path(Path(".creative-marketer/archive") / Path(entry["path"]))
            archive.parent.mkdir(parents=True, exist_ok=True)
            source.replace(archive)

    def _write_mocs(self, state: dict[str, Any]) -> None:
        groups = {
            "Agents.md": {"agent_definition", "agent_version", "agent_run"},
            "Products.md": {"brand", "product", "product_knowledge_snapshot", "asset"},
            "Research.md": {
                "research_source",
                "evidence_snapshot",
                "research_snapshot",
                "research_finding",
            },
            "Creative.md": {
                "creative_concept_set",
                "creative_concept",
                "creative_concept_decision",
            },
        }
        all_entries = list(state["nodes"].values())
        for filename, types in groups.items():
            links = [
                "- [["
                + Path(item["path"]).with_suffix("").as_posix()
                + "|"
                + markdown_escape(item["title"])
                + "]]"
                for item in sorted(all_entries, key=lambda value: (value["title"], value["path"]))
                if item["node_type"] in types
            ]
            body = "\n".join(
                [
                    BEGIN_GENERATED,
                    f"# {filename[:-3]}",
                    "",
                    *links,
                    END_GENERATED,
                    "",
                    MY_NOTES,
                    "",
                    "User-editable content.",
                    "",
                ]
            )
            target = self._safe_path(Path(filename))
            existing = target.read_text(encoding="utf-8") if target.exists() else None
            self._write_text(target, preserve_user_notes(existing, body))
        root_links = [f"- [[{name[:-3]}]]" for name in groups]
        target = self._safe_path(Path("Creative Marketer.md"))
        body = "\n".join(
            [
                BEGIN_GENERATED,
                "# Creative Marketer",
                "",
                *root_links,
                END_GENERATED,
                "",
                MY_NOTES,
                "",
                "User-editable content.",
                "",
            ]
        )
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        self._write_text(target, preserve_user_notes(existing, body))

    def sync(self, *, full: bool = False) -> dict[str, int]:
        self.root.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        written = archived = 0
        if full or not state.get("cursor"):
            response = self._request("/v1/knowledge/projection")
            current_keys = {
                f"{node['node_type']}:{node['canonical_id']}" for node in response["nodes"]
            }
            for key in set(state["nodes"]) - current_keys:
                node_type, canonical_id = key.split(":", 1)
                self._archive({"node_type": node_type, "canonical_id": canonical_id}, state)
                archived += 1
            self._write_graph(response["nodes"], state)
            written = len(response["nodes"])
            state["cursor"] = response["next_cursor"]
        else:
            while True:
                query = urllib.parse.urlencode({"cursor": state["cursor"], "limit": 250})
                response = self._request(f"/v1/knowledge/projection/changes?{query}")
                for item in response["changes"]:
                    if item.get("node"):
                        self._write_graph([item["node"]], state)
                        written += 1
                    elif item.get("deleted_node"):
                        self._archive(item["deleted_node"], state)
                        archived += 1
                state["cursor"] = response["next_cursor"]
                self._save_state(state)
                if not response["has_more"]:
                    break
        self._write_mocs(state)
        self._save_state(state)
        return {"written": written, "archived": archived}
