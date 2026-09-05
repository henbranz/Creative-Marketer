import ast
import json
import re
import subprocess
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from creative_marketer.events.domain import event_sha256_v1
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app

API_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = API_ROOT.parents[1]
BACKEND_SOURCE = API_ROOT / "src"
PRODUCT_SOURCE = BACKEND_SOURCE / "creative_marketer"


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_all_domain_code_obeys_the_inward_dependency_boundary() -> None:
    domain_files = [
        path
        for path in _python_files(PRODUCT_SOURCE)
        if path.name == "domain.py" or "domain" in path.relative_to(PRODUCT_SOURCE).parts
    ]
    forbidden_roots = {
        "alembic",
        "fastapi",
        "opentelemetry",
        "psycopg",
        "sqlalchemy",
        "temporalio",
        "openai",
        "anthropic",
        "langgraph",
    }
    violations = {
        str(path.relative_to(API_ROOT)): sorted(
            imported for imported in _imports(path) if imported.split(".", 1)[0] in forbidden_roots
        )
        for path in domain_files
    }
    assert not {path: values for path, values in violations.items() if values}


def test_application_code_never_depends_on_delivery_or_infrastructure() -> None:
    application_files = [
        path
        for path in _python_files(PRODUCT_SOURCE)
        if path.name == "application.py"
        or "application" in path.relative_to(PRODUCT_SOURCE).parts
        or "workflow_orchestration" in path.relative_to(PRODUCT_SOURCE).parts
    ]
    violations: dict[str, list[str]] = {}
    for path in application_files:
        forbidden = [
            imported
            for imported in _imports(path)
            if imported == "creative_marketer_api"
            or imported.startswith("creative_marketer_api.")
            or imported == "creative_marketer.infrastructure"
            or imported.startswith("creative_marketer.infrastructure.")
        ]
        if forbidden:
            violations[str(path.relative_to(API_ROOT))] = sorted(forbidden)
    assert not violations


def test_workers_do_not_import_fastapi_delivery_routes() -> None:
    worker_files = [path for path in _python_files(PRODUCT_SOURCE) if path.name == "worker.py"]
    violations = {
        str(path.relative_to(API_ROOT)): sorted(
            imported
            for imported in _imports(path)
            if imported == "fastapi"
            or imported.startswith("fastapi.")
            or imported == "creative_marketer_api"
            or imported.startswith("creative_marketer_api.")
        )
        for path in worker_files
    }
    assert not {path: values for path, values in violations.items() if values}


def test_temporal_sdk_and_workflow_io_are_confined_to_the_temporal_adapter() -> None:
    temporal_importers = [
        path
        for path in _python_files(PRODUCT_SOURCE)
        if any(imported.split(".", 1)[0] == "temporalio" for imported in _imports(path))
    ]
    assert temporal_importers
    assert all(
        path.is_relative_to(PRODUCT_SOURCE / "infrastructure" / "temporal")
        for path in temporal_importers
    )

    workflow_path = PRODUCT_SOURCE / "infrastructure" / "temporal" / "workflows.py"
    forbidden_import_roots = {
        "os",
        "pathlib",
        "random",
        "secrets",
        "time",
        "uuid",
        "sqlalchemy",
        "psycopg",
        "requests",
        "httpx",
        "openai",
        "anthropic",
    }
    assert not [
        imported
        for imported in _imports(workflow_path)
        if imported.split(".", 1)[0] in forbidden_import_roots
    ]
    source = workflow_path.read_text(encoding="utf-8")
    for forbidden_call in (
        "open(",
        "datetime.now(",
        "datetime.utcnow(",
        "uuid4(",
        "ToolGateway(",
        ".execute(",
    ):
        assert forbidden_call not in source


def test_phase0_has_no_provider_or_agent_framework_dependency() -> None:
    manifests = [
        API_ROOT / "pyproject.toml",
        REPOSITORY_ROOT / "package.json",
        REPOSITORY_ROOT / "package-lock.json",
    ]
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in manifests)
    source += "\n" + "\n".join(
        path.read_text(encoding="utf-8").lower() for path in _python_files(BACKEND_SOURCE)
    )
    forbidden = (
        "facebook-business",
        "facebook_business",
        "tiktok-business-api-sdk",
        "shopifyapi",
        "shopify-api-js",
        "openai-agents",
        "agents-sdk",
        "anthropic-agent",
        "langgraph",
        "higgsfield-sdk",
        "runwayml",
        "hermes-agent",
    )
    assert not [dependency for dependency in forbidden if dependency in source]


def test_phase0_has_registry_types_but_no_executable_business_agents() -> None:
    forbidden_names = {
        "Researcher",
        "CreativeStrategist",
        "Producer",
        "Marketer",
        "CommerceOperationsAgent",
        "IntelligenceAgent",
    }
    implementations: list[str] = []
    for path in _python_files(BACKEND_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in forbidden_names
            ):
                implementations.append(f"{path.relative_to(API_ROOT)}:{node.lineno}:{node.name}")
    assert not implementations


