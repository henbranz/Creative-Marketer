from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from creative_marketer.research.domain import (
    MAX_BLOCK_CHARACTERS,
    MAX_EVIDENCE_BLOCKS,
    MAX_EXTRACTED_BYTES,
    MAX_OUTBOUND_LINKS,
    EvidenceBlock,
    EvidenceBlockKind,
    ResearchValidationError,
    canonicalize_url,
    research_sha256_v1,
)

SKIPPED = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "noscript",
    "svg",
}
VOID = {"input", "embed"}
BLOCKS = {
    "h1": EvidenceBlockKind.HEADING,
    "h2": EvidenceBlockKind.HEADING,
    "h3": EvidenceBlockKind.HEADING,
    "h4": EvidenceBlockKind.HEADING,
    "h5": EvidenceBlockKind.HEADING,
    "h6": EvidenceBlockKind.HEADING,
    "p": EvidenceBlockKind.PARAGRAPH,
    "li": EvidenceBlockKind.LIST,
    "tr": EvidenceBlockKind.TABLE_TEXT,
    "blockquote": EvidenceBlockKind.QUOTE,
}
INSTRUCTION_PATTERN = re.compile(
    r"(?i)\b(ignore (?:all|any|the|previous)|system prompt|developer message|do not reveal|"
    r"follow these instructions|you are (?:chatgpt|an? ai|a language model))\b"
)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    title: str | None
    blocks: tuple[EvidenceBlock, ...]
    outbound_links: tuple[str, ...]
    structured_metadata: dict[str, str]
    instruction_like_content: bool
    semantic_digest: str


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip_depth = 0
        self.current: tuple[EvidenceBlockKind, list[str]] | None = None
        self.blocks: list[EvidenceBlock] = []
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        hidden = "hidden" in values or values.get("aria-hidden", "").lower() == "true"
        style = values.get("style", "").replace(" ", "").lower()
        if self.skip_depth:
            if tag not in VOID:
                self.skip_depth += 1
            return
        if tag in SKIPPED or hidden or "display:none" in style:
            if tag not in VOID:
                self.skip_depth = 1
            return
        if tag == "title":
            self.in_title = True
        if tag in BLOCKS and len(self.blocks) < MAX_EVIDENCE_BLOCKS:
            self._flush()
            self.current = (BLOCKS[tag], [])
        if tag == "a" and values.get("href") and len(self.links) < MAX_OUTBOUND_LINKS:
            try:
                link = canonicalize_url(urljoin(self.base_url, values["href"]))
                if urlsplit(link).scheme in {"http", "https"} and link not in self.links:
                    self.links.append(link)
            except (ResearchValidationError, ValueError):
                pass
        if tag == "meta":
            key = values.get("property") or values.get("name")
            content = values.get("content", "").strip()
            if (
                key
                and content
                and key.lower()
                in {"description", "og:title", "og:site_name", "article:published_time"}
            ):
                self.metadata[key.lower()] = content[:2000]
        if tag == "link" and values.get("rel", "").lower() == "canonical" and values.get("href"):
            with suppress(ResearchValidationError):
                self.metadata["canonical"] = canonicalize_url(
                    urljoin(self.base_url, values["href"])
                )

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            if tag not in VOID:
                self.skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
        if tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        if self.current is not None:
            self.current[1].append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self.current is None:
            return
        kind, values = self.current
        text = " ".join(" ".join(values).split())[:MAX_BLOCK_CHARACTERS]
        if text:
            self.blocks.append(EvidenceBlock(kind, text, len(self.blocks)))
        self.current = None


class DeterministicEvidenceExtractor:
    version = "html-v1"

    def extract(self, body: bytes, content_type: str, final_url: str) -> ExtractionResult:
        try:
            media_type, _, parameters = content_type.partition(";")
            charset = "utf-8"
            for parameter in parameters.split(";"):
                key, separator, value = parameter.partition("=")
                if separator and key.strip().lower() == "charset":
                    charset = value.strip().strip("\"'") or "utf-8"
                    break
            try:
                text = body.decode(charset, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            title: str | None
            links: tuple[str, ...]
            metadata: dict[str, str]
            if media_type.strip().lower() == "text/plain":
                blocks = tuple(
                    EvidenceBlock(EvidenceBlockKind.PARAGRAPH, part[:MAX_BLOCK_CHARACTERS], index)
                    for index, part in enumerate(
                        filter(None, (" ".join(v.split()) for v in text.split("\n\n")))
                    )
                    if index < MAX_EVIDENCE_BLOCKS
                )
                title, links, metadata = None, (), {}
            else:
                parser = _Extractor(final_url)
                parser.feed(text)
                parser.close()
                blocks = tuple(parser.blocks)
                title = " ".join(" ".join(parser.title_parts).split())[:500] or None
                links = tuple(parser.links)
                metadata = parser.metadata
        except Exception as error:
            raise ResearchValidationError("evidence extraction failed") from error
        if not blocks:
            raise ResearchValidationError("source did not contain extractable text")
        instruction_like = any(INSTRUCTION_PATTERN.search(block.text) for block in blocks)
        content = {
            "schema_version": 1,
            "extractor_version": self.version,
            "final_url": canonicalize_url(final_url),
            "title": title,
            "blocks": [block.semantic() for block in blocks],
            "outbound_links": list(links),
            "structured_metadata": metadata,
            "instruction_like_content": instruction_like,
        }
        if len(str(content).encode()) > MAX_EXTRACTED_BYTES:
            raise ResearchValidationError("extracted evidence exceeds 500 KiB")
        return ExtractionResult(
            title, blocks, links, metadata, instruction_like, research_sha256_v1(content)
        )
