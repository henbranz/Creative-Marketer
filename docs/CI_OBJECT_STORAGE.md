# CI object storage provenance

## September 25, 2026 registry incident

The architecture-security job could no longer pull
`quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`:
the registry denied anonymous authorization before bootstrap or tests. The same
digest at `docker.io/minio/minio` returned `insufficient_scope`. This proves
public distribution is unavailable, not whether a registry administrator deleted
the content or changed private permissions. That internal distinction is not
observable from the public errors; adding credentials is not an established fix.

The [official repository](https://github.com/minio/minio) now documents source-only
community distribution and is archived. The [official release issue](https://github.com/minio/minio/issues/21647)
also records the absence of Docker images for the security release. There is no
other approved S3-compatible image in this project. Do not switch to an unverified
mirror, mutable `latest`, or a separately licensed commercial image.

## Approved CI-only replacement

The operator approved building from pinned official source. This is a **project-built
CI image**, not an upstream-published image or a production storage recommendation.
`infra/ci/minio.Dockerfile` pins:

- Official `minio/minio` release `RELEASE.2025-10-15T17-29-55Z`, commit
  `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`.
- Official source archive SHA-256
  `45521908307306e925c98d629e1c17d78c8b72b6ee242b1bfb1409f7d8ee5841`, checked before compilation.
- Docker Official Image `golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d`;
  its Linux amd64 manifest is
  `sha256:abe4f87f354c4f6d7ee3fb11b241c6b6c24a32ca50a2ebcc30493a2168e14048`.
- Go dependencies from the release's `go.mod` / `go.sum`, verified with
  `go mod verify`; `-mod=readonly` and `GOTOOLCHAIN=local` prevent implicit changes.

There are no package-manager installs or unpinned runtime base images. The final
scratch image includes the binary, CA bundle and upstream AGPL license; it runs as
UID 65532 without capabilities. CI starts the exact locally built image ID from
`--iidfile`, never a mutable tag, on loopback with disposable container storage.
Build inputs are reproducible; OCI metadata/build-platform differences can change
the output image digest. CI's build log records each output digest. No registry
publication, credentials, local Compose change or existing storage restart occurs.

Before modifying the workflow, local Linux amd64 validation built image
`sha256:112c4e88d383e39c9750eb407daf790afc64822cf79e58cca244ff3abab63937`,
started `server /data`, bootstrapped the private S3 bucket, and passed all nine
Catalog/Asset Library integration tests against an isolated database and port.
These include signed uploads/downloads, anonymous access denial, tenant isolation
and immutable snapshot/asset behavior. The workflow still runs all migrations,
downgrade guards, static checks, typing, and the full coverage-enforced test suite.

This archived software remains a CI fixture only. Production storage maintenance
and the now-inaccessible local Compose pin need a separate deployment decision.
To update the fixture, review upstream provenance, pin a new source commit and
archive checksum and compiler digest, rebuild for Linux amd64, then rerun bootstrap,
real S3/security tests and the complete CI job. Never hide a failing test with a skip.
