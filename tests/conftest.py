import pytest
from app.database import engine as main_engine

@pytest.fixture(scope="session", autouse=True)
def cleanup_database_connections():
    yield
    # Dispose of the main database engine to close any remaining connection pool connections
    main_engine.dispose()
