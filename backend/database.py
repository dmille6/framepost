from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from config import settings

# SQLite + WAL + the PRAGMAs from the brief. `check_same_thread=False` is required because
# FastAPI request handlers and APScheduler jobs run on different threads but share the engine.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA synchronous = NORMAL")
    # WAL lets readers and one writer coexist; it does NOT queue two writers. Without a
    # busy timeout SQLite fails the loser instantly, and it did: through September the
    # nightly engagement sync held the write lock across its HTTP calls and the
    # per-minute heartbeat and 5-minute disk sampler died with "database is locked" two
    # or three times a night, 36 tracebacks in the last week alone. Nothing was lost --
    # the next tick succeeded -- but real failures would have been invisible in that
    # noise. 30s is chosen against the sync's observed lock span, not the heartbeat's
    # tolerance: a background job waiting half a minute for a write costs nothing.
    cur.execute("PRAGMA busy_timeout = 30000")
    cur.execute("PRAGMA foreign_keys = ON")
    cur.execute("PRAGMA temp_store = MEMORY")
    cur.execute("PRAGMA mmap_size = 268435456")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