def test_tool_executors_are_invoked_only_inside_the_gateway() -> None:
    calls: list[str] = []
    for path in _python_files(PRODUCT_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "execute":
                continue
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "executor":
                calls.append(f"{path.relative_to(API_ROOT)}:{node.lineno}")
    assert calls == ["src/creative_marketer/tool_execution/application.py:573"]


def test_public_api_surface_contains_only_identity_and_catalog_mutation(
    settings: Settings,
) -> None:
    routes: set[tuple[str, str]] = set()
    for route in create_app(settings).routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not isinstance(path, str) or not isinstance(methods, set):
            continue
        routes.update((method, path) for method in methods if isinstance(method, str))
    allowed_non_read = {("HEAD", "/docs"), ("HEAD", "/redoc")}
    unexpected = {
        (method, path)
        for method, path in routes
        if method not in {"GET", "HEAD"}
        and (method, path) not in allowed_non_read
        and not (path.startswith("/v1/brands") or path.startswith("/v1/products"))
    }
    assert not unexpected
    forbidden_fragments = (
        "/agents",
        "/tools",
        "/permissions",
        "/authorize",
        "/approvals",
        "/events",
        "/outbox",
        "/execute",
    )
    assert not [path for _, path in routes if any(value in path for value in forbidden_fragments)]


def test_repository_has_no_obvious_committed_credentials_or_unsafe_debug_calls() -> None:
    secret_patterns = (
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    excluded = {"package-lock.json", "uv.lock"}
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    candidates = [
        REPOSITORY_ROOT / value.decode()
        for value in tracked
        if value
        and Path(value.decode()).name not in excluded
        and "tests" not in Path(value.decode()).parts
    ]
    findings: list[str] = []
    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(content) for pattern in secret_patterns):
            findings.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert not findings

    python_debug = []
    for path in _python_files(BACKEND_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                python_debug.append(f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}")
    assert not python_debug

    typescript = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (REPOSITORY_ROOT / "apps" / "web", REPOSITORY_ROOT / "packages")
        for path in root.rglob("*.ts*")
        if ".next" not in path.parts and "node_modules" not in path.parts
    )
    assert "console.log(" not in typescript


def test_example_environment_is_explicitly_local_and_development_only() -> None:
    values = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "APP_ENV=development" in values
    assert "DEV_IDENTITY_ENABLED=true" in values
    assert "@localhost:" in values
    assert "local-only-" in values
    assert "APP_ENV=production" not in values


def test_event_contracts_are_versioned_closed_language_neutral_schemas() -> None:
    schema_root = PRODUCT_SOURCE / "events" / "schemas"
    schemas = sorted(schema_root.glob("*.json"))
    assert schemas
    for path in schemas:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert re.fullmatch(r"[a-z][a-z0-9_.]+\.v[1-9][0-9]*", path.stem)
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["x-event-type"] == path.stem
        assert document["additionalProperties"] is False
        assert "$ref" not in path.read_text(encoding="utf-8")
    assert list(REPOSITORY_ROOT.rglob("openapi.json")) == [
        REPOSITORY_ROOT / "packages" / "contracts" / "openapi.json"
    ]
    assert not [
        path for path in REPOSITORY_ROOT.rglob("openapi.yaml") if "node_modules" not in path.parts
    ]


def test_published_v1_event_contract_digests_are_immutable() -> None:
    expected = {
        "catalog.asset.archived.v1": (
            "sha256:bcbb774c81085112f4594e587be1b0bc23dbdfdebc3eac331ccac840499d6b4f"
        ),
        "catalog.asset.ready.v1": (
            "sha256:3bbba4e4f7c7caf2121f67c557f676157fcea18093155e451c225fbe71c20c63"
        ),
        "catalog.brand.created.v1": (
            "sha256:37dd8c1541a9c68d98d74941bf96a20415e53c786d1040d081eba9c70c72f550"
        ),
        "catalog.product.brief_completed.v1": (
            "sha256:c60461c5ddca0b19525df7e3f216ff53ed96be07c6a2fdacaa8b1e1e506dfda3"
        ),
        "catalog.product.created.v1": (
            "sha256:65626cbe777eeedbaa0619ba275dd461aab6963223ace65a5b91b1b26a27ddee"
        ),
        "catalog.product.snapshot_created.v1": (
            "sha256:2d8b2c8a7c7d09b0fdada374218277e4d8848d9babe9a9a75c6924672eef05e4"
        ),
        "catalog.product.updated.v1": (
            "sha256:b510933280654f2e45ca59513d41d5171523fea6bf1975920ed1380524d41d87"
        ),
        "governance.approval.denied.v1": (
            "sha256:b287fb010f7271979b1c02a9d5f5d50d4a677c7c6404bf3abe025a157edb0443"
        ),
        "governance.approval.granted.v1": (
            "sha256:54043212af29c8f65129dc25ec51062a795c6c98d8cedbd2c0572b993f24a3c3"
        ),
        "governance.approval.requested.v1": (
            "sha256:5ebed0b5d431e28c55f17dbe6d6b49451141131c47d94bb4fe2e48f443f4ccf2"
        ),
        "governance.approval.revoked.v1": (
            "sha256:4d30484b8a5a63250b5a6690a60faca670483fcab95c44263b326506342ce379"
        ),
        "governance.tool.execution_failed.v1": (
            "sha256:7253d38389952cb4e0625409bcfde4fad07fcd98ee78fdce4af7298d518adb55"
        ),
        "governance.tool.execution_outcome_unknown.v1": (
            "sha256:4030b4e74fef99627e57d9e66dcc9c01d21e3845c7ead1ee7d1e8cce73fba8c2"
        ),
        "governance.tool.execution_succeeded.v1": (
            "sha256:8717b393ccdc75e59e4ab557d8ea6fdc55ece77d31911f4a935c6bf89bc41bd0"
        ),
    }
    schema_root = PRODUCT_SOURCE / "events" / "schemas"
    actual = {
        path.stem: event_sha256_v1(json.loads(path.read_text(encoding="utf-8")))
        for path in schema_root.glob("*.v1.json")
    }
    assert actual == expected


def test_migrations_have_one_linear_head() -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    files = list((API_ROOT / "migrations" / "versions").glob("*.py"))
    assert script.get_heads() == ["20260906_0013"]
    assert len(revisions) == len(files)
    assert all(not revision.is_branch_point for revision in revisions)
