# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from types import SimpleNamespace
from uuid import uuid4

import pytest

import scripts.bootstrap_demo as demo
from creative_marketer.catalog.asset_domain import detect_mime
from creative_marketer.production.application import validate_production_plan
from tests.test_production import context


def test_demo_media_and_plan_fixture_are_real_and_contract_valid() -> None:
    production_context = context()
    scene_keys = ("scene_one", "scene_two", "scene_three")
    output = demo._production_output(
        SimpleNamespace(
            capability_context={
                "selected_assets": [
                    {"asset_id": str(production_context.selected_assets[0].asset_id)}
                ],
                "concept_scene_keys": list(scene_keys),
            }
        )
    )

    plan = validate_production_plan(
        output,
        tenant_id=uuid4(),
        product_id=uuid4(),
        agent_run_id=uuid4(),
        context=production_context,
        concept_scene_keys=scene_keys,
    )

    assert detect_mime(demo.PNG) == "image/png"
    assert {segment.media_kind.value for segment in plan.generation_segments} == {
        "IMAGE",
        "VIDEO",
    }


@pytest.mark.asyncio
async def test_demo_bootstrap_refuses_non_development(monkeypatch) -> None:
    monkeypatch.setattr(demo, "Settings", lambda: SimpleNamespace(app_env="production"))
    with pytest.raises(SystemExit, match="development-only"):
        await demo.run()
