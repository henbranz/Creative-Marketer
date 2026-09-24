# CI-only MinIO built from the official AGPL source security release.
# Provenance and update procedure: docs/CI_OBJECT_STORAGE.md.
FROM --platform=$BUILDPLATFORM golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d AS build
ARG TARGETARCH
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOAMD64=v1
WORKDIR /src
RUN curl --fail --silent --show-error --location \
      https://codeload.github.com/minio/minio/tar.gz/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a -o /tmp/minio.tar.gz \
    && echo '45521908307306e925c98d629e1c17d78c8b72b6ee242b1bfb1409f7d8ee5841  /tmp/minio.tar.gz' | sha256sum --check --strict \
    && tar -xzf /tmp/minio.tar.gz --strip-components=1 \
    && go mod download && go mod verify
RUN GOARCH=$TARGETARCH go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w -buildid=' -o /out/minio . \
    && mkdir -p /out/data /out/tmp && chmod 1777 /out/tmp

FROM scratch
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="Creative Marketer CI-only official-source MinIO"
COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /src/LICENSE /LICENSE
COPY --from=build --chown=65532:65532 /out/data /data
COPY --from=build --chown=65532:65532 /out/tmp /tmp
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["/usr/bin/minio"]
CMD ["server", "/data"]
