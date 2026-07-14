import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import CameraDB

def test_camera_model_defaults():
    cam = CameraDB(api_key="test_api")
    assert cam.api_key == "test_api"
    # SQLAlchemy default values are not populated on the Python object before session commit
    assert cam.status is None

def test_camera_model_db_defaults():
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    
    db = TestSessionLocal()
    cam = CameraDB(api_key="test_defaults")
    db.add(cam)
    db.commit()
    
    assert cam.status == "online"
    assert cam.last_seen is not None
    assert isinstance(cam.last_seen, datetime)
    
    db.close()
    test_engine.dispose()

