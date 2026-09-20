import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
    agent_versions,
)
from creative_marketer.infrastructure.database.agent_runtime_schema import (
    agent_runs,
    research_snapshots,
)
from creative_marketer.infrastructure.database.assembly_schema import (
    assembly_items,
    assembly_jobs,
    assembly_plans,
    final_creative_decisions,
    final_creatives,
)
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    brands,
    product_knowledge_snapshots,
    products,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    action_proposals as commerce_action_proposals,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    action_results as commerce_action_results,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    connections as commerce_connections,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    product_mappings as commerce_product_mappings,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    reports as commerce_reports,
)
from creative_marketer.infrastructure.database.creative_schema import (
    concept_decisions,
    concept_sets,
    concepts,
)
from creative_marketer.infrastructure.database.intelligence_schema import (
    context_manifests,
    experiment_decisions,
    experiment_proposals,
    insight_candidates,
    insight_decisions,
)
from creative_marketer.infrastructure.database.intelligence_schema import (
    reports as intelligence_reports,
)
from creative_marketer.infrastructure.database.knowledge_schema import (
    projection_changes,
    projection_nodes,
)
from creative_marketer.infrastructure.database.measurement_schema import (
    attribution_results,
    performance_snapshots,
)
from creative_marketer.infrastructure.database.orchestration_schema import (
    creative_cycles,
    supervisor_reports,
)
from creative_marketer.infrastructure.database.production_schema import (
    asset_lineage,
    generation_jobs,
    generation_segments,
    plan_decisions,
    production_plans,
    production_shots,
)
from creative_marketer.infrastructure.database.publishing_schema import (
    publication_decisions,
    publication_drafts,
    publications,
    social_accounts,
)
from creative_marketer.infrastructure.database.research_schema import (
    evidence_snapshots,
    research_targets,
    social_evidence_snapshots,
    sources,
)
from creative_marketer.knowledge.domain import (
    KnowledgeChange,
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeNodeRef,
    KnowledgeNodeType,
    KnowledgeRelationship,
)


def _json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json(child) for child in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _rel(
    node_type: KnowledgeNodeType, canonical_id: object, relationship: str
) -> KnowledgeRelationship:
    return KnowledgeRelationship(KnowledgeNodeRef(node_type, str(canonical_id)), relationship)


def _time(row: Mapping[str, Any], key: str, fallback: str = "created_at") -> datetime:
    return cast(datetime, row.get(key) or row[fallback])


def _finding_id(snapshot_id: object, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"creative-marketer:research-finding:{snapshot_id}:{key}"))


