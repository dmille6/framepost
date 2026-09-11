"""Shared fixtures. Tests run inside the backend container (`docker compose exec -T
backend python -m pytest tests/ -q`) where the app's env vars and deps already exist.
DB-touching tests get a fresh in-memory SQLite with the full schema — never the real DB.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401 — registers all tables on Base.metadata


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_live_r2(monkeypatch):
    """No test ever writes to the real R2 bucket.

    r2.configured() reads process environment, and the container that runs this suite
    holds the production credentials — so without this any test exercising the Instagram
    path silently uploads objects to live storage. It did, before this existed. Tests
    that want the R2 path opt in through the r2_stub fixture.
    """
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def r2_stub(monkeypatch):
    """R2 configured, but put/delete/presign held in memory. Returns the object store so
    a test can assert on what was written and what survived cleanup."""
    from services import r2

    store: dict[str, bytes] = {}
    for key, value in (("R2_ACCOUNT_ID", "acct"), ("R2_ACCESS_KEY_ID", "ak"),
                       ("R2_SECRET_ACCESS_KEY", "sk"), ("R2_BUCKET", "bucket")):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(r2, "put", lambda k, body, **kw: store.__setitem__(k, body))
    monkeypatch.setattr(r2, "delete", lambda k: store.pop(k, None))
    monkeypatch.setattr(r2, "presign_get", lambda k, **kw: f"https://r2.test/{k}?sig=stub")
    monkeypatch.setattr(r2, "list_keys", lambda prefix="": [
        k for k in store if k.startswith(prefix)])
    return store
