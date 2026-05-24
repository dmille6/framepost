FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# exiftool is only used as a fallback for IPTC encoding edges (brief, services/iptc.py).
# build-essential is kept slim — wheels cover Pillow, cryptography, argon2-cffi.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libpng16-16 \
        exiftool \
        ffmpeg \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user matching the host mike (UID 1000) so bind-mounted files stay writable.
RUN useradd -u 1000 -m -s /bin/bash app && \
    mkdir -p /app/data && \
    chown -R app:app /app

COPY --chown=app:app requirements.txt ./
RUN pip install -r requirements.txt

# Configurable build cache buster — bumped to force a fresh dep install when adding packages.
ENV FRAMEPOST_DEP_REV=3

COPY --chown=app:app . ./

USER app

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
