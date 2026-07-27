FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --user --no-cache-dir -e ".[api,alpaca]"

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app/src

COPY . .

RUN groupadd -r traderos && useradd -r -g traderos -d /app traderos && \
    chown -R traderos:traderos /app

USER traderos

VOLUME ["/app/data", "/app/exports"]

ENTRYPOINT ["traderos"]
CMD ["daemon", "--mode", "paper"]
