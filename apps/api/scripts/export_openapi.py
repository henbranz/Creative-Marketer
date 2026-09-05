import json
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://contract:contract@localhost/contracts")
os.environ.setdefault("AUDIT_FINGERPRINT_KEY", "contract-generation-key-32-bytes-minimum")

from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app


def main() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://contract:contract@localhost/contracts",
        audit_fingerprint_key="contract-generation-key-32-bytes-minimum",
        app_env="test",
    )
    target = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
    target.write_text(
        json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
