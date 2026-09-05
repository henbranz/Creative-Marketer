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


def test_permission_engine_is_framework_provider_and_execution_independent() -> None:
    root = Path(__file__).parents[1] / "src" / "creative_marketer" / "permission_governance"
    source_files = list(root.glob("*.py"))
    forbidden = {
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "openai",
        "anthropic",
        "temporalio",
    }
    assert imported_roots(source_files).isdisjoint(forbidden)
    source = "\n".join(path.read_text().lower() for path in source_files)
    for value in ("functiontool", "eval(", "exec(", "provider sdk"):
        assert value not in source


def test_no_public_permission_mutation_or_evaluation_routes_exist() -> None:
    delivery_root = Path(__file__).parents[1] / "src" / "creative_marketer_api"
    source = "\n".join(path.read_text() for path in delivery_root.glob("*.py"))
    assert '@router.post("/permissions' not in source
    assert '@router.post("/authorize' not in source


def test_approval_and_idempotency_are_framework_provider_and_executor_independent() -> None:
    source_files: list[Path] = []
    for package in ("approval_governance", "execution_control"):
        root = Path(__file__).parents[1] / "src" / "creative_marketer" / package
        source_files.extend(root.glob("*.py"))
    source_files.append(
        Path(__file__).parents[1] / "src" / "creative_marketer" / "action_binding.py"
    )
    forbidden = {
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "openai",
        "anthropic",
        "temporalio",
        "langgraph",
    }
    assert imported_roots(source_files).isdisjoint(forbidden)
    source = "\n".join(path.read_text().lower() for path in source_files)
    for value in ("requests.post", "httpx", "functiontool", "provider sdk", "toolcall"):
        assert value not in source


def test_no_public_normalization_or_approval_request_creation_route_exists() -> None:
    delivery_root = Path(__file__).parents[1] / "src" / "creative_marketer_api"
    source = "\n".join(path.read_text() for path in delivery_root.glob("*.py"))
    assert "NormalizedToolInput" not in source
    assert '@router.post("/approvals"' not in source


def test_event_domain_and_application_are_worker_safe_and_provider_independent() -> None:
    root = Path(__file__).parents[1] / "src" / "creative_marketer" / "events"
    source_files = [root / "domain.py", root / "application.py", root / "contracts.py"]
    forbidden = {
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "openai",
        "anthropic",
        "temporalio",
        "redis",
        "kafka",
    }
    assert imported_roots(source_files).isdisjoint(forbidden)
    source = "\n".join(path.read_text().lower() for path in source_files)
    for value in ("requests.post", "httpx", "functiontool", "tool gateway", "eventstore"):
        assert value not in source


def test_no_public_event_injection_route_exists() -> None:
    delivery_root = Path(__file__).parents[1] / "src" / "creative_marketer_api"
    source = "\n".join(path.read_text() for path in delivery_root.glob("*.py"))
    for route in ('@router.post("/events', '@router.post("/outbox', "POST /publish-event"):
        assert route not in source


def test_tool_gateway_is_internal_and_provider_independent() -> None:
    root = Path(__file__).parents[1] / "src" / "creative_marketer" / "tool_execution"
    source_files = list(root.glob("*.py"))
    forbidden = {
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "openai",
        "anthropic",
        "shopify",
        "meta",
        "temporalio",
    }
    assert imported_roots(source_files).isdisjoint(forbidden)
    delivery_root = Path(__file__).parents[1] / "src" / "creative_marketer_api"
    delivery = "\n".join(path.read_text().lower() for path in delivery_root.glob("*.py"))
    assert "toolgateway" not in delivery
    for package in ("agent_governance", "permission_governance", "approval_governance"):
        domain = Path(__file__).parents[1] / "src" / "creative_marketer" / package / "domain.py"
        assert "toolexecutor" not in domain.read_text().lower()


def test_delivery_does_not_construct_authoritative_identity() -> None:
    route_source = (
        Path(__file__).parents[1] / "src" / "creative_marketer_api" / "authentication_routes.py"
    ).read_text()
    assert "TenantContext(" not in route_source
    assert "Actor(" not in route_source


def test_domain_modules_do_not_import_observability_sdks() -> None:
    root = Path(__file__).parents[1] / "src" / "creative_marketer"
    domain_files = list(root.glob("**/domain.py"))
    forbidden = {"opentelemetry", "logging"}
    assert imported_roots(domain_files).isdisjoint(forbidden)


def test_observability_has_no_provider_or_business_sdk_dependency() -> None:
    root = Path(__file__).parents[1] / "src" / "creative_marketer" / "observability"
    source = "\n".join(path.read_text().lower() for path in root.glob("*.py"))
    for forbidden in ("openai", "anthropic", "shopify", "temporalio", "stripe"):
        assert forbidden not in source


def test_temporal_sdk_is_isolated_from_domain_and_application_contracts() -> None:
    root = Path(__file__).parents[1] / "src" / "creative_marketer"
    protected: list[Path] = list(root.glob("**/domain.py"))
    protected.extend(root.glob("**/application.py"))
    protected.extend((root / "workflow_orchestration").glob("*.py"))
    assert "temporalio" not in imported_roots(protected)

    temporal_root = root / "infrastructure" / "temporal"
    assert temporal_root.is_dir()
    worker_source = (temporal_root / "worker.py").read_text().lower()
    assert "fastapi" not in worker_source
    assert "creative_marketer_api" not in worker_source
