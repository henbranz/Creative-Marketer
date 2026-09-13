#!/usr/bin/env python3
"""Secret-safe root environment initialization and diagnostics."""

from __future__ import annotations

import argparse
import shutil
import socket
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
ENV = ROOT / ".env"


def assignments(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().replace("_", "").isalnum():
            result[key.strip()] = value.strip()
    return result


def init() -> int:
    if not ENV.exists():
        shutil.copyfile(EXAMPLE, ENV)
        added = len(assignments(EXAMPLE))
        action = "created"
    else:
        current = assignments(ENV)
        missing = [(key, value) for key, value in assignments(EXAMPLE).items() if key not in current]
        if missing:
            with ENV.open("a") as stream:
                stream.write("\n# Added by make env-init from the central contract\n")
                for key, value in missing:
                    stream.write(f"{key}={value}\n")
        added = len(missing)
        action = "merged"
    try:
        ENV.chmod(stat.S_IRUSR | stat.S_IWUSR)
        permissions = "0600"
    except OSError:
        permissions = "platform-managed"
    print(f"Central .env {action}; {added} contract fields added; permissions {permissions}.")
    print("Existing values were preserved. No values were displayed.")
    return 0


def _configured(values: dict[str, str], key: str) -> str:
    return "CONFIGURED" if values.get(key, "").strip() else "NOT CONFIGURED"


def _tcp(value: str, default_port: int) -> str:
    parsed = urlsplit(value if "://" in value else f"tcp://{value}")
    host, port = parsed.hostname, parsed.port or default_port
    if not host:
        return "INVALID CONFIGURATION"
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return "OK"
    except OSError:
        return "UNAVAILABLE"


def _object_storage(value: str) -> str:
    try:
        target = value.rstrip("/") + "/minio/health/live"
        with urlopen(Request(target, method="GET"), timeout=1) as response:
            return "OK" if response.status < 400 else "UNAVAILABLE"
    except Exception:
        return "UNAVAILABLE"


def check() -> int:
    values = assignments(ENV)
    print("Creative Marketer environment\n")
    rows = [
        ("Central root .env", "OK" if ENV.exists() else "NOT CONFIGURED"),
        ("Database", _tcp(values.get("DATABASE_URL", ""), 5432)),
        ("Object storage", _object_storage(values.get("OBJECT_STORAGE_ENDPOINT_URL", ""))),
        ("Temporal", _tcp(values.get("TEMPORAL_ADDRESS", ""), 7233)),
        ("OpenAI key", _configured(values, "OPENAI_API_KEY")),
        ("BytePlus key", _configured(values, "BYTEPLUS_LAS_API_KEY")),
        ("Image provider", values.get("MEDIA_IMAGE_PROVIDER") or "NOT CONFIGURED"),
        ("Video provider", values.get("MEDIA_VIDEO_PROVIDER") or "NOT CONFIGURED"),
        (
            "Billable media",
            "ENABLED" if values.get("ALLOW_BILLABLE_MEDIA", "").lower() == "true" else "DISABLED",
        ),
        ("Live acknowledgement", _configured(values, "RUN_LIVE_E2E")),
        ("Live Product", _configured(values, "LIVE_E2E_PRODUCT_ID")),
        ("Obsidian", _configured(values, "OBSIDIAN_VAULT_PATH")),
        ("Assembly renderer", "OK" if shutil.which("ffmpeg") else "CONTAINER ONLY"),
    ]
    for label, status in rows:
        print(f"{label:<25} {status}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "check"))
    action = parser.parse_args().action
    return init() if action == "init" else check()


if __name__ == "__main__":
    sys.exit(main())
