# mypy: disable-error-code="no-untyped-def"

import importlib.util
from pathlib import Path
from typing import Any


def _module() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts/environment.py"
    spec = importlib.util.spec_from_file_location("root_environment_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_init_preserves_existing_values_and_prints_no_secret(tmp_path, capsys) -> None:
    module = _module()
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    secret = "secret-sentinel-never-print"
    example.write_text("OPENAI_API_KEY=\nNEW_SETTING=safe\n")
    target.write_text(f"OPENAI_API_KEY={secret}\n")
    module.EXAMPLE = example
    module.ENV = target
    assert module.init() == 0
    assert module.assignments(target) == {
        "OPENAI_API_KEY": secret,
        "NEW_SETTING": "safe",
    }
    assert secret not in capsys.readouterr().out
    assert target.stat().st_mode & 0o777 == 0o600