class SqlAlchemyCanonicalKnowledgeReader:
    """Cross-context read adapter. It never writes canonical context tables."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def graph(self, context: ExecutionContext) -> KnowledgeGraph:
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            tenant = context.tenant_id
            rows: dict[str, list[Mapping[str, Any]]] = {}
            statements = {
                "brands": select(brands).where(brands.c.tenant_id == tenant),
                "products": select(products).where(products.c.tenant_id == tenant),
                "product_snapshots": select(product_knowledge_snapshots).where(
                    product_knowledge_snapshots.c.tenant_id == tenant
                ),
                "assets": select(assets).where(assets.c.tenant_id == tenant),
                "definitions": select(agent_definitions).where(
                    or_(
                        agent_definitions.c.tenant_id == tenant,
                        agent_definitions.c.tenant_id.is_(None),
                    )
                ),
                "versions": select(agent_versions).where(
                    or_(agent_versions.c.tenant_id == tenant, agent_versions.c.tenant_id.is_(None))
                ),
                "activations": select(agent_activations).where(
                    or_(
                        agent_activations.c.tenant_id == tenant,
                        agent_activations.c.tenant_id.is_(None),
                    )
                ),
                "runs": select(agent_runs).where(agent_runs.c.tenant_id == tenant),
                "sources": select(sources).where(sources.c.tenant_id == tenant),
                "evidence": select(evidence_snapshots).where(
                    evidence_snapshots.c.tenant_id == tenant
                ),
                "research_targets": select(research_targets).where(
                    research_targets.c.tenant_id == tenant
                ),
                "social_evidence": select(social_evidence_snapshots).where(
                    social_evidence_snapshots.c.tenant_id == tenant
                ),
                "research_snapshots": select(research_snapshots).where(
                    research_snapshots.c.tenant_id == tenant
                ),
                "concept_sets": select(concept_sets).where(concept_sets.c.tenant_id == tenant),
                "concepts": select(concepts).where(concepts.c.tenant_id == tenant),
                "decisions": select(concept_decisions).where(
                    concept_decisions.c.tenant_id == tenant
                ),
                "production_plans": select(production_plans).where(
                    production_plans.c.tenant_id == tenant
                ),
                "production_shots": select(production_shots).where(
                    production_shots.c.tenant_id == tenant
                ),
                "generation_segments": select(generation_segments).where(
                    generation_segments.c.tenant_id == tenant
                ),
                "plan_decisions": select(plan_decisions).where(
                    plan_decisions.c.tenant_id == tenant
                ),
                "generation_jobs": select(generation_jobs).where(
                    generation_jobs.c.tenant_id == tenant
                ),
                "asset_lineage": select(asset_lineage).where(asset_lineage.c.tenant_id == tenant),
                "assembly_plans": select(assembly_plans).where(
                    assembly_plans.c.tenant_id == tenant
                ),
                "assembly_items": select(assembly_items).where(
                    assembly_items.c.tenant_id == tenant
                ),
                "assembly_jobs": select(assembly_jobs).where(assembly_jobs.c.tenant_id == tenant),
                "final_creatives": select(final_creatives).where(
                    final_creatives.c.tenant_id == tenant
                ),
                "final_creative_decisions": select(final_creative_decisions).where(
                    final_creative_decisions.c.tenant_id == tenant
                ),
                "social_accounts": select(social_accounts).where(
                    social_accounts.c.tenant_id == tenant
                ),
                "publication_drafts": select(publication_drafts).where(
                    publication_drafts.c.tenant_id == tenant
                ),
                "publication_decisions": select(publication_decisions).where(
                    publication_decisions.c.tenant_id == tenant
                ),
                "publications": select(publications).where(publications.c.tenant_id == tenant),
                "performance_snapshots": select(performance_snapshots).where(
                    performance_snapshots.c.tenant_id == tenant
                ),
                "attribution_results": select(attribution_results).where(
                    attribution_results.c.tenant_id == tenant
                ),
                "intelligence_manifests": select(context_manifests).where(
                    context_manifests.c.tenant_id == tenant
                ),
                "intelligence_reports": select(intelligence_reports).where(
                    intelligence_reports.c.tenant_id == tenant
                ),
                "insight_candidates": select(insight_candidates).where(
                    insight_candidates.c.tenant_id == tenant
                ),
                "insight_decisions": select(insight_decisions).where(
                    insight_decisions.c.tenant_id == tenant
                ),
                "experiment_proposals": select(experiment_proposals).where(
                    experiment_proposals.c.tenant_id == tenant
                ),
                "experiment_decisions": select(experiment_decisions).where(
                    experiment_decisions.c.tenant_id == tenant
                ),
                "creative_cycles": select(creative_cycles).where(
                    creative_cycles.c.tenant_id == tenant
                ),
                "supervisor_reports": select(supervisor_reports).where(
                    supervisor_reports.c.tenant_id == tenant
                ),
                "commerce_connections": select(commerce_connections).where(
                    commerce_connections.c.tenant_id == tenant
                ),
                "commerce_mappings": select(commerce_product_mappings).where(
                    commerce_product_mappings.c.tenant_id == tenant
                ),
                "commerce_reports": select(commerce_reports).where(
                    commerce_reports.c.tenant_id == tenant
                ),
                "commerce_proposals": select(commerce_action_proposals).where(
                    commerce_action_proposals.c.tenant_id == tenant
                ),
                "commerce_results": select(commerce_action_results).where(
                    commerce_action_results.c.tenant_id == tenant
                ),
            }
            for key, statement in statements.items():
                result = (await session.execute(statement)).mappings()
                rows[key] = [cast(Mapping[str, Any], row) for row in result]
        return self._build(rows)

    def _build(self, rows: Mapping[str, list[Mapping[str, Any]]]) -> KnowledgeGraph:
        nodes: list[KnowledgeNode] = []
        product_names = {row["id"]: row["name"] for row in rows["products"]}
        active_versions = {
            row["definition_id"]: row["active_version_id"] for row in rows["activations"]
        }
        decisions_by_concept: dict[object, list[Mapping[str, Any]]] = {}
        for row in rows["decisions"]:
            decisions_by_concept.setdefault(row["concept_id"], []).append(row)
        concept_sets_by_id = {row["id"]: row for row in rows["concept_sets"]}
        evidence_by_finding: dict[tuple[str, str], tuple[str, ...]] = {}
        social_evidence_ids = {str(row["id"]) for row in rows.get("social_evidence", [])}
        for snapshot in rows["research_snapshots"]:
            for finding in snapshot["findings"] or []:
                evidence_by_finding[(str(snapshot["id"]), finding["key"])] = tuple(
                    citation["evidence_snapshot_id"] for citation in finding.get("citations", [])
                )

        for row in rows["brands"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.BRAND,
                    str(row["id"]),
                    row["name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {"slug": row["slug"], "website_url": row["website_url"]},
                )
            )
        decisions_by_plan: dict[object, list[Mapping[str, Any]]] = {}
        for decision in rows.get("plan_decisions", []):
            decisions_by_plan.setdefault(decision["production_plan_id"], []).append(decision)
        for row in rows.get("production_plans", []):
            latest = max(
                decisions_by_plan.get(row["id"], []),
                key=lambda item: item["created_at"],
                default=None,
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCTION_PLAN,
                    str(row["id"]),
                    f"Production Plan {str(row['id'])[:8]}",
                    latest["state"] if latest else "UNREVIEWED",
                    row["created_at"],
                    latest["created_at"] if latest else row["created_at"],
                    {
                        "strategy": row["strategy"],
                        "estimated_max_video_cost": str(row["estimated_max_video_cost"]),
                        "estimated_max_image_cost": str(row["estimated_max_image_cost"]),
                        "currency": row["currency"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.CREATIVE_CONCEPT,
                            row["concept_id"],
                            "planned_from_concept",
                        ),
                        _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                        _rel(
                            KnowledgeNodeType.PRODUCT, row["product_id"], "production_for_product"
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        segments_by_plan: dict[object, list[Mapping[str, Any]]] = {}
        for segment in rows.get("generation_segments", []):
            segments_by_plan.setdefault(segment["production_plan_id"], []).append(segment)
            relationships = [
                _rel(
                    KnowledgeNodeType.PRODUCTION_PLAN,
                    segment["production_plan_id"],
                    "segment_of_plan",
                )
            ]
            relationships.extend(
                _rel(
                    KnowledgeNodeType.PRODUCTION_SHOT,
                    f"{segment['production_plan_id']}:{key}",
                    "executes_shot",
                )
                for key in segment["shot_keys"]
            )
            relationships.extend(
                _rel(
                    KnowledgeNodeType.ASSET,
                    value["asset_id"] if isinstance(value, dict) else value,
                    "uses_reference_asset",
                )
                for value in segment["reference_assets"]
                if (isinstance(value, str) or (isinstance(value, dict) and value.get("asset_id")))
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.GENERATION_SEGMENT,
                    str(segment["id"]),
                    segment["segment_key"],
                    "immutable",
                    segment["created_at"],
                    segment["created_at"],
                    {
                        "media_kind": segment["media_kind"],
                        "duration_seconds": segment["duration_seconds"],
                    },
                    tuple(relationships),
                    segment["generation_spec_digest"],
                )
            )
        for shot in rows.get("production_shots", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCTION_SHOT,
                    f"{shot['production_plan_id']}:{shot['shot_key']}",
                    shot["shot_key"],
                    shot["source_strategy"],
                    shot["created_at"],
                    shot["created_at"],
                    {
                        "source_strategy": shot["source_strategy"],
                        "specification": shot["specification"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.PRODUCTION_PLAN,
                            shot["production_plan_id"],
                            "shot_of_plan",
                        ),
                    ),
                )
            )
        for job in rows.get("generation_jobs", []):
            relationships = [
                _rel(KnowledgeNodeType.PRODUCTION_PLAN, job["production_plan_id"], "job_for_plan"),
                _rel(
                    KnowledgeNodeType.GENERATION_SEGMENT,
                    job["generation_segment_id"],
                    "executes_segment",
                ),
            ]
            if job["output_asset_id"]:
                relationships.append(
                    _rel(KnowledgeNodeType.ASSET, job["output_asset_id"], "produced_asset")
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.GENERATION_JOB,
                    str(job["id"]),
                    f"{job['kind'].title()} Generation {str(job['id'])[:8]}",
                    job["status"],
                    job["created_at"],
                    job["updated_at"],
                    {
                        "kind": job["kind"],
                        "provider": job["provider"],
                        "model": job["model"],
                        "route_version": job["route_version"],
                        "pricing_version": job["pricing_version"],
                        "actual_cost": str(job["actual_cost"]),
                        "unknown_cost": str(job["unknown_cost"]),
                        "currency": job["currency"],
                    },
                    tuple(relationships),
                )
            )
        assembly_items_by_plan: dict[object, list[Mapping[str, Any]]] = {}
        for item in rows.get("assembly_items", []):
            assembly_items_by_plan.setdefault(item["assembly_plan_id"], []).append(item)
        for row in rows.get("assembly_plans", []):
            items = sorted(
                assembly_items_by_plan.get(row["id"], []), key=lambda item: item["ordinal"]
            )
            relationships = [
                _rel(KnowledgeNodeType.PRODUCTION_PLAN, row["production_plan_id"], "assembles_plan")
            ]
            relationships.extend(
                _rel(KnowledgeNodeType.ASSET, item["source_asset_id"], "uses_source_asset")
                for item in items
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.ASSEMBLY_PLAN,
                    str(row["id"]),
                    f"Assembly Plan {str(row['id'])[:8]}",
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {
                        "timeline_duration_ms": row["timeline_duration_ms"],
                        "render_profile": row["render_profile_key"],
                        "render_profile_version": row["render_profile_version"],
                        "timeline": [
                            {
                                "item_key": item["item_key"],
                                "source_kind": item["source_kind"],
                                "shot_keys": item["production_shot_keys"],
                                "start_ms": item["timeline_start_ms"],
                                "duration_ms": item["timeline_duration_ms"],
                                "audio_policy": item["audio_behavior"],
                            }
                            for item in items
                        ],
                    },
                    tuple(relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("assembly_jobs", []):
            relationships = [
                _rel(KnowledgeNodeType.ASSEMBLY_PLAN, row["assembly_plan_id"], "executes_assembly")
            ]
            if row["output_asset_id"]:
                relationships.append(
                    _rel(KnowledgeNodeType.ASSET, row["output_asset_id"], "produced_asset")
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.ASSEMBLY_JOB,
                    str(row["id"]),
                    f"Assembly Job {str(row['id'])[:8]}",
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "renderer": row["renderer"],
                        "renderer_version": row["renderer_version"],
                        "failure_code": row["failure_code"],
                    },
                    tuple(relationships),
                )
            )
        decisions_by_final: dict[object, list[Mapping[str, Any]]] = {}
        for row in rows.get("final_creative_decisions", []):
            decisions_by_final.setdefault(row["final_creative_id"], []).append(row)
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.FINAL_CREATIVE_DECISION,
                    str(row["id"]),
                    row["state"].replace("_", " ").title(),
                    row["state"],
                    row["created_at"],
                    row["created_at"],
                    {},
                    (
                        _rel(
                            KnowledgeNodeType.FINAL_CREATIVE,
                            row["final_creative_id"],
                            "decision_for_final_creative",
                        ),
                    ),
                )
            )
        for row in rows.get("final_creatives", []):
            latest = max(
                decisions_by_final.get(row["id"], []),
                key=lambda item: item["created_at"],
                default=None,
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.FINAL_CREATIVE,
                    str(row["id"]),
                    f"Final Creative {str(row['id'])[:8]}",
                    latest["state"] if latest else "AWAITING_REVIEW",
                    row["created_at"],
                    latest["created_at"] if latest else row["created_at"],
                    {
                        "duration_ms": row["duration_ms"],
                        "resolution": f"{row['width']}x{row['height']}",
                        "fps": row["fps"],
                        "has_audio": row["has_audio"],
                        "source_count": row["source_count"],
                        "render_profile": row["render_profile_key"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.ASSEMBLY_PLAN,
                            row["assembly_plan_id"],
                            "created_from_assembly",
                        ),
                        _rel(
                            KnowledgeNodeType.PRODUCTION_PLAN,
                            row["production_plan_id"],
                            "created_from_production",
                        ),
                        _rel(
                            KnowledgeNodeType.CREATIVE_CONCEPT,
                            row["creative_concept_id"],
                            "created_from_concept",
                        ),
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "final_for_product"),
                        _rel(KnowledgeNodeType.ASSET, row["output_asset_id"], "final_asset"),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows["products"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCT,
                    str(row["id"]),
                    row["name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "category": row["category"],
                        "sku": row["sku"],
                        "description": row["short_description"],
                    },
                    (_rel(KnowledgeNodeType.BRAND, row["brand_id"], "belongs_to_brand"),),
                )
            )
        for row in rows["product_snapshots"]:
            product_name = product_names.get(row["product_id"], "Product")
            title = f"{product_name} Knowledge v{row['source_revision']}"
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                    str(row["id"]),
                    title,
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {
                        "schema_version": row["schema_version"],
                        "source_revision": row["source_revision"],
                        "content": row["content"],
                    },
                    (_rel(KnowledgeNodeType.PRODUCT, row["product_id"], "snapshot_of"),),
                    row["digest"],
                )
            )
        for row in rows["assets"]:
            relationships = [_rel(KnowledgeNodeType.BRAND, row["brand_id"], "belongs_to_brand")]
            if row["product_id"]:
                relationships.append(
                    _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "belongs_to_product")
                )
            for lineage in rows.get("asset_lineage", []):
                if lineage["child_asset_id"] == row["id"]:
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.ASSET,
                            lineage["parent_asset_id"],
                            lineage["relationship_type"].casefold(),
                        )
                    )
            generating_job = next(
                (
                    job
                    for job in rows.get("generation_jobs", [])
                    if job["output_asset_id"] == row["id"]
                ),
                None,
            )
            if generating_job is not None:
                relationships.extend(
                    (
                        _rel(
                            KnowledgeNodeType.GENERATION_JOB,
                            generating_job["id"],
                            "generated_by_job",
                        ),
                        _rel(
                            KnowledgeNodeType.PRODUCTION_PLAN,
                            generating_job["production_plan_id"],
                            "generated_for_plan",
                        ),
                    )
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.ASSET,
                    str(row["id"]),
                    row["original_filename"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "kind": row["kind"],
                        "role": row["role"],
                        "rights_status": row["rights_status"],
                        "allowed_uses": row["allowed_uses"],
                        "byte_size": row["byte_size"],
                        "digest": row["digest"],
                    },
                    tuple(relationships),
                    row["digest"],
                )
            )
        for row in rows["definitions"]:
            active_version = next(
                (
                    version
                    for version in rows["versions"]
                    if version["id"] == active_versions.get(row["id"])
                ),
                None,
            )
            relations = tuple(
                _rel(KnowledgeNodeType.AGENT_VERSION, version["id"], "has_version")
                for version in rows["versions"]
                if version["definition_id"] == row["id"]
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.AGENT_DEFINITION,
                    str(row["id"]),
                    row["agent_key"].replace("_", " ").title(),
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "agent_key": row["agent_key"],
                        "agent_type": row["agent_type"],
                        "scope": row["scope_kind"],
                        "active_version_id": active_versions.get(row["id"]),
                        "mission": active_version["mission"] if active_version else None,
                        "version": active_version["version_number"] if active_version else None,
                        "model_profile": (
                            (active_version["model_policy"] or {}).get("profile_key")
                            if active_version
                            else None
                        ),
                        "inputs_consumed": (
                            active_version["read_scopes"] if active_version else []
                        ),
                        "outputs_produced": (
                            active_version["output_contract_key"] if active_version else None
                        ),
                        "downstream_capabilities": {
                            "researcher": ["creative_strategist"],
                            "creative_strategist": ["producer"],
                        }.get(row["agent_type"], []),
                    },
                    relations,
                )
            )
        for row in rows["versions"]:
            policy = row["model_policy"] or {}
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.AGENT_VERSION,
                    str(row["id"]),
                    f"{row['display_name']} v{row['version_number']}",
                    "active"
                    if active_versions.get(row["definition_id"]) == row["id"]
                    else "historical",
                    row["created_at"],
                    row["created_at"],
                    {
                        "version": row["version_number"],
                        "mission": row["mission"],
                        "responsibilities": row["responsibilities"],
                        "model_profile": policy.get("profile_key"),
                        "required_capabilities": policy.get("required_capabilities", []),
                        "inputs_consumed": row["read_scopes"],
                        "outputs_produced": {
                            "contract": row["output_contract_key"],
                            "version": row["output_contract_version"],
                        },
                        "memory_scopes": row["memory_scopes"],
                        "downstream_capabilities": {
                            "researcher": ["creative_strategist"],
                            "creative_strategist": ["producer"],
                        }.get(
                            next(
                                (
                                    definition["agent_type"]
                                    for definition in rows["definitions"]
                                    if definition["id"] == row["definition_id"]
                                ),
                                "",
                            ),
                            [],
                        ),
                    },
                    (_rel(KnowledgeNodeType.AGENT_DEFINITION, row["definition_id"], "version_of"),),
                    row["configuration_digest"],
                )
            )
        for row in rows["runs"]:
            relationships = [
                _rel(KnowledgeNodeType.AGENT_VERSION, row["agent_version_id"], "executed_as"),
                _rel(
                    KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                    row["product_snapshot_id"],
                    "consumed_product_snapshot",
                ),
            ]
            for ref in row["input_context_refs"] or []:
                if ref.get("kind") == "research_snapshot" and ref.get("id"):
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.RESEARCH_SNAPSHOT,
                            ref["id"],
                            "consumed_research_snapshot",
                        )
                    )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.AGENT_RUN,
                    str(row["id"]),
                    f"{str(row['agent_type']).replace('_', ' ').title()} Run {str(row['id'])[:8]}",
                    row["status"],
                    row["created_at"],
                    _time(row, "completed_at"),
                    {
                        "agent_type": row["agent_type"],
                        "agent_version_number": row["agent_version_number"],
                        "model_profile": row["model_profile_key"],
                        "provider": row["resolved_provider"],
                        "model": row["resolved_model"],
                        "started_at": row["started_at"],
                        "completed_at": row["completed_at"],
                        "token_usage": {
                            "input": row["input_tokens"],
                            "output": row["output_tokens"],
                            "total": row["total_tokens"],
                        },
                        "estimated_cost": format(row["estimated_cost"], "f"),
                        "currency": row["currency"],
                        "inputs": row["input_context_refs"],
                        "result_ref": row["result_ref"],
                    },
                    tuple(relationships),
                )
            )
        for row in rows["sources"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.RESEARCH_SOURCE,
                    str(row["id"]),
                    row["display_name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "category": row["category"],
                        "source_type": row["source_type"],
                        "url": row["canonical_url"],
                    },
                    (_rel(KnowledgeNodeType.PRODUCT, row["product_id"], "researches_product"),),
                )
            )
        for row in rows["evidence"]:
            title = row["title"] or f"Evidence {str(row['id'])[:8]}"
            blocks = [
                {
                    "kind": block.get("kind"),
                    "ordinal": block.get("ordinal"),
                    "text": block.get("text"),
                }
                for block in (row["content_blocks"] or [])
            ]
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.EVIDENCE_SNAPSHOT,
                    str(row["id"]),
                    title,
                    "immutable",
                    row["captured_at"],
                    row["captured_at"],
                    {
                        "url": row["final_url"],
                        "captured_at": row["captured_at"],
                        "extracted_text": blocks,
                        "instruction_like_content": row["instruction_like_content"],
                    },
                    (
                        _rel(KnowledgeNodeType.RESEARCH_SOURCE, row["source_id"], "evidence_from"),
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "evidence_for_product"),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("research_targets", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.RESEARCH_TARGET,
                    str(row["id"]),
                    row["display_name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "kind": row["kind"],
                        "website_url": row["website_url"],
                        "platform": row["platform"],
                        "platform_handle": row["platform_handle"],
                        "platform_profile_url": row["platform_profile_url"],
                        "platform_identifier": row["platform_identifier"],
                    },
                    (_rel(KnowledgeNodeType.PRODUCT, row["product_id"], "researches_product"),),
                )
            )
        for row in rows.get("social_evidence", []):
            title = (
                row["headline"]
                or row["advertiser_name"]
                or (f"{str(row['platform']).title()} evidence {str(row['id'])[:8]}")
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.SOCIAL_EVIDENCE_SNAPSHOT,
                    str(row["id"]),
                    title[:500],
                    "immutable",
                    row["captured_at"],
                    row["captured_at"],
                    {
                        "platform": row["platform"],
                        "evidence_type": row["evidence_type"],
                        "provenance": row["provenance"],
                        "source_url": row["source_url"],
                        "destination_url": row["destination_url"],
                        "advertiser_name": row["advertiser_name"],
                        "platform_content_id": row["platform_content_id"],
                        "headline": row["headline"],
                        "body_text": row["body_text"],
                        "cta": row["cta"],
                        "media_type": row["media_type"],
                        "placements": row["placements"],
                        "activity_status": row["activity_status"],
                        "region": row["region"],
                        "reach_range": row["reach_range"],
                        "source_provider": row["source_provider"],
                        "rights_status": row["rights_status"],
                        "allowed_uses": row["allowed_uses"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.RESEARCH_TARGET,
                            row["research_target_id"],
                            "evidence_from_target",
                        ),
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "evidence_for_product"),
                        *(
                            (
                                _rel(
                                    KnowledgeNodeType.ASSET,
                                    row["media_asset_id"],
                                    "references_asset",
                                ),
                            )
                            if row["media_asset_id"]
                            else ()
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows["research_snapshots"]:
            findings = row["findings"] or []
            finding_rels = tuple(
                _rel(
                    KnowledgeNodeType.RESEARCH_FINDING,
                    _finding_id(row["id"], item["key"]),
                    "contains_finding",
                )
                for item in findings
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.RESEARCH_SNAPSHOT,
                    str(row["id"]),
                    f"Research Snapshot {str(row['id'])[:8]}",
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {
                        "schema_version": row["schema_version"],
                        "valid_until": row["valid_until"],
                        "research_gaps": row["research_gaps"],
                        "recommended_next_sources": row["recommended_next_sources"],
                    },
                    (
                        _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            row["product_snapshot_id"],
                            "researches_snapshot",
                        ),
                        *finding_rels,
                    ),
                    row["semantic_digest"],
                )
            )
            for finding in findings:
                evidence_rels = tuple(
                    _rel(
                        (
                            KnowledgeNodeType.SOCIAL_EVIDENCE_SNAPSHOT
                            if str(citation["evidence_snapshot_id"]) in social_evidence_ids
                            else KnowledgeNodeType.EVIDENCE_SNAPSHOT
                        ),
                        citation["evidence_snapshot_id"],
                        "supported_by_evidence",
                    )
                    for citation in finding.get("citations", [])
                )
                nodes.append(
                    KnowledgeNode(
                        KnowledgeNodeType.RESEARCH_FINDING,
                        _finding_id(row["id"], finding["key"]),
                        finding["statement"][:160],
                        "current",
                        row["created_at"],
                        row["created_at"],
                        {
                            "finding_key": finding["key"],
                            "category": finding["category"],
                            "statement": finding["statement"],
                            "confidence": finding["confidence"],
                            "scope": finding["scope"],
                            "implication": finding.get("implication"),
                            "citations": finding.get("citations", []),
                        },
                        (
                            _rel(KnowledgeNodeType.RESEARCH_SNAPSHOT, row["id"], "finding_of"),
                            *evidence_rels,
                        ),
                    )
                )
        for row in rows["concept_sets"]:
            concept_set_relationships = [
                _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                _rel(
                    KnowledgeNodeType.RESEARCH_SNAPSHOT,
                    row["research_snapshot_id"],
                    "consumed_research_snapshot",
                ),
                _rel(
                    KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                    row["product_snapshot_id"],
                    "consumed_product_snapshot",
                ),
            ]
            if row.get("experiment_proposal_id"):
                concept_set_relationships.append(
                    _rel(
                        KnowledgeNodeType.EXPERIMENT_PROPOSAL,
                        row["experiment_proposal_id"],
                        "tests_experiment_proposal",
                    )
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CONCEPT_SET,
                    str(row["id"]),
                    f"Creative Concept Set {str(row['id'])[:8]}",
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {"schema_version": row["schema_version"]},
                    tuple(concept_set_relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows["concepts"]:
            payload = row["concept_payload"] or {}
            latest = max(
                decisions_by_concept.get(row["id"], []),
                key=lambda item: item["created_at"],
                default=None,
            )
            relationships = [
                _rel(
                    KnowledgeNodeType.CREATIVE_CONCEPT_SET,
                    row["concept_set_id"],
                    "belongs_to_concept_set",
                ),
                _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "concept_for_product"),
            ]
            concept_set = concept_sets_by_id.get(row["concept_set_id"])
            if concept_set:
                relationships.extend(
                    (
                        _rel(
                            KnowledgeNodeType.AGENT_RUN,
                            concept_set["agent_run_id"],
                            "produced_by_run",
                        ),
                        _rel(
                            KnowledgeNodeType.RESEARCH_SNAPSHOT,
                            concept_set["research_snapshot_id"],
                            "uses_research_snapshot",
                        ),
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            concept_set["product_snapshot_id"],
                            "uses_product_snapshot",
                        ),
                    )
                )
            for ref in payload.get("supporting_research_refs", []):
                relationships.append(
                    _rel(
                        KnowledgeNodeType.RESEARCH_FINDING,
                        _finding_id(ref["research_snapshot_id"], ref["finding_key"]),
                        "supported_by_finding",
                    )
                )
                relationships.extend(
                    _rel(
                        (
                            KnowledgeNodeType.SOCIAL_EVIDENCE_SNAPSHOT
                            if str(evidence_id) in social_evidence_ids
                            else KnowledgeNodeType.EVIDENCE_SNAPSHOT
                        ),
                        evidence_id,
                        "supported_by_evidence",
                    )
                    for evidence_id in evidence_by_finding.get(
                        (str(ref["research_snapshot_id"]), ref["finding_key"]), ()
                    )
                )
            requirements = list(payload.get("required_assets", []))
            for scene in payload.get("scenes", []):
                requirements.extend(scene.get("asset_requirements", []))
            for requirement in requirements:
                if requirement.get("kind") == "EXISTING_ASSET" and requirement.get("asset_id"):
                    relationships.append(
                        _rel(KnowledgeNodeType.ASSET, requirement["asset_id"], "requires_asset")
                    )
            for message in payload.get("message_points", []):
                if message.get("product_claim_ref") and concept_set:
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            concept_set["product_snapshot_id"],
                            "claim_provenance",
                        )
                    )
            for decision in decisions_by_concept.get(row["id"], []):
                relationships.append(
                    _rel(
                        KnowledgeNodeType.CREATIVE_CONCEPT_DECISION, decision["id"], "has_decision"
                    )
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CONCEPT,
                    str(row["id"]),
                    payload.get("title") or row["concept_key"],
                    latest["state"] if latest else "UNREVIEWED",
                    row["created_at"],
                    latest["created_at"] if latest else row["created_at"],
                    payload,
                    tuple(relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows["decisions"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CONCEPT_DECISION,
                    str(row["id"]),
                    f"{row['state'].replace('_', ' ').title()} Decision",
                    row["state"],
                    row["created_at"],
                    row["created_at"],
                    {"reason_code": row["reason_code"], "note": row["note"]},
                    (_rel(KnowledgeNodeType.CREATIVE_CONCEPT, row["concept_id"], "decision_for"),),
                )
            )
        for row in rows.get("social_accounts", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.SOCIAL_ACCOUNT,
                    str(row["id"]),
                    f"{row['platform'].title()} · {row['display_name']}",
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "platform": row["platform"],
                        "display_name": row["display_name"],
                        "username": row["username"],
                        "provider": row["provider"],
                    },
                )
            )
        decisions_by_draft = {
            row["publication_draft_id"]: row for row in rows.get("publication_decisions", [])
        }
        publications_by_draft = {
            row["publication_draft_id"]: row for row in rows.get("publications", [])
        }
        for row in rows.get("publication_drafts", []):
            publication_decision = decisions_by_draft.get(row["id"])
            publication = publications_by_draft.get(row["id"])
            relationships = [
                _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "publishes_product"),
                _rel(
                    KnowledgeNodeType.FINAL_CREATIVE,
                    row["final_creative_id"],
                    "publishes_final_creative",
                ),
                _rel(KnowledgeNodeType.ASSET, row["output_asset_id"], "publishes_asset"),
                _rel(
                    KnowledgeNodeType.SOCIAL_ACCOUNT,
                    row["social_account_id"],
                    "targets_account",
                ),
            ]
            if publication_decision:
                relationships.append(
                    _rel(
                        KnowledgeNodeType.PUBLICATION_DECISION,
                        publication_decision["id"],
                        "has_publication_decision",
                    )
                )
            if publication:
                relationships.append(
                    _rel(
                        KnowledgeNodeType.PUBLICATION,
                        publication["id"],
                        "resulted_in_publication",
                    )
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PUBLICATION_DRAFT,
                    str(row["id"]),
                    f"{row['platform'].title()} Publication Draft",
                    publication_decision["state"] if publication_decision else "PENDING_APPROVAL",
                    row["created_at"],
                    publication_decision["created_at"]
                    if publication_decision
                    else row["created_at"],
                    {
                        "platform": row["platform"],
                        "mode": row["mode"],
                        "scheduled_at": row["scheduled_at"],
                        "hashtags": row["hashtags"],
                        "caption_digest": "sha256:"
                        + hashlib.sha256(row["caption"].encode()).hexdigest(),
                    },
                    tuple(relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("publication_decisions", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PUBLICATION_DECISION,
                    str(row["id"]),
                    f"Publication {row['state'].title()}",
                    row["state"],
                    row["created_at"],
                    row["created_at"],
                    {"action_digest": row["action_digest"]},
                    (
                        _rel(
                            KnowledgeNodeType.PUBLICATION_DRAFT,
                            row["publication_draft_id"],
                            "decision_for",
                        ),
                    ),
                    row["action_digest"],
                )
            )
        snapshots_by_publication: dict[object, list[Mapping[str, Any]]] = {}
        for snapshot in rows.get("performance_snapshots", []):
            snapshots_by_publication.setdefault(snapshot["publication_id"], []).append(snapshot)
        for row in rows.get("publications", []):
            publication_relationships = [
                _rel(
                    KnowledgeNodeType.PUBLICATION_DRAFT,
                    row["publication_draft_id"],
                    "published_from",
                ),
                _rel(
                    KnowledgeNodeType.FINAL_CREATIVE,
                    row["final_creative_id"],
                    "published_final_creative",
                ),
                _rel(
                    KnowledgeNodeType.SOCIAL_ACCOUNT,
                    row["social_account_id"],
                    "published_to",
                ),
            ]
            publication_relationships.extend(
                _rel(KnowledgeNodeType.PERFORMANCE_SNAPSHOT, snapshot["id"], "measured_by")
                for snapshot in snapshots_by_publication.get(row["id"], [])
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PUBLICATION,
                    str(row["id"]),
                    f"Published to {row['platform'].title()}",
                    row["status"],
                    row["created_at"],
                    row["published_at"] or row["created_at"],
                    {
                        "platform": row["platform"],
                        "provider": row["provider"],
                        "external_post_id": row["external_post_id"],
                        "canonical_permalink": row["canonical_permalink"],
                    },
                    tuple(publication_relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("performance_snapshots", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PERFORMANCE_SNAPSHOT,
                    str(row["id"]),
                    "Performance Snapshot",
                    row["freshness"],
                    row["created_at"],
                    row["created_at"],
                    {
                        "latest_metrics": row["latest_metrics"],
                        "derived_metrics": row["derived_metrics"],
                        "attributed_conversions": row["attributed_conversions"],
                        "attributed_revenue": row["attributed_revenue"],
                        "freshness": row["freshness"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.PUBLICATION,
                            row["publication_id"],
                            "measures_publication",
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("attribution_results", []):
            digest = (
                "sha256:"
                + hashlib.sha256(
                    f"{row['id']}:{row['method']}:{row['formula_version']}".encode()
                ).hexdigest()
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.ATTRIBUTION_RESULT,
                    str(row["id"]),
                    "Direct Reference Attribution",
                    "ATTRIBUTED",
                    row["created_at"],
                    row["created_at"],
                    {"method": row["method"], "formula_version": row["formula_version"]},
                    (
                        _rel(
                            KnowledgeNodeType.PUBLICATION,
                            row["publication_id"],
                            "attributes_to_publication",
                        ),
                    ),
                    digest,
                )
            )
        manifests = {row["id"]: row for row in rows.get("intelligence_manifests", [])}
        candidate_decisions = {
            row["candidate_id"]: row for row in rows.get("insight_decisions", [])
        }
        proposal_decisions = {
            row["proposal_id"]: row for row in rows.get("experiment_decisions", [])
        }
        proposals_by_report: dict[object, list[Mapping[str, Any]]] = {}
        for proposal in rows.get("experiment_proposals", []):
            proposals_by_report.setdefault(proposal["report_id"], []).append(proposal)
        for row in rows.get("intelligence_reports", []):
            manifest = manifests.get(row["context_manifest_id"])
            relationships = [
                _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "analyzes_product"),
                _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
            ]
            if manifest:
                for ref in manifest["manifest"].get("performance_snapshots", []):
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.PERFORMANCE_SNAPSHOT,
                            ref["id"],
                            "analyzes_performance_snapshot",
                        )
                    )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.INTELLIGENCE_REPORT,
                    str(row["id"]),
                    f"Intelligence Report {str(row['id'])[:8]}",
                    row["data_trust_level"],
                    row["created_at"],
                    row["created_at"],
                    {
                        "data_trust_level": row["data_trust_level"],
                        "summary": row["summary"],
                        "observations": row["observations"],
                        "comparative_findings": row["comparative_findings"],
                        "limitations": row["limitations"],
                        "source_publications": manifest["manifest"].get("publications", [])
                        if manifest
                        else [],
                        "experiment_proposal_ids": [
                            str(item["id"]) for item in proposals_by_report.get(row["id"], [])
                        ],
                    },
                    tuple(relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("insight_candidates", []):
            candidate_decision = candidate_decisions.get(row["id"])
            status = candidate_decision["decision"] if candidate_decision else row["status"]
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.INSIGHT_CANDIDATE,
                    str(row["id"]),
                    row["statement"][:100],
                    status,
                    row["created_at"],
                    candidate_decision["created_at"] if candidate_decision else row["created_at"],
                    {
                        "statement": row["statement"],
                        "scope": row["scope"],
                        "evidence_refs": row["evidence_refs"],
                        "sample_size": row["sample_size"],
                        "metric": row["metric"],
                        "confidence": row["confidence"],
                        "limitations": row["limitations"],
                        "data_trust_level": row["data_trust_level"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.INTELLIGENCE_REPORT,
                            row["report_id"],
                            "candidate_from_report",
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        concept_sets_by_proposal: dict[object, list[Mapping[str, Any]]] = {}
        for concept_set in rows.get("concept_sets", []):
            if concept_set.get("experiment_proposal_id"):
                concept_sets_by_proposal.setdefault(
                    concept_set["experiment_proposal_id"], []
                ).append(concept_set)
        for row in rows.get("experiment_proposals", []):
            proposal_decision = proposal_decisions.get(row["id"])
            relationships = [
                _rel(
                    KnowledgeNodeType.INTELLIGENCE_REPORT,
                    row["report_id"],
                    "proposed_from_report",
                ),
                *[
                    _rel(KnowledgeNodeType.INSIGHT_CANDIDATE, item, "tests_candidate")
                    for item in row["candidate_ids"]
                ],
                *[
                    _rel(
                        KnowledgeNodeType.CREATIVE_CONCEPT_SET,
                        item["id"],
                        "resulted_in_concept_set",
                    )
                    for item in concept_sets_by_proposal.get(row["id"], [])
                ],
            ]
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.EXPERIMENT_PROPOSAL,
                    str(row["id"]),
                    row["hypothesis"][:100],
                    proposal_decision["decision"] if proposal_decision else "PROPOSED",
                    row["created_at"],
                    proposal_decision["created_at"] if proposal_decision else row["created_at"],
                    {
                        "hypothesis": row["hypothesis"],
                        "primary_variable": row["primary_variable"],
                        "controlled_elements": row["controlled_elements"],
                        "target_metric": row["target_metric"],
                        "platform": row["platform"],
                        "measurement_window": row["measurement_window"],
                        "creative_direction": row["creative_direction"],
                        "rationale": row["rationale"],
                        "expected_learning": row["expected_learning"],
                        "data_trust_level": row["data_trust_level"],
                        "resulting_concept_set_ids": [
                            str(item["id"]) for item in concept_sets_by_proposal.get(row["id"], [])
                        ],
                    },
                    tuple(relationships),
                    row["semantic_digest"],
                )
            )
        reports_by_cycle: dict[object, list[Mapping[str, Any]]] = {}
        for report in rows.get("supervisor_reports", []):
            reports_by_cycle.setdefault(report["cycle_id"], []).append(report)
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.SUPERVISOR_REPORT,
                    str(report["id"]),
                    f"Supervisor Report {str(report['id'])[:8]}",
                    "CREATED",
                    report["created_at"],
                    report["created_at"],
                    {
                        "summary": report["summary"],
                        "current_stage_explanation": report["current_stage_explanation"],
                        "blockers": report["blockers"],
                        "attention_items": report["attention_items"],
                        "suggested_next_actions": report["suggested_next_actions"],
                        "completion_summary": report["completion_summary"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.CREATIVE_CYCLE,
                            report["cycle_id"],
                            "explains_cycle",
                        ),
                    ),
                    report["semantic_digest"],
                )
            )
        artifact_types = {
            "research_snapshot_id": KnowledgeNodeType.RESEARCH_SNAPSHOT,
            "creative_concept_set_id": KnowledgeNodeType.CREATIVE_CONCEPT_SET,
            "approved_concept_id": KnowledgeNodeType.CREATIVE_CONCEPT,
            "production_plan_id": KnowledgeNodeType.PRODUCTION_PLAN,
            "final_creative_id": KnowledgeNodeType.FINAL_CREATIVE,
            "publication_draft_id": KnowledgeNodeType.PUBLICATION_DRAFT,
            "publication_id": KnowledgeNodeType.PUBLICATION,
            "performance_snapshot_id": KnowledgeNodeType.PERFORMANCE_SNAPSHOT,
            "intelligence_report_id": KnowledgeNodeType.INTELLIGENCE_REPORT,
            "experiment_proposal_id": KnowledgeNodeType.EXPERIMENT_PROPOSAL,
        }
        for row in rows.get("creative_cycles", []):
            bindings = row["artifact_bindings"] or {}
            relationships = [
                _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "coordinates_product"),
                _rel(
                    KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                    row["product_snapshot_id"],
                    "binds_product_snapshot",
                ),
            ]
            relationships.extend(
                _rel(node_type, bindings[key], "binds_artifact")
                for key, node_type in artifact_types.items()
                if bindings.get(key)
            )
            if row["parent_cycle_id"]:
                relationships.append(
                    _rel(
                        KnowledgeNodeType.CREATIVE_CYCLE,
                        row["parent_cycle_id"],
                        "continues_cycle",
                    )
                )
            if row["source_experiment_proposal_id"]:
                relationships.append(
                    _rel(
                        KnowledgeNodeType.EXPERIMENT_PROPOSAL,
                        row["source_experiment_proposal_id"],
                        "started_from_experiment",
                    )
                )
            relationships.extend(
                _rel(KnowledgeNodeType.SUPERVISOR_REPORT, report["id"], "has_report")
                for report in reports_by_cycle.get(row["id"], [])
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CYCLE,
                    str(row["id"]),
                    f"Creative Cycle {str(row['id'])[:8]}",
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "current_stage": row["current_stage"],
                        "mode": row["mode"],
                        "provider_mode": "DEMO_FAKE",
                        "state_machine_version": row["state_machine_version"],
                        "cycle_version": row["cycle_version"],
                        "product_snapshot_digest": row["product_snapshot_digest"],
                        "artifact_bindings": bindings,
                        "blocker_code": row["blocker_code"],
                        "failure_code": row["failure_code"],
                    },
                    tuple(relationships),
                )
            )
        for row in rows.get("commerce_connections", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.COMMERCE_CONNECTION,
                    str(row["id"]),
                    row["display_name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "provider": row["provider"],
                        "safe_store_identifier": row["safe_store_identifier"],
                        "is_fake": row["provider"] == "fake",
                    },
                )
            )
        for row in rows.get("commerce_mappings", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCT_COMMERCE_MAPPING,
                    str(row["id"]),
                    "Commerce mapping",
                    row["status"],
                    row["created_at"],
                    row["created_at"],
                    {
                        "external_product_id": row["external_product_id"],
                        "external_variant_id": row["external_variant_id"],
                    },
                    (
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "maps_product"),
                        _rel(
                            KnowledgeNodeType.COMMERCE_CONNECTION,
                            row["connection_id"],
                            "maps_to_store",
                        ),
                    ),
                )
            )
        for row in rows.get("commerce_reports", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.COMMERCE_OPERATIONS_REPORT,
                    str(row["id"]),
                    "Commerce Operations Report",
                    "CREATED",
                    row["created_at"],
                    row["created_at"],
                    {
                        "summary": row["summary"],
                        "inventory_exceptions": row["inventory_exceptions"],
                        "order_exceptions": row["order_exceptions"],
                        "limitations": row["limitations"],
                    },
                    (
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "analyzes_product"),
                        _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("commerce_proposals", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.COMMERCE_ACTION_PROPOSAL,
                    str(row["id"]),
                    f"{row['action_type'].replace('_', ' ').title()} proposal",
                    "PROPOSED",
                    row["created_at"],
                    row["created_at"],
                    {
                        "action_type": row["action_type"],
                        "exact_quantity": row["exact_quantity"],
                        "exact_amount": row["exact_amount"],
                        "currency": row["currency"],
                        "reason": row["reason"],
                        "risk_level": "R6" if row["action_type"] == "REFUND" else "R5",
                    },
                    (
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "proposes_for_product"),
                        _rel(
                            KnowledgeNodeType.COMMERCE_CONNECTION,
                            row["connection_id"],
                            "targets_store",
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows.get("commerce_results", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.COMMERCE_ACTION_RESULT,
                    str(row["id"]),
                    "Commerce action result",
                    row["status"],
                    row["completed_at"],
                    row["completed_at"],
                    {
                        "action_type": row["action_type"],
                        "provider": row["provider"],
                        "observed_fact_refs": row["observed_fact_refs"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.COMMERCE_ACTION_PROPOSAL,
                            row["proposal_id"],
                            "result_of_proposal",
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        by_ref = {node.ref: node for node in nodes}
        reverse: dict[KnowledgeNodeRef, list[KnowledgeRelationship]] = {}
        for node in nodes:
            for relationship in node.relationships:
                if relationship.target in by_ref:
                    reverse.setdefault(relationship.target, []).append(
                        KnowledgeRelationship(node.ref, "linked_from")
                    )
        nodes = [
            replace(node, relationships=(*node.relationships, *reverse.get(node.ref, ())))
            for node in nodes
        ]
        nodes.sort(key=lambda node: (node.node_type.value, node.canonical_id))
        return KnowledgeGraph(tuple(nodes))


def _node_from_payload(payload: Mapping[str, Any]) -> KnowledgeNode:
    return KnowledgeNode(
        KnowledgeNodeType(payload["node_type"]),
        payload["canonical_id"],
        payload["title"],
        payload["status"],
        datetime.fromisoformat(payload["created_at"]),
        datetime.fromisoformat(payload["updated_at"]),
        payload.get("properties", {}),
        tuple(
            KnowledgeRelationship(
                KnowledgeNodeRef(
                    KnowledgeNodeType(item["target_node_type"]), item["target_canonical_id"]
                ),
                item["relationship_type"],
            )
            for item in payload.get("relationships", [])
        ),
        payload.get("semantic_digest"),
    )


class SqlAlchemyKnowledgeProjectionStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def refresh(self, context: ExecutionContext, graph: KnowledgeGraph) -> int:
        now = datetime.now(UTC)
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant_id, 0))"),
                {"tenant_id": str(context.tenant_id)},
            )
            existing = {
                row["node_key"]: row
                for row in (
                    await session.execute(
                        select(projection_nodes)
                        .where(projection_nodes.c.tenant_id == context.tenant_id)
                        .with_for_update()
                    )
                ).mappings()
            }
            incoming = {f"{node.node_type.value}:{node.canonical_id}": node for node in graph.nodes}
            for key, node in incoming.items():
                payload = cast(dict[str, object], _json(node.primitive()))
                digest = node.projection_digest
                if key not in existing:
                    await session.execute(
                        insert(projection_nodes).values(
                            tenant_id=context.tenant_id,
                            node_key=key,
                            node_type=node.node_type.value,
                            canonical_id=node.canonical_id,
                            projection_digest=digest,
                            payload=payload,
                            updated_at=now,
                        )
                    )
                elif existing[key]["projection_digest"] != digest:
                    await session.execute(
                        update(projection_nodes)
                        .where(
                            projection_nodes.c.tenant_id == context.tenant_id,
                            projection_nodes.c.node_key == key,
                        )
                        .values(projection_digest=digest, payload=payload, updated_at=now)
                    )
                else:
                    continue
                await session.execute(
                    insert(projection_changes).values(
                        tenant_id=context.tenant_id,
                        node_key=key,
                        node_type=node.node_type.value,
                        canonical_id=node.canonical_id,
                        operation="upsert",
                        payload=payload,
                        created_at=now,
                    )
                )
            for key, row in existing.items():
                if key in incoming:
                    continue
                await session.execute(
                    delete(projection_nodes).where(
                        projection_nodes.c.tenant_id == context.tenant_id,
                        projection_nodes.c.node_key == key,
                    )
                )
                await session.execute(
                    insert(projection_changes).values(
                        tenant_id=context.tenant_id,
                        node_key=key,
                        node_type=row["node_type"],
                        canonical_id=row["canonical_id"],
                        operation="delete",
                        payload=None,
                        created_at=now,
                    )
                )
            latest = await session.scalar(
                select(func.coalesce(func.max(projection_changes.c.revision), 0)).where(
                    projection_changes.c.tenant_id == context.tenant_id
                )
            )
            return int(latest or 0)

    async def changes(
        self, context: ExecutionContext, *, after_revision: int, limit: int
    ) -> tuple[tuple[KnowledgeChange, ...], int]:
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            result = (
                await session.execute(
                    select(projection_changes)
                    .where(
                        projection_changes.c.tenant_id == context.tenant_id,
                        projection_changes.c.revision > after_revision,
                    )
                    .order_by(projection_changes.c.revision)
                    .limit(limit)
                )
            ).mappings()
            changes = tuple(
                KnowledgeChange(
                    int(row["revision"]),
                    _node_from_payload(row["payload"]) if row["payload"] else None,
                    None
                    if row["payload"]
                    else KnowledgeNodeRef(KnowledgeNodeType(row["node_type"]), row["canonical_id"]),
                )
                for row in result
            )
            latest = await session.scalar(
                select(func.coalesce(func.max(projection_changes.c.revision), 0)).where(
                    projection_changes.c.tenant_id == context.tenant_id
                )
            )
            return changes, int(latest or 0)
