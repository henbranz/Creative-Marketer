from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app


async def seed_catalog_identity(admin: AsyncEngine, *, role: str = "owner") -> tuple[UUID, UUID]:
    tenant_id, user_id = uuid4(), uuid4()
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO identity.tenants (id,name,slug,status) "
                "VALUES (:id,'Catalog tenant',:slug,'active')"
            ),
            {"id": tenant_id, "slug": f"catalog-{tenant_id}"},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.users (id,email,normalized_email,status) "
                "VALUES (:id,:email,:email,'active')"
            ),
            {"id": user_id, "email": f"{user_id}@example.test"},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.external_identities (id,user_id,issuer,subject,status) VALUES (:id,:user,'https://catalog.test',:subject,'active')"
            ),
            {"id": uuid4(), "user": user_id, "subject": str(user_id)},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.memberships (tenant_id,user_id,role,status) "
                "VALUES (:tenant,:user,:role,'active')"
            ),
            {"tenant": tenant_id, "user": user_id, "role": role},
        )
    return tenant_id, user_id


async def client(runtime_url: str) -> AsyncIterator[AsyncClient]:
    app = create_app(
        Settings(
            app_env="test",
            database_url=runtime_url,
            dev_identity_enabled=True,
            audit_fingerprint_key="catalog-test-fingerprint-key-32-bytes",
            cors_origins=["http://localhost:3000"],
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


def headers(tenant_id: UUID, user_id: UUID, **forged: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer https://catalog.test|{user_id}",
        "X-Tenant-ID": str(tenant_id),
        **forged,
    }


def brand_body(name: str = "Northstar") -> dict[str, object]:
    return {
        "name": name,
        "slug": name.lower(),
        "website_url": "https://northstar.example",
        "status": "active",
        "profile": {
            "industry": "Outdoor",
            "description": "SENTINEL-CONFIDENTIAL-BRAND",
            "brand_positioning": "Equipment for considered travel",
            "brand_voice": "Precise",
            "tone_attributes": ["calm"],
            "visual_style_keywords": ["editorial"],
            "target_markets": ["US"],
            "primary_language": "en",
            "allowed_claims": ["Reusable"],
            "prohibited_claims": ["Unbreakable"],
            "competitors": ["Alternative"],
        },
    }


def product_body() -> dict[str, object]:
    audience = {
        "name": "Urban commuters",
        "description": "People moving through cities",
        "pain_points": ["Disposable waste"],
        "desires": ["Durability"],
        "motivations": ["Lower waste"],
        "objections": ["Price"],
    }
    return {
        "name": "Atlas",
        "slug": "atlas",
        "sku": "ATLAS-1",
        "category": "Drinkware",
        "short_description": "SENTINEL-CONFIDENTIAL-PRODUCT",
        "status": "draft",
        "profile": {
            "description": "Insulated steel bottle",
            "features": ["Double wall"],
            "benefits": ["Cold all day"],
            "materials": ["Steel"],
            "variants": ["Charcoal"],
            "price": "29.90",
            "currency": "USD",
            "estimated_margin": "0.4200",
            "target_audiences": [audience],
            "problems_solved": ["Warm drinks"],
            "use_cases": ["Commute"],
            "differentiators": ["Repairable lid"],
            "purchase_objections": ["Price"],
            "allowed_claims": ["Reusable"],
            "prohibited_claims": ["Cures dehydration"],
            "shipping_summary": "Ships boxed",
            "seasonality_notes": None,
            "landing_page_url": "https://northstar.example/atlas",
            "competitor_product_refs": [],
        },
        "brief": {
            "product_why": "Reduce disposable bottle use",
            "emotional_benefits": ["Prepared"],
            "primary_audience": audience,
            "secondary_audiences": [],
            "positioning_statement": "The repairable commuter bottle",
            "competitive_alternatives": ["Disposable bottles"],
            "why_choose_us": ["Repairable"],
            "current_channels": [],
            "priority_channels": ["Instagram"],
            "conversion_goal": "Purchase",
            "offers": [],
            "cta_preferences": ["Shop now"],
            "desired_creative_style": "Editorial utility",
            "tones_to_explore": ["Direct"],
            "tones_to_avoid": ["Alarmist"],
            "creative_references": [],
            "mandatory_messaging": [],
            "prohibited_messaging": ["Health cure"],
            "required_disclaimers": [],
            "legal_safety_constraints": [],
            "geographical_restrictions": [],
        },
    }


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_owner_api_creates_product_brain_and_immutable_snapshots(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_id, user_id = await seed_catalog_identity(admin_engine)
    async for http in client(runtime_database_url):
        brand_response = await http.post(
            "/v1/brands", headers=headers(tenant_id, user_id), json=brand_body()
        )
        assert brand_response.status_code == 201
        brand_id = brand_response.json()["id"]
        assert len((await http.get("/v1/brands", headers=headers(tenant_id, user_id))).json()) == 1
        assert (
            await http.get(f"/v1/brands/{brand_id}", headers=headers(tenant_id, user_id))
        ).status_code == 200
        changed_brand = brand_body()
        changed_brand["website_url"] = "https://northstar.example/about"
        assert (
            await http.patch(
                f"/v1/brands/{brand_id}",
                headers=headers(tenant_id, user_id),
                json=changed_brand,
            )
        ).status_code == 200
        created = await http.post(
            f"/v1/brands/{brand_id}/products",
            headers=headers(tenant_id, user_id),
            json=product_body(),
        )
        assert created.status_code == 201
        workspace = created.json()
        assert workspace["completeness"]["score"] == 100
        product_id = workspace["product"]["id"]
        assert (
            len(
                (
                    await http.get(
                        f"/v1/brands/{brand_id}/products", headers=headers(tenant_id, user_id)
                    )
                ).json()
            )
            == 1
        )
        assert (
            await http.get(f"/v1/products/{product_id}", headers=headers(tenant_id, user_id))
        ).status_code == 200
        product_update = product_body()
        product_update.pop("brief")
        product_update["status"] = "active"
        assert (
            await http.patch(
                f"/v1/products/{product_id}",
                headers=headers(tenant_id, user_id),
                json=product_update,
            )
        ).status_code == 200
        assert (
            await http.get(
                f"/v1/products/{product_id}/brief/completeness",
                headers=headers(tenant_id, user_id),
            )
        ).json()["score"] == 100
        assert (
            await http.get(f"/v1/products/{product_id}/brief", headers=headers(tenant_id, user_id))
        ).status_code == 200
        first = (
            await http.post(
                f"/v1/products/{product_id}/snapshots", headers=headers(tenant_id, user_id)
            )
        ).json()
        brief = cast(dict[str, object], product_body()["brief"]).copy()
        brief["product_why"] = "A changed strategic reason"
        assert (
            await http.put(
                f"/v1/products/{product_id}/brief", headers=headers(tenant_id, user_id), json=brief
            )
        ).status_code == 200
        second = (
            await http.post(
                f"/v1/products/{product_id}/snapshots", headers=headers(tenant_id, user_id)
            )
        ).json()
        assert (
            first["digest"] != second["digest"]
            and first["source_revision"] == 1
            and second["source_revision"] == 2
        )
        assert (
            await http.get(
                f"/v1/products/{product_id}/snapshots/latest",
                headers=headers(tenant_id, user_id),
            )
        ).json()["id"] == second["id"]
    async with admin_engine.begin() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT digest, source_revision FROM "
                        "catalog.product_knowledge_snapshots ORDER BY source_revision"
                    )
                )
            )
            .tuples()
            .all()
        )
        assert rows == [(first["digest"], 1), (second["digest"], 2)]
        evidence = str(
            (await connection.execute(text("SELECT safe_metadata FROM audit.audit_records")))
            .scalars()
            .all()
        ) + str(
            (await connection.execute(text("SELECT payload FROM event_delivery.outbox_events")))
            .scalars()
            .all()
        )
        assert "SENTINEL-CONFIDENTIAL" not in evidence


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_member_is_read_only_and_forged_browser_role_is_irrelevant(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_id, user_id = await seed_catalog_identity(admin_engine, role="member")
    async for http in client(runtime_database_url):
        response = await http.post(
            "/v1/brands",
            headers=headers(tenant_id, user_id, **{"X-Membership-Role": "owner"}),
            json=brand_body(),
        )
        assert (
            response.status_code == 403 and response.json()["detail"] == "catalog_mutation_denied"
        )
        assert (
            await http.get("/v1/brands", headers=headers(tenant_id, user_id))
        ).status_code == 200


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_cross_tenant_known_ids_are_invisible_and_same_tenant_fk_blocks_attachment(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_a, user_a = await seed_catalog_identity(admin_engine)
    tenant_b, user_b = await seed_catalog_identity(admin_engine)
    async for http in client(runtime_database_url):
        brand_b = (
            await http.post(
                "/v1/brands", headers=headers(tenant_b, user_b), json=brand_body("Otherbrand")
            )
        ).json()
        assert (
            await http.get(f"/v1/brands/{brand_b['id']}", headers=headers(tenant_a, user_a))
        ).status_code == 404
        assert (
            await http.post(
                f"/v1/brands/{brand_b['id']}/products",
                headers=headers(tenant_a, user_a),
                json=product_body(),
            )
        ).status_code == 404
    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "INSERT INTO catalog.products "
                    "(id,tenant_id,brand_id,name,slug,category,status,created_by) "
                    "VALUES (:id,:tenant,:brand,'Cross','cross','Test','draft',:user)"
                ),
                {"id": uuid4(), "tenant": tenant_a, "brand": UUID(brand_b["id"]), "user": user_a},
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_missing_tenant_context_and_snapshot_mutation_fail_closed(
    admin_engine: AsyncEngine, runtime_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_id, user_id = await seed_catalog_identity(admin_engine)
    async for http in client(runtime_database_url):
        brand = (
            await http.post("/v1/brands", headers=headers(tenant_id, user_id), json=brand_body())
        ).json()
        workspace = (
            await http.post(
                f"/v1/brands/{brand['id']}/products",
                headers=headers(tenant_id, user_id),
                json=product_body(),
            )
        ).json()
        snapshot = (
            await http.post(
                f"/v1/products/{workspace['product']['id']}/snapshots",
                headers=headers(tenant_id, user_id),
            )
        ).json()
    async with runtime_engine.begin() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM catalog.products")) == 0
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE catalog.product_knowledge_snapshots SET source_revision=9 WHERE id=:id"
                ),
                {"id": UUID(snapshot["id"])},
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_product_state_audit_and_outbox_rollback_atomically(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_id, user_id = await seed_catalog_identity(admin_engine)
    async for http in client(runtime_database_url):
        brand = (
            await http.post("/v1/brands", headers=headers(tenant_id, user_id), json=brand_body())
        ).json()

        async with admin_engine.begin() as connection:
            counts_before = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM audit.audit_records "
                        "WHERE action LIKE 'catalog.%'), "
                        "(SELECT count(*) FROM event_delivery.outbox_events)"
                    )
                )
            ).one()
            await connection.execute(
                text(
                    "CREATE FUNCTION catalog.test_reject_product_event() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN "
                    "IF NEW.event_type = 'catalog.product.created.v1' THEN "
                    "RAISE EXCEPTION 'forced outbox failure'; END IF; RETURN NEW; END; $$"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER test_reject_product_event BEFORE INSERT ON "
                    "event_delivery.outbox_events FOR EACH ROW EXECUTE FUNCTION "
                    "catalog.test_reject_product_event()"
                )
            )

        try:
            with pytest.raises(DBAPIError, match="forced outbox failure"):
                await http.post(
                    f"/v1/brands/{brand['id']}/products",
                    headers=headers(tenant_id, user_id),
                    json=product_body(),
                )
        finally:
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text("DROP TRIGGER test_reject_product_event ON event_delivery.outbox_events")
                )
                await connection.execute(text("DROP FUNCTION catalog.test_reject_product_event()"))

        async with admin_engine.begin() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM catalog.products")) == 0
            counts_after = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM audit.audit_records "
                        "WHERE action LIKE 'catalog.%'), "
                        "(SELECT count(*) FROM event_delivery.outbox_events)"
                    )
                )
            ).one()
            assert counts_after == counts_before
