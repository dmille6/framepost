# Serve a frontend that was compiled OUTSIDE this build. See scripts/build-frontend.sh.
#
# Why the compile is not here: under BuildKit on this host, npm's install of esbuild
# lands truncated -- 6,672,013 bytes where 10,358,936 is expected, deterministically the
# same bytes on every attempt. The part-written binary segfaults, and the build dies with
# an EPIPE that points at a closed pipe rather than at the broken file.
#
# The identical install run via `docker run` produces the correct binary and builds
# cleanly, so the compile happens there and this stage only serves the result. That also
# means `docker compose build nginx` alone is no longer enough -- it would package
# whatever dist/ happens to be on disk. Use scripts/build-frontend.sh, which compiles
# first and then builds this image.
FROM nginx:1.27-alpine
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY frontend/dist /usr/share/nginx/html
EXPOSE 80
