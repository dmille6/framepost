#!/usr/bin/env bash
# Compile the frontend, then build the nginx image that serves it.
#
# The compile runs in a plain `docker run` container rather than as a Docker build stage.
# Under BuildKit on the production host, npm installs a truncated esbuild binary --
# 6,672,013 bytes against an expected 10,358,936, the same bytes every time -- which
# segfaults and fails the build with a misleading EPIPE. The identical install via
# `docker run` is correct. The root cause was not found; this sidesteps it.
#
# The source tree is mounted READ-ONLY and everything is built inside the container,
# with dist/ copied back out. An earlier version bind-mounted frontend/ writable and let
# npm work in place: the container runs as root, so it left root-owned node_modules in
# the repo that the host user could not remove, and subsequent installs reconciled
# against that debris instead of installing cleanly.
#
# node_modules lives in a named volume so it survives between runs. That matters because
# this host's path to the npm registry drops 20-30% of packets, making a cold install
# minutes rather than seconds.
#
# There is deliberately no lockfile: see the note at the install step. Versions are
# therefore not pinned, and the compile is gated on verifying the tool instead.
#
#   scripts/build-frontend.sh           compile + build the image
#   scripts/build-frontend.sh --fresh   discard the cached node_modules first
set -euo pipefail
cd "$(dirname "$0")/.."

NODE_IMAGE="node:20-alpine"
VOLUME="framepost-frontend-node-modules"
CONTAINER="framepost-frontend-build"

if [ "${1:-}" = "--fresh" ]; then
  echo "==> discarding cached node_modules"
  docker volume rm -f "$VOLUME" >/dev/null 2>&1 || true
fi
docker volume create "$VOLUME" >/dev/null
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

echo "==> compiling in a plain container"
docker run --name "$CONTAINER" \
  -v "$PWD/frontend:/src:ro" \
  -v "$VOLUME:/app/node_modules" \
  -w /app "$NODE_IMAGE" sh -c '
    set -e
    npm config set fetch-timeout 600000 >/dev/null
    npm config set fetch-retries 10 >/dev/null
    npm config set fetch-retry-maxtimeout 120000 >/dev/null

    # Copy the source in; node_modules is a mounted volume and must not be overwritten.
    cd /app
    for f in package.json package-lock.json index.html vite.config.ts tsconfig.json \
             tsconfig.app.json tsconfig.node.json; do
      [ -f "/src/$f" ] && cp "/src/$f" . || true
    done
    for d in src public; do
      [ -d "/src/$d" ] && { rm -rf "./$d"; cp -r "/src/$d" .; } || true
    done

    # npm install, deliberately not npm ci. On this setup npm ci resolves the
    # lockfile without @esbuild at all -- 47 packages, no build tool, two seconds --
    # while npm install from the same package.json produces the correct tree. That is
    # the long-standing npm handling of platform-specific optionalDependencies; the
    # lockfile itself is well-formed, with all 26 @esbuild entries present.
    #
    # The cost is that exact versions are not pinned. The verification below is what
    # replaces that guarantee: nothing is compiled until the tool is proven to run.
    npm install --no-audit --no-fund

    # Verify the tool actually arrived whole before trusting it to compile anything.
    B=node_modules/@esbuild/linux-x64/bin/esbuild
    [ -f "$B" ] || { echo "esbuild binary missing after install" >&2; exit 1; }
    "$B" --version >/dev/null 2>&1 || {
      echo "esbuild present but will not run ($(stat -c%s "$B") bytes) -- truncated" >&2
      exit 1; }
    echo "  esbuild ok: $(stat -c%s "$B") bytes, $("$B" --version)"

    npm run build
  '

echo "==> extracting dist/"
rm -rf frontend/dist
docker cp "$CONTAINER:/app/dist" frontend/dist
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

[ -n "$(ls -A frontend/dist 2>/dev/null)" ] || {
  echo "compile produced no dist/ -- refusing to build an image around nothing" >&2; exit 1; }
echo "==> dist: $(find frontend/dist -type f | wc -l | tr -d ' ') files"

echo "==> building the nginx image"
docker compose build nginx
echo "==> done. 'docker compose up -d nginx' to deploy it."
