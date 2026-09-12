# mypy: disable-error-code="arg-type,no-untyped-def"

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from creative_marketer.infrastructure.database.knowledge_projection import (
    SqlAlchemyCanonicalKnowledgeReader,
)
from creative_marketer.knowledge.application import KnowledgeGraphProjector
from creative_marketer.knowledge.domain import (
    KnowledgeChange,
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeNodeRef,
    KnowledgeNodeType,
    KnowledgeRelationship,
)
from creative_marketer_api.knowledge_routes import (
    create_knowledge_router,
    decode_cursor,
    encode_cursor,
)


def node(title: str = "Product") -> KnowledgeNode:
    now = datetime.now(UTC)
    return KnowledgeNode(
        KnowledgeNodeType.PRODUCT,
        str(uuid4()),
        title,
        "active",
        now,
        now,
        {"description": "Safe", "api_key": "sk-secret000000"},
    )


class Reader:
    def __init__(self, graph: KnowledgeGraph) -> None:
        self.value = graph

    async def graph(self, _context):
        return self.value


class Store:
    def __init__(self) -> None:
        self.nodes: dict[KnowledgeNodeRef, KnowledgeNode] = {}
        self.log: list[KnowledgeChange] = []

    async def refresh(self, _context, graph):
        incoming = {item.ref: item for item in graph.nodes}
        for ref, value in incoming.items():
            if self.nodes.get(ref) != value:
                self.nodes[ref] = value
                self.log.append(KnowledgeChange(len(self.log) + 1, value, None))
        for ref in set(self.nodes) - set(incoming):
            del self.nodes[ref]
            self.log.append(KnowledgeChange(len(self.log) + 1, None, ref))
        return len(self.log)

    async def changes(self, _context, *, after_revision, limit):
        values = tuple(item for item in self.log if item.revision > after_revision)[:limit]
        return values, len(self.log)


@pytest.mark.asyncio
async def test_projector_is_incremental_idempotent_and_replays_deletions() -> None:
    original = node()
    reader, store = Reader(KnowledgeGraph((original,))), Store()
    projector = KnowledgeGraphProjector(reader, store)
    first, cursor, more = await projector.changes(object(), cursor=0, limit=10)
    assert first[0].node == original and cursor == 1 and not more
    repeated, cursor, _ = await projector.changes(object(), cursor=cursor, limit=10)
    assert repeated == () and cursor == 1
    reader.value = KnowledgeGraph(())
    deleted, cursor, _ = await projector.changes(object(), cursor=cursor, limit=10)
    assert deleted[0].deleted_node == original.ref and cursor == 2


@pytest.mark.asyncio
async def test_projector_pages_a_stable_revision_log() -> None:
    values = tuple(node(str(index)) for index in range(3))
    projector = KnowledgeGraphProjector(Reader(KnowledgeGraph(values)), Store())
    first, cursor, more = await projector.changes(object(), cursor=0, limit=2)
    second, cursor, more_again = await projector.changes(object(), cursor=cursor, limit=2)
    assert len(first) == 2 and more
    assert len(second) == 1 and cursor == 3 and not more_again


def test_nodes_are_deterministic_private_and_edges_are_derived() -> None:
    source, target = node("Source"), node("Target")
    linked = KnowledgeNode(
        source.node_type,
        source.canonical_id,
        source.title,
        source.status,
        source.created_at,
        source.updated_at,
        {"authorization": "Bearer secretsecret", "safe": "visible"},
        (KnowledgeRelationship(target.ref, "belongs_to_product"),),
    )
    graph = KnowledgeGraph((linked, target))
    assert linked.properties == {"safe": "visible"}
    assert graph.edges[0].target_node == target.ref
    assert linked.projection_digest == linked.projection_digest
    assert "secretsecret" not in str(linked.primitive())


def test_cursor_is_tenant_bound_and_rejects_tampering() -> None:
    tenant = uuid4()
    cursor = encode_cursor(tenant, 42)
    assert decode_cursor(cursor, tenant) == 42
    with pytest.raises(HTTPException):
        decode_cursor(cursor, uuid4())
    with pytest.raises(HTTPException):
        decode_cursor("not-json", tenant)


def test_canonical_reader_builds_only_supplied_tenant_rows_and_reverse_links() -> None:
    now, brand_id, product_id = datetime.now(UTC), uuid4(), uuid4()
    rows = {
        "brands": [
            {
                "id": brand_id,
                "name": "Brand",
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "slug": "brand",
                "website_url": None,
            }
        ],
        "products": [
            {
                "id": product_id,
                "brand_id": brand_id,
                "name": "Product",
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "category": "test",
                "sku": None,
                "short_description": "Description",
            }
        ],
        "product_snapshots": [],
        "assets": [],
        "definitions": [],
        "versions": [],
        "activations": [],
        "runs": [],
        "sources": [],
        "evidence": [],
        "research_snapshots": [],
        "concept_sets": [],
        "concepts": [],
        "decisions": [],
    }
    graph = SqlAlchemyCanonicalKnowledgeReader(None)._build(rows)
    assert len(graph.nodes) == 2
    brand = next(item for item in graph.nodes if item.node_type is KnowledgeNodeType.BRAND)
    assert any(rel.target.canonical_id == str(product_id) for rel in brand.relationships)


def endpoint(router, path: str):
    return next(route.endpoint for route in router.routes if route.path == path)


@pytest.mark.asyncio
async def test_projection_routes_expose_full_and_incremental_contracts() -> None:
    value = node()
    projector = KnowledgeGraphProjector(Reader(KnowledgeGraph((value,))), Store())
    router = create_knowledge_router(None, None, projector, "test", None)
    context = type("Context", (), {"tenant_id": uuid4()})()
    full = await endpoint(router, "/v1/knowledge/projection")(context)
    changes = await endpoint(router, "/v1/knowledge/projection/changes")(
        context, full.next_cursor, 250
    )
    assert full.nodes[0].canonical_id == value.canonical_id
    assert changes.changes == [] and not changes.has_more


@pytest.mark.asyncio
async def test_projector_rejects_bad_bounds() -> None:
    projector = KnowledgeGraphProjector(Reader(KnowledgeGraph(())), Store())
    with pytest.raises(ValueError):
        await projector.changes(object(), cursor=-1)
    with pytest.raises(ValueError):
        await projector.changes(object(), cursor=0, limit=501)
