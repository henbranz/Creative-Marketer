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
