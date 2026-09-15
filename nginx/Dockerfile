# Multi-stage: build the React/Vite frontend, then serve with nginx + reverse-proxy /api.
# Build context is the project root so we can see both frontend/ and nginx/.

# Stage 1 — frontend build
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# Long fetch timeouts: this host loses 20-30% of packets to Cloudflare, which fronts
# registry.npmjs.org, so an install that takes seconds elsewhere takes minutes here and
# npm's defaults give up first.
RUN npm config set fetch-timeout 600000 \
 && npm config set fetch-retries 10 \
 && npm config set fetch-retry-maxtimeout 120000 \
 && npm ci

# Prove the compiler survived the install before trusting it to compile anything.
#
# On 2026-09-14 this binary arrived at 6,672,013 bytes against an expected 10,358,936 --
# an exact prefix of the real file. It segfaulted, and vite reported it as `Error: The
# service was stopped: write EPIPE` from esbuild's child process -- an error naming a
# closed pipe, five levels away from the truncated file that caused it.
#
# The note that used to sit here argued the truncation could not have been a bad download
# "because npm verifies tarballs against the lockfile's integrity hash". There was no
# lockfile. It had never been committed, so this line ran `npm install`, which resolves a
# fresh tree on every build and pins nothing -- which is also why twelve attempts to
# reproduce it proved less than they seemed to. The lockfile is committed now and `npm ci`
# is unconditional, so a tarball that arrives wrong is rejected on its hash rather than
# unpacked and run.
#
# The check stays. It is cheap, and it covers what a hash cannot: damage after extraction.
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
