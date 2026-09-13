# mypy: disable-error-code="no-untyped-def"

import importlib.util
import shutil
import subprocess
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


def test_make_env_check_does_not_parse_dotenv(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[3]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(repository / "Makefile", tmp_path / "Makefile")
    shutil.copyfile(repository / "scripts/environment.py", scripts / "environment.py")
    (tmp_path / ".env").write_text(
        'CORS_ORIGINS=["http://localhost:3000"]\n'
        'NEXT_PUBLIC_OBSIDIAN_VAULT_NAME="Creative Marketer"\n'
        'SENTINEL_WITH_DOLLAR="value$with$dollars"\n'
        'SENTINEL_WITH_HASH="value#fragment"\n'
    )

    makefile = (tmp_path / "Makefile").read_text()
    assert "include .env" not in makefile
    assert "-include .env" not in makefile

    direct = subprocess.run(
        ["python3", "scripts/environment.py", "check"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    through_make = subprocess.run(
        ["make", "env-check"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert direct.returncode == 0
    assert through_make.returncode == 0
    assert through_make.stdout == direct.stdout
    assert "Creative Marketer environment" in through_make.stdout
    assert "missing separator" not in through_make.stderr
    assert "value$with$dollars" not in through_make.stdout
    assert "value#fragment" not in through_make.stdout


def test_make_env_init_handles_missing_and_existing_dotenv(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[3]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(repository / "Makefile", tmp_path / "Makefile")
    shutil.copyfile(repository / "scripts/environment.py", scripts / "environment.py")
    shutil.copyfile(repository / ".env.example", tmp_path / ".env.example")

    created = subprocess.run(
        ["make", "env-init"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    merged = subprocess.run(
        ["make", "env-init"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert created.returncode == 0
    assert "Central .env created" in created.stdout
    assert merged.returncode == 0
    assert "Central .env merged; 0 contract fields added" in merged.stdout
