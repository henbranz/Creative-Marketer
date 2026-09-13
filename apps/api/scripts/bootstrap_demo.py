"""Create the deterministic, no-billing local product walkthrough."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    CreateTenantAgentDefinition,
    ListTenantAgentDefinitions,
    ResolveActiveAgentVersion,
)
from creative_marketer.agent_governance.domain import AgentUnavailable
from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    WorkloadIdentity,
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import ModelInvocationResult, ModelUsage
from creative_marketer.assembly.application import AssemblyService
from creative_marketer.catalog.application import CatalogService
from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    AssetKind,
    AssetRole,
    AssetStatus,
    RightsStatus,
)
from creative_marketer.catalog.domain import (
    Audience,
    Brand,
    BrandProfile,
    Product,
    ProductBrief,
    ProductProfile,
    ProductStatus,
)
from creative_marketer.creative.application import CreativeService
from creative_marketer.creative.domain import (
    ChannelIntent,
    CreativeDecisionState,
    CreativeStrategyRequest,
)
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.domain import (
    ExternalIdentity,
    Membership,
    MembershipRole,
    MembershipStatus,
    Tenant,
    User,
)
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.assembly_uow import (
    SqlAlchemyAssemblyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.catalog_uow import (
    SqlAlchemyCatalogUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.creative_uow import (
    SqlAlchemyCreativeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.production_uow import (
    SqlAlchemyProductionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.research_uow import (
    SqlAlchemyResearchUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.uow import SqlAlchemyUnitOfWorkFactory
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.infrastructure.object_storage import S3ObjectStore
from creative_marketer.permission_governance.application import (
    ActivateToolPermissionVersion,
    CreateToolPermission,
    CreateToolPermissionVersion,
)
from creative_marketer.permission_governance.domain import (
    PermissionEffect,
    ToolPermissionVersionConfiguration,
)
from creative_marketer.production.application import initial_producer_route
from creative_marketer.production.domain import ProductionPlanningRequest
from creative_marketer.production.infrastructure.fakes import DEMO_MP4
from creative_marketer.production.tool_contracts import media_tool_contracts
from creative_marketer.research.application import FetchedPage, ResearchService
from creative_marketer.research.domain import ResearchCategory
from creative_marketer.tool_governance.application import ResolveActiveTool
from creative_marketer_api.config import Settings
from scripts.bootstrap_creative_strategist import creative_strategist_configuration
from scripts.bootstrap_media_tools import run as bootstrap_media_tools
from scripts.bootstrap_producer import producer_configuration
from scripts.bootstrap_researcher import researcher_configuration

TENANT_ID = uuid5(NAMESPACE_URL, "creative-marketer:local-demo:tenant")
USER_ID = uuid5(NAMESPACE_URL, "creative-marketer:local-demo:user")
BRAND_ID = uuid5(NAMESPACE_URL, "creative-marketer:local-demo:brand")
PRODUCT_ID = uuid5(NAMESPACE_URL, "creative-marketer:local-demo:product")
ISSUER = "local-demo"
SUBJECT = "owner"
PNG = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+Xz2vWQAAAABJRU5ErkJggg=="
)


class DemoFetcher:
    async def fetch(self, requested_url: str) -> FetchedPage:
        return FetchedPage(
            requested_url,
            requested_url,
            200,
            "text/html",
            b"<h1>LOCAL DEMO audience evidence</h1>"
            b"<p>Commuters value durable reusable products.</p>",
            datetime.now(UTC),
        )


class DemoWorkload:
    async def current(self) -> WorkloadIdentity:
        return WorkloadIdentity("local-demo-agent-worker", "development")


def _context() -> ExecutionContext:
    return ExecutionContext(
        TENANT_ID,
        Actor(ActorKind.USER, USER_ID),
        USER_ID,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "development",
        AuthenticationAssurance(datetime.now(UTC), "development-token", "explicit"),
    )


async def _identity(factory: SqlAlchemyUnitOfWorkFactory) -> None:
    async with factory() as uow:
        if await uow.users.get(USER_ID) is None:
            await uow.users.add(
                User(
                    "local-demo@creative-marketer.invalid",
                    "local-demo@creative-marketer.invalid",
                    USER_ID,
                )
            )
        if await uow.external_identities.get(ISSUER, SUBJECT) is None:
            await uow.external_identities.add(ExternalIdentity(USER_ID, ISSUER, SUBJECT))
        await uow.commit()
    async with factory(TenantContext(TENANT_ID)) as uow:
        if await uow.tenants.get(TENANT_ID) is None:
            await uow.tenants.add(Tenant("LOCAL DEMO", "local-demo", TENANT_ID))
        if await uow.memberships.get(USER_ID) is None:
            await uow.memberships.add(Membership(TENANT_ID, USER_ID, MembershipRole.OWNER))
        await uow.commit()


async def _agent(
    factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    agent_type: str,
    key: str,
    configuration: object,
) -> UUID:
    context = _context()
    matches = [
        item
        for item in await ListTenantAgentDefinitions(factory)(context)
        if item.agent_type == agent_type
    ]
    definition = (
        matches[0]
        if matches
        else await CreateTenantAgentDefinition(factory)(
            context, agent_key=key, agent_type=agent_type
        )
    )
    try:
        active = await ResolveActiveAgentVersion(factory)(context, definition.id)
    except AgentUnavailable:
        active = None
    if active is None or active.configuration_digest != configuration.configuration_digest:  # type: ignore[attr-defined]
        version = await CreateAgentVersion(factory)(context, definition.id, configuration)  # type: ignore[arg-type]
        await ActivateAgentVersion(factory)(context, definition.id, version.id)
    return definition.id


def _creative_output(invocation: object) -> dict[str, object]:
    sections = invocation.capability_context  # type: ignore[attr-defined]
    research = sections["research_findings"][0]
    claim = sections["product_claim_refs"][0]
    concepts = []
    for index in range(3):
        concepts.append(
            {
                "concept_key": f"local_demo_{index + 1}",
                "title": f"LOCAL DEMO concept {index + 1}",
                "format": "SHORT_FORM_VIDEO",
                "channel_intent": "TIKTOK",
                "creative_angle": f"Durability in daily motion {index + 1}",
                "strategic_rationale": "Grounded in the captured commuter evidence.",
                "target_audience": "Busy commuters",
                "hook": {
                    "spoken_or_voiceover": f"Built for commute routine {index + 1}.",
                    "on_screen_text": "One bottle. Every day.",
                    "visual_open": f"Bottle reveal variation {index + 1} beside a train pass.",
                },
                "estimated_duration_seconds": 15,
                "scenes": [
                    {
                        "ordinal": ordinal,
                        "estimated_duration_seconds": 5,
                        "purpose": purpose,
                        "visual_direction": direction,
                        "on_screen_text": None,
                        "voiceover": voice,
                        "message_point_refs": ["durable_claim"],
                        "asset_requirements": [],
                    }
                    for ordinal, purpose, direction, voice in (
                        (
                            1,
                            "Hook",
                            f"Fast tabletop reveal variation {index + 1}",
                            "Built for real routines.",
                        ),
                        (2, "Proof", "Close product detail", "Durable and reusable."),
                        (3, "Action", "Product exits frame", "Take it everywhere."),
                    )
                ],
                "cta": {"text": "See the bottle", "intent": "DISCOVER"},
                "hypothesis": "A tangible routine-first hook improves hold rate.",
                "primary_success_metric": "HOOK_HOLD_RATE",
                "supporting_research_refs": [
                    {
                        "research_snapshot_id": sections["research_snapshot_id"],
                        "finding_key": research["key"],
                    }
                ],
                "message_points": [
                    {
                        "key": "durable_claim",
                        "kind": "PRODUCT_FACT",
                        "text": claim["text"],
                        "product_claim_ref": claim["key"],
                    }
                ],
                "required_assets": [
                    {
                        "kind": "EXISTING_ASSET",
                        "asset_id": sections["available_assets"][0]["asset_id"],
                        "intended_role": "product hero",
                    }
                ],
                "required_disclaimers": ["LOCAL DEMO imagery"],
                "production_notes": "LOCAL DEMO — use the governed fake providers.",
            }
        )
    return {"concepts": concepts, "strategy_limitations": ["LOCAL DEMO fixture"]}


def _production_output(invocation: object) -> dict[str, object]:
    sections = invocation.capability_context  # type: ignore[attr-defined]
    asset_id = sections["selected_assets"][0]["asset_id"]
    base = {
        "visual_objective": "Show the product",
        "subject": "Bottle",
        "environment": "Commuter desk",
        "composition": "Centered",
        "framing": "Close",
        "lens_intent": "Natural",
        "camera_position": "Eye level",
        "camera_motion": "Slow push",
        "lighting": "Soft daylight",
        "action": "Rotate",
        "continuity_requirements": ["Same bottle"],
        "product_preservation_constraints": ["Keep label readable"],
        "audio_intent": None,
    }
    image_spec = {
        "subject": "Bottle",
        "environment": "Commuter desk",
        "composition": "Centered",
        "camera_framing": "Close",
        "lighting": "Soft daylight",
        "style": "Editorial",
        "product_preservation_constraints": ["Keep label readable"],
        "reference_asset_ids": [asset_id],
        "aspect_ratio": "9:16",
        "quality_intent": "HIGH",
        "negative_constraints": ["No distortion"],
    }
    scenes = []
    for ordinal, key in enumerate(sections["concept_scene_keys"], 1):
        strategy = (
            "GENERATE_IMAGE"
            if ordinal == 1
            else "GENERATE_VIDEO"
            if ordinal == 2
            else "MANUAL_CAPTURE"
        )
        scenes.append(
            {
                "scene_key": key,
                "ordinal": ordinal,
                "purpose": ("Hook", "Proof", "Action")[ordinal - 1],
                "duration_seconds": 5,
                "message": "LOCAL DEMO product story",
                "voiceover": None,
                "on_screen_text": None,
                "shots": [
                    {
                        **base,
                        "shot_key": f"local_demo_shot_{ordinal}",
                        "scene_key": key,
                        "ordinal": 1,
                        "source_strategy": strategy,
                        "existing_asset_id": None,
                        "image_generation_spec": image_spec if ordinal == 1 else None,
                    }
                ],
            }
        )
    spec = {
        "subject": "Bottle",
        "environment": "Commuter desk",
        "composition": "Centered",
        "camera_motion": "Slow push",
        "lighting": "Soft daylight",
        "action": "Rotate",
        "product_preservation_constraints": ["Keep label readable"],
        "negative_constraints": ["No distortion"],
        "generate_audio": False,
        "resolution": "720p",
        "aspect_ratio": "9:16",
    }
    return {
        "strategy": "LOCAL DEMO governed image and video production.",
        "format": {"kind": "SHORT_FORM_VERTICAL_VIDEO", "aspect_ratio": "9:16"},
        "scenes": scenes,
        "generation_segments": [
            {
                "segment_key": "local_demo_image",
                "shot_keys": ["local_demo_shot_1"],
                "media_kind": "IMAGE",
                "duration_seconds": None,
                "continuity": [],
                "reference_asset_ids": [asset_id],
                "generation_spec": spec,
            },
            {
                "segment_key": "local_demo_video",
                "shot_keys": ["local_demo_shot_2"],
                "media_kind": "VIDEO",
                "duration_seconds": 5,
                "continuity": ["Same bottle"],
                "reference_asset_ids": [asset_id],
                "generation_spec": spec,
            },
        ],
        "required_assets": ["LOCAL DEMO voiceover"],
    }


async def run() -> None:
    settings = Settings()
    if settings.app_env != "development":
        raise SystemExit("Demo bootstrap is development-only")
    if settings.object_storage_backend != "s3":
        raise SystemExit("Demo bootstrap requires OBJECT_STORAGE_BACKEND=s3 (make dev-up)")
    sessions = create_session_factory(str(settings.database_url))
    identity = SqlAlchemyUnitOfWorkFactory(sessions)
    await _identity(identity)
    context = _context()
    catalog_factory = SqlAlchemyCatalogUnitOfWorkFactory(sessions)
    catalog = CatalogService(catalog_factory)
    existing = [item for item in await catalog.list_products(context) if item.id == PRODUCT_ID]
    store = S3ObjectStore(
        endpoint_url=str(settings.object_storage_endpoint_url),
        public_endpoint_url=str(settings.object_storage_public_endpoint_url),
        region=settings.object_storage_region,
        bucket=settings.object_storage_bucket,
        access_key_id=settings.object_storage_access_key_id,
        secret_access_key=settings.object_storage_secret_access_key.get_secret_value(),
    )
    await store.ensure_private_bucket([str(value) for value in settings.cors_origins])
    if not existing:
        audience = Audience(
            "Busy commuters",
            "People replacing disposable bottles",
            ("Disposable waste",),
            ("Reliable hydration",),
            ("Durability",),
            ("Cleaning effort",),
        )
        brand = Brand(TENANT_ID, "LOCAL DEMO Everyday", "local-demo-everyday", USER_ID, BRAND_ID)
        await catalog.create_brand(
            context,
            brand,
            BrandProfile(
                TENANT_ID,
                BRAND_ID,
                industry="Consumer goods",
                description="LOCAL DEMO brand",
                brand_positioning="Durable daily essentials",
                brand_voice="Clear and practical",
                tone_attributes=("direct",),
                visual_style_keywords=("editorial",),
                target_markets=("Local demo",),
                allowed_claims=("Made for daily reuse",),
                prohibited_claims=("Guaranteed results",),
            ),
        )
        product = Product(
            TENANT_ID,
            BRAND_ID,
            "LOCAL DEMO Commuter Bottle",
            "local-demo-commuter-bottle",
            "Drinkware",
            USER_ID,
            PRODUCT_ID,
            sku="LOCAL-DEMO-001",
            short_description="A visible, no-billing production walkthrough.",
            status=ProductStatus.ACTIVE,
        )
        profile = ProductProfile(
            TENANT_ID,
            PRODUCT_ID,
            description="A durable reusable bottle for everyday commuting.",
            features=("Repairable lid", "Double wall"),
            benefits=("Keeps drinks cool",),
            materials=("Recycled steel",),
            variants=("Graphite",),
            price=Decimal("39.00"),
            currency="USD",
            estimated_margin=Decimal("0.55"),
            target_audiences=(audience,),
            problems_solved=("Disposable bottle waste",),
            use_cases=("Daily commute",),
            differentiators=("Repairable components",),
            purchase_objections=("Higher initial price",),
            allowed_claims=("Made from recycled steel",),
            prohibited_claims=("Guaranteed results",),
            shipping_summary="LOCAL DEMO shipping",
            seasonality_notes="Evergreen",
            landing_page_url="https://example.com/local-demo",
        )
        brief = ProductBrief(
            TENANT_ID,
            PRODUCT_ID,
            product_why="Reduce disposable bottle waste.",
            emotional_benefits=("Prepared",),
            primary_audience=audience,
            positioning_statement="A durable bottle for real routines.",
            competitive_alternatives=("Disposable bottles",),
            why_choose_us=("Repairable lid",),
            current_channels=("Website",),
            priority_channels=("TikTok",),
            conversion_goal="Product detail visits",
            offers=("None",),
            cta_preferences=("Discover",),
            desired_creative_style="Editorial utility",
            tones_to_explore=("Confident",),
            tones_to_avoid=("Sensational",),
            creative_references=("Daily carry",),
            mandatory_messaging=("Made from recycled steel",),
            prohibited_messaging=("Guaranteed results",),
            required_disclaimers=("LOCAL DEMO imagery",),
            legal_safety_constraints=("No unsupported claims",),
            geographical_restrictions=("None",),
        )
        await catalog.create_product(context, product, profile, brief)
        assets = AssetService(catalog_factory, store)
        for role in (AssetRole.PRODUCT_HERO, AssetRole.PRODUCT_DETAIL):
            await assets.ingest_generated(
                context,
                brand_id=BRAND_ID,
                product_id=PRODUCT_ID,
                kind=AssetKind.IMAGE,
                role=role,
                content=PNG,
                media_type="image/png",
                rights_status=RightsStatus.CONFIRMED,
                allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
                original_filename=f"LOCAL-DEMO-{role.value}.png",
            )
        research = ResearchService(
            SqlAlchemyResearchUnitOfWorkFactory(sessions), DemoFetcher(), store
        )
        await research.create_source(
            context,
            product_id=PRODUCT_ID,
            url="https://example.com/local-demo-evidence",
            display_name="LOCAL DEMO audience evidence",
            category=ResearchCategory.MARKET_REFERENCE,
        )
    asset_service = AssetService(catalog_factory, store)
    current_images = await asset_service.list(
        context, product_id=PRODUCT_ID, kind=AssetKind.IMAGE, status=AssetStatus.READY
    )
    current_roles = {item.role for item in current_images}
    for role in (AssetRole.PRODUCT_HERO, AssetRole.PRODUCT_DETAIL):
        if role not in current_roles:
            await asset_service.ingest_generated(
                context,
                brand_id=BRAND_ID,
                product_id=PRODUCT_ID,
                kind=AssetKind.IMAGE,
                role=role,
                content=PNG,
                media_type="image/png",
                rights_status=RightsStatus.CONFIRMED,
                allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
                original_filename=f"LOCAL-DEMO-{role.value}.png",
            )
    current_images = await asset_service.list(
        context, product_id=PRODUCT_ID, kind=AssetKind.IMAGE, status=AssetStatus.READY
    )
    async with catalog_factory(context) as uow:
        latest_snapshot = await uow.snapshots.latest(PRODUCT_ID)
    raw_snapshot_assets = (
        latest_snapshot.content.get("assets", ()) if latest_snapshot is not None else ()
    )
    snapshot_assets = raw_snapshot_assets if isinstance(raw_snapshot_assets, (list, tuple)) else ()
    snapshot_asset_ids = {
        str(item.get("asset_id")) for item in snapshot_assets if isinstance(item, dict)
    }
    current_image_ids = {str(item.id) for item in current_images}
    if latest_snapshot is None or not current_image_ids.issubset(snapshot_asset_ids):
        await catalog.create_snapshot(context, PRODUCT_ID)
    research = ResearchService(SqlAlchemyResearchUnitOfWorkFactory(sessions), DemoFetcher(), store)
    if not await research.list_sources(context, PRODUCT_ID):
        await research.create_source(
            context,
            product_id=PRODUCT_ID,
            url="https://example.com/local-demo-evidence",
            display_name="LOCAL DEMO audience evidence",
            category=ResearchCategory.MARKET_REFERENCE,
        )
    videos = await asset_service.list(
        context, product_id=PRODUCT_ID, kind=AssetKind.VIDEO, status=AssetStatus.READY
    )
    manual_asset = (
        videos[0]
        if videos
        else await asset_service.ingest_generated(
            context,
            brand_id=BRAND_ID,
            product_id=PRODUCT_ID,
            kind=AssetKind.VIDEO,
            role=AssetRole.PRODUCTION_REFERENCE,
            content=DEMO_MP4,
            media_type="video/mp4",
            rights_status=RightsStatus.CONFIRMED,
            allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
            original_filename="LOCAL-DEMO-manual-source.mp4",
        )
    )
    registry = SqlAlchemyAgentRegistryUnitOfWorkFactory(sessions)
    await _agent(registry, "researcher", "researcher", researcher_configuration())
    await _agent(
        registry, "creative_strategist", "creative_strategist", creative_strategist_configuration()
    )
    producer_id = await _agent(registry, "producer", "astra_producer", producer_configuration())
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter(
            (
                initial_researcher_route(),
                initial_creative_strategist_route(),
                initial_producer_route(),
            )
        ),
        ModelProviderRegistry(
            {
                "openai": FakeModelProvider(
                    lambda invocation: ModelInvocationResult(
                        (
                            {
                                "findings": [
                                    {
                                        "key": "audience_language",
                                        "category": "audience",
                                        "statement": "Commuters value durable reusable products.",
                                        "confidence": "HIGH",
                                        "citations": [
                                            {
                                                "evidence_snapshot_id": str(
                                                    invocation.untrusted_evidence[
                                                        0
                                                    ].evidence_snapshot_id
                                                ),
                                                "block_index": invocation.untrusted_evidence[
                                                    0
                                                ].block_index,
                                                "block_digest": invocation.untrusted_evidence[
                                                    0
                                                ].block_digest,
                                            }
                                        ],
                                        "scope": "LOCAL DEMO evidence",
                                        "implication": "Show durability in a daily routine.",
                                    }
                                ],
                                "research_gaps": ["LOCAL DEMO has intentionally bounded evidence."],
                                "recommended_next_sources": [],
                            }
                            if invocation.output_contract_key == "research.research_snapshot"
                            else _creative_output(invocation)
                            if invocation.output_contract_key == "creative.creative_concept_set"
                            else _production_output(invocation)
                        ),
                        f"local-demo-{invocation.output_contract_key}",
                        ModelUsage(100, 100, 200),
                        "openai",
                        "gpt-6-astra"
                        if invocation.output_contract_key == "production.production_plan"
                        else "gpt-5.6-terra",
                    )
                )
            }
        ),
        DemoWorkload(),
    )
    snapshots = await runtime.list_snapshots(context, PRODUCT_ID)
    if not snapshots:
        pending = await runtime.request_researcher(
            context, product_id=PRODUCT_ID, idempotency_key="local-demo-research-v1"
        )
        await runtime.execute(TENANT_ID, pending.id)
    creative = CreativeService(SqlAlchemyCreativeUnitOfWorkFactory(sessions))
    sets = await creative.list_sets(context, PRODUCT_ID)
    if not sets:
        pending = await runtime.request_creative_strategist(
            context,
            product_id=PRODUCT_ID,
            request=CreativeStrategyRequest(3, ChannelIntent.TIKTOK),
            idempotency_key="local-demo-creative-v2",
        )
        await runtime.execute(TENANT_ID, pending.id)
        sets = await creative.list_sets(context, PRODUCT_ID)
    concept = sets[0].concepts[0]
    _, decision = await creative.get_concept(context, concept.id)
    if decision is None:
        await creative.decide(
            context,
            concept.id,
            CreativeDecisionState.APPROVED_FOR_PRODUCTION,
            note="LOCAL DEMO approval",
        )
    production_factory = SqlAlchemyProductionUnitOfWorkFactory(sessions)
    async with production_factory(TENANT_ID) as uow:
        plans = await uow.production.list_plans(PRODUCT_ID)
    if not plans:
        pending = await runtime.request_producer(
            context,
            concept_id=concept.id,
            request=ProductionPlanningRequest(),
            idempotency_key="local-demo-production-v1",
        )
        await runtime.execute(TENANT_ID, pending.id)
        async with production_factory(TENANT_ID) as uow:
            plans = await uow.production.list_plans(PRODUCT_ID)
    assembly = AssemblyService(SqlAlchemyAssemblyUnitOfWorkFactory(sessions))
    async with SqlAlchemyAssemblyUnitOfWorkFactory(sessions)(TENANT_ID) as uow:
        source = await uow.assembly.production_input(plans[0].plan.id)
    if source is not None:
        manual_shot = next(
            (
                shot
                for scene in source.scenes
                for shot in scene.shots
                if shot.source_strategy == "MANUAL_CAPTURE"
            ),
            None,
        )
        if manual_shot is not None and manual_shot.manual_source is None:
            await assembly.bind_manual_source(context, manual_shot.id, manual_asset.id)
    __import__("os").environ["BOOTSTRAP_PLATFORM_ACTOR_ID"] = str(
        uuid5(NAMESPACE_URL, "creative-marketer:local-demo:platform")
    )
    await bootstrap_media_tools()
    tool_factory = SqlAlchemyToolRegistryUnitOfWorkFactory(sessions)
    permission_factory = SqlAlchemyPermissionUnitOfWorkFactory(sessions)
    policy = ToolPermissionVersionConfiguration(
        PermissionEffect.GRANT, ("production.media",), ("development",)
    )
    for key in (contract.tool_key for contract in media_tool_contracts()):
        tool = await ResolveActiveTool(tool_factory)(key)
        async with permission_factory(context.tenant_context()) as uow:
            existing_permission = await uow.permissions.get_for_subject(
                producer_id, tool.definition_id
            )
        if existing_permission is None:
            permission = await CreateToolPermission(permission_factory)(
                context, producer_id, tool.definition_id
            )
            version = await CreateToolPermissionVersion(permission_factory)(
                context, permission.id, policy
            )
            await ActivateToolPermissionVersion(permission_factory)(
                context, permission.id, version.id
            )
    print(
        "\nCreative Marketer local demo ready.\n\nTenant ID:\n"
        + str(TENANT_ID)
        + "\n\nDevelopment credential:\n"
        + ISSUER
        + "|"
        + SUBJECT
        + "\n\nUI:\nhttp://localhost:3000"
    )


if __name__ == "__main__":
    asyncio.run(run())
