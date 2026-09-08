# Marginalia — standalone governed creative-writing application

FROM node:24.13.0-bookworm-slim@sha256:4660b1ca8b28d6d1906fd644abe34b2ed81d15434d26d845ef0aced307cf4b6f AS codex-cli

ARG CODEX_VERSION=0.146.1
ARG TARGETARCH

RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && case "$TARGETARCH" in \
         amd64) CODEX_PACKAGE="codex-linux-x64"; CODEX_TARGET="x86_64-unknown-linux-musl" ;; \
         arm64) CODEX_PACKAGE="codex-linux-arm64"; CODEX_TARGET="aarch64-unknown-linux-musl" ;; \
         *) echo "Unsupported image architecture: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && cp "/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/${CODEX_PACKAGE}/vendor/${CODEX_TARGET}/bin/codex" /codex \
    && chmod 0755 /codex

FROM rust:1.94.0-bookworm@sha256:365468470075493dc4583f47387001854321c5a8583ea9604b297e67f01c5a4f AS generation-companions

WORKDIR /build
COPY AG_NG_CONTRACT_COMMIT DOCKET_CONTRACT_COMMIT ./
COPY ag-ng/ ag-ng/
COPY docket-runtime/ docket-runtime/
RUN test "$(cat ag-ng/AG_NG_CONTRACT_COMMIT)" = "$(cat AG_NG_CONTRACT_COMMIT)" \
    && test "$(cat docket-runtime/DOCKET_CONTRACT_COMMIT)" = "$(cat DOCKET_CONTRACT_COMMIT)" \
    && cargo build --locked --release --manifest-path ag-ng/Cargo.toml -p ag-app --bin ag-loopctl \
    && cargo build --locked --release --manifest-path ag-ng/Cargo.toml -p ag-providerd \
    && cargo build --locked --release --manifest-path docket-runtime/Cargo.toml -p gwr-local --bin docket \
    && install -Dm0755 ag-ng/target/release/ag-loopctl /out/ag-loopctl \
    && install -Dm0755 ag-ng/target/release/ag-providerd /out/ag-providerd \
    && install -Dm0755 ag-ng/target/release/ag-providerctl /out/ag-providerctl \
    && install -Dm0755 docket-runtime/target/release/docket /out/docket

FROM python:3.11-slim@sha256:a630a63cdb314e2d138a2fca3e375e319e8568346ffafac5b980f888630ac4f1

WORKDIR /app

# One exact runtime/build resolution for local, test, and container execution.
COPY requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir -r requirements.lock -r requirements-build.lock

# Install the independent historical receipt reader (not fetched implicitly).
COPY LICENSE NOTICE /licenses/marginalia/
COPY AG_NG_CONTRACT_COMMIT DOCKET_CONTRACT_COMMIT MODEL_EXECUTION_CONTRACT_COMMIT /app/
COPY model-execution/ /tmp/model-execution/
COPY receipt-v1/ /tmp/receipt-v1/
RUN test "$(cat /tmp/model-execution/MODEL_EXECUTION_CONTRACT_COMMIT)" = "$(cat MODEL_EXECUTION_CONTRACT_COMMIT)" \
    && pip install --no-cache-dir --no-build-isolation --no-deps /tmp/model-execution \
    && pip install --no-cache-dir --no-build-isolation --no-deps /tmp/receipt-v1/ \
    && rm -rf /tmp/model-execution /tmp/receipt-v1/

# Install Marginalia without a second dependency resolution.
COPY pyproject.toml README.md AG_CONTRACT.md ./
COPY src/ src/
RUN pip install --no-cache-dir --no-build-isolation --no-deps .

COPY --from=codex-cli /codex /opt/codex/codex
COPY --from=generation-companions /out/ag-loopctl /usr/local/bin/ag-loopctl
COPY --from=generation-companions /out/ag-providerd /usr/local/bin/ag-providerd
COPY --from=generation-companions /out/ag-providerctl /usr/local/bin/ag-providerctl
COPY --from=generation-companions /out/docket /usr/local/bin/docket

RUN python3 -c "import importlib.metadata as m; import model_execution, receipt_v1, gov_webui; assert m.version('marginalia') == '0.1.0'" \
    && python3 -c "import importlib.metadata as m; names={d.metadata['Name'].lower() for d in m.distributions()}; assert 'agent-governor' not in names and 'receipt-kernel' not in names" \
    && /opt/codex/codex --version \
    && /usr/local/bin/ag-loopctl --help >/dev/null \
    && /usr/local/bin/ag-providerd --help >/dev/null \
    && /usr/local/bin/ag-providerctl --help >/dev/null \
    && /usr/local/bin/docket --help >/dev/null

# Operational identity is applied after dependency/application installation so
# a new commit label does not invalidate the expensive reproducible build layers.
ARG MARGINALIA_BUILD_SHA=unknown
ARG MARGINALIA_BUILD_TIME=unknown
ARG MARGINALIA_IMAGE_REF=unknown
LABEL org.opencontainers.image.title="Marginalia" \
      org.opencontainers.image.description="Governed creative-writing local appliance" \
      org.opencontainers.image.source="https://github.com/unpingable/marginalia" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.revision="${MARGINALIA_BUILD_SHA}" \
      org.opencontainers.image.created="${MARGINALIA_BUILD_TIME}" \
      org.opencontainers.image.ref.name="${MARGINALIA_IMAGE_REF}" \
      org.marginalia.ag-ng.commit="1bd4225a94fa82df066923612f713a93ba93a7bb" \
      org.marginalia.docket.commit="181589f910b76030b312d6478bd0ac813a630855"
ENV MARGINALIA_BUILD_SHA="${MARGINALIA_BUILD_SHA}" \
    MARGINALIA_BUILD_TIME="${MARGINALIA_BUILD_TIME}" \
    MARGINALIA_IMAGE_REF="${MARGINALIA_IMAGE_REF}"

# Entrypoints for the web process and credential-isolated provider daemon.
COPY entrypoint.sh /app/entrypoint.sh
COPY providerd-entrypoint.sh /app/providerd-entrypoint.sh
COPY generation-worker-entrypoint.sh /app/generation-worker-entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/providerd-entrypoint.sh /app/generation-worker-entrypoint.sh

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready')" || exit 1

# Run Marginalia; model execution exists only in the required ag-ng services.
CMD ["/app/entrypoint.sh"]
