from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from creative_marketer.agent_runtime.domain import parse_research_output
from creative_marketer.catalog.application import CatalogConflict, CatalogService
from creative_marketer.catalog.domain import ProductBrief, ProductProfile
from creative_marketer.creative.domain import product_claim_refs
from creative_marketer.infrastructure.database.agent_runtime_repositories import (
    SqlAlchemyAgentRunRepository,
)
from creative_marketer.infrastructure.database.catalog_uow import SqlAlchemyCatalogUnitOfWorkFactory
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter
from tests.integration.test_asset_library import product_setup
from tests.integration.test_catalog import client, headers, seed_catalog_identity
from tests.test_agent_runtime_domain import block, output, run

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("scope", ["product", "brand"])
async def test_claim_create_edit_remove_snapshot_and_historical_authority(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
    runtime_database_url: str,
    scope: Literal["product", "brand"],
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    catalog = CatalogService(catalog_factory)
    sibling = replace(product, id=uuid4(), name="Sibling", slug="sibling")
    await catalog.create_product(
        context,
        sibling,
        ProductProfile(tenant_id=context.tenant_id, product_id=sibling.id),
        ProductBrief(tenant_id=context.tenant_id, product_id=sibling.id),
    )
    workspace = await catalog.get_workspace(context, product.id)
    await catalog.save_brief(context, replace(workspace.brief, product_why="Unapproved claim"))
    original = await catalog.create_snapshot(context, product.id)
    assert product_claim_refs(original.digest, original.content) == ()
    previous: list[str] = []
    saved_snapshots = [original]
    async for api in client(runtime_database_url):
        for claims in (["User-confirmed fact"], ["Edited user-confirmed fact"], []):
            response = await api.put(
                f"/v1/products/{product.id}/claims",
                headers=headers(context.tenant_id, context.user_id),
                json={"scope": scope, "allowed_claims": claims, "expected_claims": previous},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            key = "product" if scope == "product" else "brand"
            assert result[key]["profile"]["allowed_claims"] == claims
            workspace = await catalog.get_workspace(context, product.id)
            snapshot = workspace.latest_snapshot
            assert snapshot is not None
            assert snapshot.source_revision == saved_snapshots[-1].source_revision + 1
            assert snapshot.digest != saved_snapshots[-1].digest
            assert workspace.brief.product_why == "Unapproved claim"
            refs = product_claim_refs(snapshot.digest, snapshot.content)
            assert [ref.text for ref in refs] == claims
            assert refs == product_claim_refs(snapshot.digest, snapshot.content)
            assert not set(ref.key for ref in refs) & {
                ref.key
                for old in saved_snapshots
                for ref in product_claim_refs(old.digest, old.content)
            }
            saved_snapshots.append(snapshot)
            sibling_state = await catalog.get_workspace(context, sibling.id)
            if scope == "brand":
                assert sibling_state.latest_snapshot is not None
                assert sibling_state.brief.revision == len(saved_snapshots)
                assert [
                    ref.text
                    for ref in product_claim_refs(
                        sibling_state.latest_snapshot.digest, sibling_state.latest_snapshot.content
                    )
                ] == claims
            else:
                assert sibling_state.latest_snapshot is None
                assert sibling_state.brief.revision == 1
            previous = claims
        # No-op explicit save does not create false revisions.
        await catalog.save_claims(
            context, product.id, scope=scope, allowed_claims=(), expected_claims=()
        )
        assert (
            await catalog.get_workspace(context, product.id)
        ).latest_snapshot == saved_snapshots[-1]
    async with admin_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, digest FROM catalog.product_knowledge_snapshots "
                    "WHERE product_id=:id"
                ),
                {"id": product.id},
            )
        ).all()
        assert {row.id: row.digest for row in rows} == {s.id: s.digest for s in saved_snapshots}
        audits = (
            await connection.execute(
                text(
                    "SELECT before_digest,after_digest,safe_metadata FROM audit.audit_records "
                    "WHERE tenant_id=:tenant AND action=:action"
                ),
                {"tenant": context.tenant_id, "action": f"catalog.{scope}.claims.updated"},
            )
        ).all()
        assert len(audits) == 3
        assert "User-confirmed" not in str(audits)


