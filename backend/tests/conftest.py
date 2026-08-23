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
