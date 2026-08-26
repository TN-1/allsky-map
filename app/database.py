from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool
from typing import Generator
from sqlalchemy.orm import Session
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/allsky_map.db")

if DATABASE_URL.startswith("sqlite:///./data/"):
    os.makedirs("./data", exist_ok=True)

# Use NullPool for SQLite to avoid connection pool exhaustion.
# SQLite uses a file-level lock and can only handle one writer at a time,
# so pooling connections just causes them to pile up waiting on the lock.
# For PostgreSQL or other backends, the default QueuePool is fine.
_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    poolclass=NullPool if _is_sqlite else QueuePool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# SQLAlchemy 2.0 style DeclarativeBase
class Base(DeclarativeBase):
    pass

# Dependency to get DB session with type annotations
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
