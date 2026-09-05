import ast
from pathlib import Path


def imported_roots(source_files: list[Path]) -> set[str]:
    imports: set[str] = set()
    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    return imports


def test_domain_does_not_import_framework_or_infrastructure() -> None:
    domain_root = Path(__file__).parents[1] / "src" / "creative_marketer" / "identity" / "domain"
    forbidden = {"fastapi", "sqlalchemy", "psycopg", "alembic"}
    imports = imported_roots(list(domain_root.glob("*.py")))
    assert imports.isdisjoint(forbidden)


def test_application_is_vendor_and_framework_independent() -> None:
    application_root = (
        Path(__file__).parents[1] / "src" / "creative_marketer" / "identity" / "application"
    )
    source = "\n".join(path.read_text() for path in application_root.glob("*.py"))
    forbidden = {"fastapi", "sqlalchemy", "auth0", "workos", "clerk", "firebase", "supabase"}
    assert not any(value in source.lower() for value in forbidden)


def test_agent_registry_is_framework_provider_and_runtime_independent() -> None:
    registry_root = Path(__file__).parents[1] / "src" / "creative_marketer" / "agent_governance"
    source_files = list(registry_root.glob("*.py"))
    forbidden_imports = {
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "openai",
        "anthropic",
        "google",
        "langgraph",
        "temporalio",
    }
    assert imported_roots(source_files).isdisjoint(forbidden_imports)
    source = "\n".join(path.read_text().lower() for path in source_files)
    assert "agentssdk" not in source
    assert "agent runtime" not in source


def test_no_public_agent_registry_mutation_routes_exist() -> None:
    delivery_root = Path(__file__).parents[1] / "src" / "creative_marketer_api"
    source = "\n".join(path.read_text() for path in delivery_root.glob("*.py"))
    assert "POST /agents" not in source
    assert '@router.post("/agents' not in source


def test_tool_registry_is_framework_provider_and_executor_independent() -> None:
    registry_root = Path(__file__).parents[1] / "src" / "creative_marketer" / "tool_governance"
    source_files = [registry_root / "domain.py", registry_root / "application.py"]
    forbidden_imports = {
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "openai",
        "anthropic",
        "google",
        "langgraph",
        "temporalio",
    }
    assert imported_roots(source_files).isdisjoint(forbidden_imports)
    source = "\n".join(path.read_text().lower() for path in source_files)
    for forbidden in ("functiontool", "python.module", "tool gateway", "provider sdk"):
        assert forbidden not in source


def test_no_public_tool_registry_mutation_routes_exist() -> None:
    delivery_root = Path(__file__).parents[1] / "src" / "creative_marketer_api"
    source = "\n".join(path.read_text() for path in delivery_root.glob("*.py"))
    assert "POST /tools" not in source
    assert '@router.post("/tools' not in source


def test_delivery_does_not_construct_authoritative_identity() -> None:
    route_source = (
        Path(__file__).parents[1] / "src" / "creative_marketer_api" / "authentication_routes.py"
    ).read_text()
    assert "TenantContext(" not in route_source
    assert "Actor(" not in route_source
