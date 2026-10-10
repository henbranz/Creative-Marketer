from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.production.domain import MediaSpendRequirement

from .agent_runtime_schema import agent_runs
from .production_schema import generation_jobs, production_plans


async def product_media_spend_requirement(
    session: AsyncSession,
    tenant_id: UUID,
    product_id: UUID,
    configured_cap: Decimal,
) -> MediaSpendRequirement:
    """Calculate the one authoritative conservative live Product spend view."""

    model_amount = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            agent_runs.c.status.in_(("SUCCEEDED", "FAILED")),
                            agent_runs.c.estimated_cost,
                        ),
                        else_=agent_runs.c.reserved_cost,
                    )
                ),
                0,
            )
        ).where(
            agent_runs.c.tenant_id == tenant_id,
            agent_runs.c.product_id == product_id,
            agent_runs.c.currency == "USD",
        )
    )
    media_amount = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            generation_jobs.c.status == "SUCCEEDED",
                            generation_jobs.c.actual_cost + generation_jobs.c.unknown_cost,
                        ),
                        (
                            generation_jobs.c.status == "OUTCOME_UNKNOWN",
                            generation_jobs.c.unknown_cost,
                        ),
                        (generation_jobs.c.status == "FAILED", generation_jobs.c.unknown_cost),
                        else_=(generation_jobs.c.reserved_cost + generation_jobs.c.unknown_cost),
                    )
                ),
                0,
            )
        )
        .select_from(
            generation_jobs.join(
                production_plans,
                generation_jobs.c.production_plan_id == production_plans.c.id,
            )
        )
        .where(
            generation_jobs.c.tenant_id == tenant_id,
            production_plans.c.product_id == product_id,
            generation_jobs.c.currency == "USD",
        )
    )
    reserved_media = await session.scalar(
        select(func.coalesce(func.sum(generation_jobs.c.reserved_cost), 0))
        .select_from(
            generation_jobs.join(
                production_plans,
                generation_jobs.c.production_plan_id == production_plans.c.id,
            )
        )
        .where(
            generation_jobs.c.tenant_id == tenant_id,
            production_plans.c.product_id == product_id,
            generation_jobs.c.currency == "USD",
            generation_jobs.c.status.in_(
                (
                    "PENDING_APPROVAL",
                    "READY",
                    "BLOCKED_SPEND_CAP",
                    "STARTING",
                    "PROCESSING",
                    "IMPORTING",
                )
            ),
        )
    )
    committed = Decimal(model_amount or 0) + Decimal(media_amount or 0)
    return MediaSpendRequirement(
        configured_cap=configured_cap,
        committed_product_spend=committed,
        reserved_media_amount=Decimal(reserved_media or 0),
        minimum_required_cap=committed,
    )
