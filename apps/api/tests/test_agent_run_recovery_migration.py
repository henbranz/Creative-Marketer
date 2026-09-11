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


def test_recovery_migration_refuses_lossy_downgrade(monkeypatch: Any) -> None:
    migration = runpy.run_path(
        str(
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260912_0016_agent_run_recovery.py"
        )
    )
    monkeypatch.setattr(op, "get_bind", Bind)
    with pytest.raises(RuntimeError, match="refusing lossy AgentRun recovery downgrade"):
        cast(Callable[[], None], migration["downgrade"])()
