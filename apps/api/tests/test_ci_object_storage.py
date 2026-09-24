from pathlib import Path


def test_ci_storage_is_pinned_official_source_not_an_untrusted_mirror() -> None:
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "infra/ci/minio.Dockerfile").read_text()
    workflow = (root / ".github/workflows/ci.yml").read_text()
    assert "golang:1.26.8-bookworm@sha256:" in dockerfile
    assert "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" in dockerfile
    assert "45521908307306e925c98d629e1c17d78c8b72b6ee242b1bfb1409f7d8ee5841" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert "go mod verify" in dockerfile and "-mod=readonly" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert ":latest" not in dockerfile
    assert "--iidfile" in workflow and '$(cat "$RUNNER_TEMP/minio-image.id")' in workflow
    assert "quay.io/minio/minio" not in workflow
    assert "Bootstrap private object storage" in workflow
    assert "uv run pytest" in workflow
    assert "Verify lossy identity downgrade fails closed" in workflow
