from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from typing import Generator
from sqlalchemy.orm import Session
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./allsky_map.db")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
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
