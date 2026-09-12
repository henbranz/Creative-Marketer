import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.catalog.application import CatalogService
from creative_marketer.catalog.domain import (
    Brand,
    BrandProfile,
    Product,
    ProductBrief,
    ProductProfile,
)
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.infrastructure.database.catalog_uow import (
    SqlAlchemyCatalogUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.knowledge_projection import (
    SqlAlchemyCanonicalKnowledgeReader,
    SqlAlchemyKnowledgeProjectionStore,
)
from creative_marketer.knowledge.application import KnowledgeGraphProjector
from creative_marketer.knowledge.domain import KnowledgeNodeType
from tests.integration.test_catalog import owner_context, seed_catalog_identity


async def product_for(
    admin: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
    *,
    name: str,
    secret: str,
) -> tuple[ExecutionContext, Product]:
    tenant_id, user_id = await seed_catalog_identity(admin)
    context = owner_context(tenant_id, user_id)
    service = CatalogService(catalog_factory)
    brand = Brand(tenant_id, f"{name} Brand", name.lower(), user_id)
    await service.create_brand(context, brand, BrandProfile(tenant_id, brand.id))
    product = Product(
        tenant_id,
        brand.id,
        name,
        name.lower(),
        "Test",
        user_id,
        short_description=secret,
    )
    await service.create_product(
        context,
        product,
        ProductProfile(tenant_id, product.id),
        ProductBrief(tenant_id, product.id),
    )
    return context, product


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_projection_is_cross_tenant_private_incremental_and_idempotent(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
    runtime_database_url: str,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
) -> None:
    context_a, product_a = await product_for(
        admin_engine,
        catalog_factory,
        name="Alpha",
        secret="safe alpha",
    )
    context_b, product_b = await product_for(
        admin_engine,
        catalog_factory,
        name="Bravo",
        secret="sk-crossTenantSecret000000",
    )
    sessions = create_session_factory(runtime_database_url)
    projector = KnowledgeGraphProjector(
        SqlAlchemyCanonicalKnowledgeReader(sessions),
        SqlAlchemyKnowledgeProjectionStore(sessions),
    )
    graph_a, revision = await projector.full(context_a)
    serialized = str([node.primitive() for node in graph_a.nodes])
    assert str(product_a.id) in serialized
    assert str(product_b.id) not in serialized
    assert "crossTenantSecret" not in serialized
    changes, cursor, has_more = await projector.changes(context_a, cursor=0, limit=500)
    assert changes and cursor == revision and not has_more
    repeated, repeated_cursor, _ = await projector.changes(context_a, cursor=cursor, limit=500)
    assert repeated == () and repeated_cursor == cursor
    graph_b, _ = await projector.full(context_b)
    assert all(node.canonical_id != str(product_a.id) for node in graph_b.nodes)

    # Exact foreign UUID knowledge does not bypass RLS on either projection table.
    async with runtime_engine.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
            {"tenant": str(context_a.tenant_id)},
        )
        leaked = await connection.scalar(
            text(
                "SELECT count(*) FROM knowledge_projection.projection_nodes WHERE canonical_id=:id"
            ),
            {"id": str(product_b.id)},
        )
    assert leaked == 0


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_projection_change_history_is_immutable_and_missing_context_fails_closed(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
    runtime_database_url: str,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
) -> None:
    context, _ = await product_for(admin_engine, catalog_factory, name="Immutable", secret="safe")
    sessions = create_session_factory(runtime_database_url)
    projector = KnowledgeGraphProjector(
        SqlAlchemyCanonicalKnowledgeReader(sessions),
        SqlAlchemyKnowledgeProjectionStore(sessions),
    )
    graph, _ = await projector.full(context)
    assert any(node.node_type is KnowledgeNodeType.PRODUCT for node in graph.nodes)
    async with runtime_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM knowledge_projection.projection_nodes")
            )
            == 0
        )
    with pytest.raises(DBAPIError):
        async with runtime_engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                {"tenant": str(context.tenant_id)},
            )
            await connection.execute(text("DELETE FROM knowledge_projection.projection_changes"))
