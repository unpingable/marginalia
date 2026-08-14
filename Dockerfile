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

FROM python:3.11-slim@sha256:a630a63cdb314e2d138a2fca3e375e319e8568346ffafac5b980f888630ac4f1

LABEL org.opencontainers.image.title="Marginalia" \
      org.opencontainers.image.description="Governed creative-writing local appliance" \
      org.opencontainers.image.source="https://github.com/unpingable/marginalia" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.1.0"

WORKDIR /app

# One exact runtime/build resolution for local, test, and container execution.
COPY requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir -r requirements.lock -r requirements-build.lock

# Install the qualified AG sources (not fetched implicitly from a registry).
COPY LICENSE NOTICE /licenses/marginalia/
COPY agent-governor/LICENSE agent-governor/NOTICE /licenses/agent-governor/
COPY AG_CONTRACT_COMMIT /app/AG_CONTRACT_COMMIT
COPY agent-governor/ /tmp/agent-governor/
RUN test "$(cat /tmp/agent-governor/AG_CONTRACT_COMMIT)" = "$(cat /app/AG_CONTRACT_COMMIT)" \
    && pip install --no-cache-dir --no-build-isolation --no-deps /tmp/agent-governor/ \
    && rm -rf /tmp/agent-governor/

COPY receipt-kernel/ /tmp/receipt-kernel/
RUN pip install --no-cache-dir --no-build-isolation --no-deps /tmp/receipt-kernel/ \
    && rm -rf /tmp/receipt-kernel/

COPY receipt-v1/ /tmp/receipt-v1/
RUN pip install --no-cache-dir --no-build-isolation --no-deps /tmp/receipt-v1/ \
    && rm -rf /tmp/receipt-v1/

# Install Marginalia without a second dependency resolution.
COPY pyproject.toml README.md AG_CONTRACT.md ./
COPY src/ src/
RUN pip install --no-cache-dir --no-build-isolation --no-deps .

COPY --from=codex-cli /codex /opt/codex/codex

RUN python3 -c "import importlib.metadata as m; import fiction_governor, governor, receipt_kernel, receipt_v1, gov_webui; assert m.version('agent-governor') == '2.8.1'; assert m.version('marginalia') == '0.1.0'" \
    && /opt/codex/codex --version

# Entrypoints: normalize the bundled subscription-backed Codex provider, then
# start governor and uvicorn with one state root.
COPY codex-provider.sh /app/codex-provider.sh
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/codex-provider.sh /app/entrypoint.sh

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run AG daemon + Marginalia with one aligned state root.
CMD ["/app/entrypoint.sh"]
