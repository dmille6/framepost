# Multi-stage: build the React/Vite frontend, then serve with nginx + reverse-proxy /api.
# Build context is the project root so we can see both frontend/ and nginx/.

# Stage 1 — frontend build
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
# Long fetch timeouts: this host loses 20-30% of packets to Cloudflare, which fronts
# registry.npmjs.org, so an install that takes seconds elsewhere takes minutes here and
# npm's defaults give up first.
RUN npm config set fetch-timeout 600000 \
 && npm config set fetch-retries 10 \
 && npm config set fetch-retry-maxtimeout 120000 \
 && if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Prove the compiler survived the install before trusting it to compile anything.
#
# On 2026-09-14 this binary arrived at 6,672,013 bytes against an expected 10,358,936 --
# an exact prefix of the real file, so a write that stopped early rather than a corrupt
# download (npm verifies tarballs against the lockfile's integrity hash, and would have
# rejected that). It segfaulted, and vite reported it as `Error: The service was stopped:
# write EPIPE` from esbuild's child process -- an error naming a closed pipe, five levels
# away from the truncated file that caused it.
#
# It has not recurred: twelve subsequent installs, serial and concurrent, under BuildKit
# and under docker run, all produced the correct binary. The cause was never found. This
# check costs nothing and turns a recurrence into one legible line instead of an hour.
RUN B=node_modules/@esbuild/linux-x64/bin/esbuild; \
    if [ ! -f "$B" ]; then echo "esbuild binary missing after install" >&2; exit 1; fi; \
    if ! "$B" --version >/dev/null 2>&1; then \
      echo "esbuild will not run ($(stat -c%s "$B") bytes, expected 10358936) -- bad install" >&2; \
      exit 1; \
    fi; \
    echo "esbuild ok: $(stat -c%s "$B") bytes, $("$B" --version)"

COPY frontend/ ./
RUN npm run build

# Stage 2 — nginx
FROM nginx:1.27-alpine
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 80
