from dataclasses import dataclass
from typing import Protocol

from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.knowledge.domain import KnowledgeChange, KnowledgeGraph


class CanonicalKnowledgeReader(Protocol):
    async def graph(self, context: ExecutionContext) -> KnowledgeGraph: ...


class KnowledgeProjectionStore(Protocol):
    async def refresh(self, context: ExecutionContext, graph: KnowledgeGraph) -> int: ...

    async def changes(
        self, context: ExecutionContext, *, after_revision: int, limit: int
    ) -> tuple[tuple[KnowledgeChange, ...], int]: ...


@dataclass(slots=True)
class KnowledgeGraphProjector:
    reader: CanonicalKnowledgeReader
    store: KnowledgeProjectionStore

    async def full(self, context: ExecutionContext) -> tuple[KnowledgeGraph, int]:
        graph = await self.reader.graph(context)
        revision = await self.store.refresh(context, graph)
        return graph, revision

    async def changes(
        self, context: ExecutionContext, *, cursor: int, limit: int = 250
    ) -> tuple[tuple[KnowledgeChange, ...], int, bool]:
        if cursor < 0 or not 1 <= limit <= 500:
            raise ValueError("knowledge projection cursor or limit is invalid")
        graph = await self.reader.graph(context)
        await self.store.refresh(context, graph)
        changes, latest = await self.store.changes(context, after_revision=cursor, limit=limit + 1)
        has_more = len(changes) > limit
        page = changes[:limit]
        next_cursor = page[-1].revision if page else min(cursor, latest)
        return page, next_cursor, has_more
