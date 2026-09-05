# ==============================================================================
# Stage 1: Extract Node.js binary
# ==============================================================================
FROM node:20-slim AS node-bin

# ==============================================================================
# Stage 2: Builder - Install dependencies & strip binary bloat
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install binutils for `strip` tool
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    && rm -rf /var/lib/apt/lists/*

# Copy standalone node binary and strip it
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/node
RUN strip --strip-unneeded /usr/local/bin/node

ARG CLOUD_PROVIDER=gcp

COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --prefix=/install -r requirements.txt \
    && if [ "$CLOUD_PROVIDER" = "gcp" ]; then pip install --no-cache-dir --no-compile --prefix=/install google-cloud-storage; \
       elif [ "$CLOUD_PROVIDER" = "aws" ]; then pip install --no-cache-dir --no-compile --prefix=/install boto3; \
       elif [ "$CLOUD_PROVIDER" = "azure" ]; then pip install --no-cache-dir --no-compile --prefix=/install azure-storage-blob; \
       else pip install --no-cache-dir --no-compile --prefix=/install google-cloud-storage boto3 azure-storage-blob; fi

# Prune bloat from installed packages:
# 1. Strip debug symbols from all compiled .so shared objects (pyarrow, numpy, pandas, etc.)
# 2. Remove tests, C/C++ header files, and bytecode
RUN find /install -name "*.so*" -exec strip --strip-unneeded {} + 2>/dev/null || true \
    && find /install -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true \
    && find /install -type d -name "test" -exec rm -rf {} + 2>/dev/null || true \
    && find /install -type d -name "include" -exec rm -rf {} + 2>/dev/null || true \
    && find /install -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find /install -name "*.pyc" -delete \
    && find /install -name "*.pyo" -delete

# ==============================================================================
# Stage 3: Final minimal runtime image
# ==============================================================================
FROM python:3.11-slim

# Copy stripped node binary
COPY --from=builder /usr/local/bin/node /usr/local/bin/node

# Copy pruned python libraries
COPY --from=builder /install /usr/local

# Install essential SSL and CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application source code (respecting .dockerignore)
COPY . .

RUN chmod +x scripts/*.sh scripts/*/*.sh 2>/dev/null || true

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/bin/bash", "scripts/cloud_job.sh"]

