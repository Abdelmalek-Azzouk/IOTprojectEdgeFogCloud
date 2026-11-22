"""Database engine/session setup. SQLite by default, PostgreSQL via DATABASE_URL."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_URL = "sqlite:///./data/smartdriver.db"


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", DEFAULT_URL)
    if url.startswith("sqlite"):
        if ":memory:" in url or url == "sqlite://":
            # shared single connection so every session sees the same in-memory DB (tests)
            return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        path = url.replace("sqlite:///", "", 1)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(eng=None):
    from cloud import models  # noqa: F401  (register tables)
    Base.metadata.create_all(bind=eng or engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
