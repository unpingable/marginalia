# Marginalia — standalone governed creative-writing application

FROM python:3.11-slim

WORKDIR /app

# One exact runtime/build resolution for local, test, and container execution.
COPY requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir -r requirements.lock -r requirements-build.lock

# Install the qualified AG sources (not fetched implicitly from a registry).
COPY AG_CONTRACT_COMMIT /app/AG_CONTRACT_COMMIT
COPY agent-governor/ /tmp/agent-governor/
RUN test "$(cat /tmp/agent-governor/AG_CONTRACT_COMMIT)" = "$(cat /app/AG_CONTRACT_COMMIT)" \
    && pip install --no-cache-dir --no-build-isolation --no-deps /tmp/agent-governor/ \
    && rm -rf /tmp/agent-governor/

COPY receipt-v1/ /tmp/receipt-v1/
RUN pip install --no-cache-dir --no-build-isolation --no-deps /tmp/receipt-v1/ \
    && rm -rf /tmp/receipt-v1/

# Install Marginalia without a second dependency resolution.
COPY pyproject.toml README.md AG_CONTRACT.md ./
COPY src/ src/
RUN pip install --no-cache-dir --no-build-isolation --no-deps .

RUN python3 -c "import importlib.metadata as m; import governor, receipt_v1, gov_webui; assert m.version('agent-governor') == '2.8.1'; assert m.version('marginalia') == '0.1.0'"

# Entrypoints: normalize the subscription-backed Codex provider, then start
# governor and uvicorn with one state root.
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