@pytest.mark.parametrize("role,allowed", [("owner", True), ("admin", True), ("member", False)])
@pytest.mark.parametrize("scope", ["product", "brand"])
async def test_claim_endpoint_permissions_and_tenant_isolation(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
    runtime_database_url: str,
    role: str,
    allowed: bool,
    scope: str,
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE identity.memberships SET role=:role WHERE tenant_id=:id"),
            {"role": role, "id": context.tenant_id},
        )
    foreign_tenant, foreign_user = await seed_catalog_identity(admin_engine)
    payload = {"scope": scope, "allowed_claims": ["Confirmed"], "expected_claims": []}
    async for api in client(runtime_database_url):
        path = f"/v1/products/{product.id}/claims"
        response = await api.put(path, headers=headers(foreign_tenant, foreign_user), json=payload)
        assert response.status_code == 404
        response = await api.put(
            path, headers=headers(context.tenant_id, context.user_id), json=payload
        )
        assert response.status_code == (200 if allowed else 403)
        if not allowed:
            assert (
                await CatalogService(catalog_factory).get_workspace(context, product.id)
            ).latest_snapshot is None


@pytest.mark.parametrize("scope", ["product", "brand"])
async def test_stale_conflicting_and_invalid_claims_fail_closed(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
    runtime_database_url: str,
    scope: Literal["product", "brand"],
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    service = CatalogService(catalog_factory)
    await service.save_claims(
        context, product.id, scope=scope, allowed_claims=("Original",), expected_claims=()
    )
    before = await service.get_workspace(context, product.id)
    async for api in client(runtime_database_url):
        for claims, expected, code in [
            (["Overwrite"], [], 409),
            (["  "], ["Original"], 422),
            (["Duplicate", "duplicate"], ["Original"], 422),
            (["x" * 501], ["Original"], 422),
        ]:
            response = await api.put(
                f"/v1/products/{product.id}/claims",
                headers=headers(context.tenant_id, context.user_id),
                json={"scope": scope, "allowed_claims": claims, "expected_claims": expected},
            )
            assert response.status_code == code
            assert (await service.get_workspace(context, product.id)) == before


async def test_legacy_profile_claim_changes_also_refresh_snapshots(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
) -> None:
    context, brand, product = await product_setup(admin_engine, catalog_factory)
    service = CatalogService(catalog_factory)
    old = await service.get_workspace(context, product.id)
    await service.update_product(
        context, product, replace(old.profile, allowed_claims=("Product fact",))
    )
    await service.update_brand(
        context, brand, replace(old.brand_profile, allowed_claims=("Brand fact",))
    )
    current = await service.get_workspace(context, product.id)
    assert current.brief.revision == 3
    assert current.latest_snapshot is not None
    refs = product_claim_refs(current.latest_snapshot.digest, current.latest_snapshot.content)
    assert [ref.text for ref in refs] == ["Brand fact", "Product fact"]


async def test_concurrent_claim_edits_one_wins_and_one_conflicts(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
) -> None:
    import asyncio

    context, _, product = await product_setup(admin_engine, catalog_factory)
    service = CatalogService(catalog_factory)
    outcomes = await asyncio.gather(
        *(
            service.save_claims(
                context, product.id, scope="product", allowed_claims=(text,), expected_claims=()
            )
            for text in ("First", "Second")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, CatalogConflict) for value in outcomes) == 1
    assert outcomes.count(None) == 1
    current = await service.get_workspace(context, product.id)
    assert current.brief.revision == 2


async def test_claim_change_marks_bound_research_outdated_without_modifying_it(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    service = CatalogService(catalog_factory)
    original = await service.create_snapshot(context, product.id)
    evidence = block()
    research = parse_research_output(
        output(evidence),
        run=replace(
            run(),
            tenant_id=context.tenant_id,
            product_id=product.id,
            product_snapshot_id=original.id,
            product_snapshot_digest=original.digest,
        ),
        selected_blocks=(evidence,),
    )
    research = replace(research, valid_until=datetime.now(UTC) + timedelta(days=1))
    before = research.semantic_digest
    await service.save_claims(
        context, product.id, scope="product", allowed_claims=("New fact",), expected_claims=()
    )
    async with AsyncSession(runtime_engine) as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :id, true)"),
            {"id": str(context.tenant_id)},
        )
        assert (
            await SqlAlchemyAgentRunRepository(session).snapshot_freshness(research) == "outdated"
        )
    assert research.semantic_digest == before


async def test_outbox_failure_rolls_back_claims_revision_and_snapshot(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    service = CatalogService(catalog_factory)
    before = await service.get_workspace(context, product.id)
    monkeypatch.setattr(
        PostgresOutboxWriter, "append", AsyncMock(side_effect=RuntimeError("test rollback"))
    )
    with pytest.raises(RuntimeError, match="test rollback"):
        await service.save_claims(
            context, product.id, scope="brand", allowed_claims=("New fact",), expected_claims=()
        )
    assert await service.get_workspace(context, product.id) == before
