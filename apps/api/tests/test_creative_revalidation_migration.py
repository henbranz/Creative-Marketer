import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import op


class Result:
    def scalar_one(self) -> int:
        return 1


class Bind:
    def execute(self, _statement: object) -> Result:
        return Result()


def migration() -> dict[str, object]:
    return runpy.run_path(
        str(
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20261009_0036_creative_concept_revalidation.py"
        )
    )


def test_revalidation_migration_refuses_lossy_downgrade(monkeypatch: Any) -> None:
    monkeypatch.setattr(op, "get_bind", Bind)
    with pytest.raises(RuntimeError, match="refusing lossy Creative revalidation downgrade"):
        cast(Callable[[], None], migration()["downgrade"])()


def test_revalidation_migration_declares_tenant_authority_constraints() -> None:
    source = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20261009_0036_creative_concept_revalidation.py"
    ).read_text()
    assert "uq_concept_revalidations_authority" in source
    assert "creative.concepts.tenant_id" in source
    assert "research.research_snapshots.tenant_id" in source
    assert "catalog.product_knowledge_snapshots.tenant_id" in source
    assert "creative.concept_decisions.tenant_id" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "protect_concept_revalidations" in source
