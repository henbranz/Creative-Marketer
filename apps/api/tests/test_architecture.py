import ast
from pathlib import Path


def test_domain_does_not_import_framework_or_infrastructure() -> None:
    domain_root = Path(__file__).parents[1] / "src" / "creative_marketer" / "identity" / "domain"
    forbidden = {"fastapi", "sqlalchemy", "psycopg", "alembic"}
    imports: set[str] = set()
    for source_file in domain_root.glob("*.py"):
        tree = ast.parse(source_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    assert imports.isdisjoint(forbidden)


def test_application_is_vendor_and_framework_independent() -> None:
    application_root = (
        Path(__file__).parents[1] / "src" / "creative_marketer" / "identity" / "application"
    )
    source = "\n".join(path.read_text() for path in application_root.glob("*.py"))
    forbidden = {"fastapi", "sqlalchemy", "auth0", "workos", "clerk", "firebase", "supabase"}
    assert not any(value in source.lower() for value in forbidden)


def test_delivery_does_not_construct_authoritative_identity() -> None:
    route_source = (
        Path(__file__).parents[1] / "src" / "creative_marketer_api" / "authentication_routes.py"
    ).read_text()
    assert "TenantContext(" not in route_source
    assert "Actor(" not in route_source
