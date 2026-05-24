"""FastAPI entrypoint. Phase 1: /health, auth (login/logout/me), CSRF middleware."""
from fastapi import FastAPI

from middleware import CSRFMiddleware
from routes import activity, ai, albums, analytics, auth, groups, health, history, performers, platforms, posts, profiles, reels, schedule, system, tags, title_templates
from routes import config as config_routes

app = FastAPI(title="FramePost", version="0.1.0")
app.add_middleware(CSRFMiddleware)

# /health stays at the root (no /api prefix) so Nginx can pass it through cleanly.
app.include_router(health.router)

# All other routes live under /api/.
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(schedule.router, prefix="/api/schedule", tags=["schedule"])
app.include_router(history.router, prefix="/api/published", tags=["history"])
app.include_router(profiles.router, prefix="/api/profiles", tags=["profiles"])
app.include_router(albums.router, prefix="/api/albums", tags=["albums"])
app.include_router(groups.router, prefix="/api/groups", tags=["groups"])
app.include_router(platforms.router, prefix="/api/platforms", tags=["platforms"])
# Flickr OAuth callback can't carry an auth session through the redirect from flickr.com,
# so the platforms router's CSRF requirements are loosened for that path inside the middleware.
app.include_router(config_routes.router, prefix="/api/config", tags=["config"])
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(title_templates.router, prefix="/api/title-templates", tags=["title-templates"])
app.include_router(activity.router, prefix="/api/activity", tags=["activity"])
app.include_router(reels.router, prefix="/api/reels", tags=["reels"])
app.include_router(performers.router, prefix="/api/performers", tags=["performers"])
