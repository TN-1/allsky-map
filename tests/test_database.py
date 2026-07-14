import pytest
from unittest.mock import MagicMock
import app.database
from app.database import get_db

def test_get_db(monkeypatch):
    # Mock SessionLocal to return a mock DB session
    mock_db = MagicMock()
    mock_session_local = MagicMock(return_value=mock_db)
    monkeypatch.setattr(app.database, "SessionLocal", mock_session_local)
    
    gen = get_db()
    db = next(gen)
    assert db is mock_db
    
    try:
        next(gen)
    except StopIteration:
        pass
        
    mock_db.close.assert_called_once()
