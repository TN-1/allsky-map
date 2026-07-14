import pytest
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app.database import Base
from app.models import CameraDB
from app.tasks import reap_the_dead, check_dead_links, _safe_head, _safe_get_image

@pytest.mark.asyncio
async def test_reap_the_dead_success(monkeypatch):
    # Setup in-memory DB engine and session for tasks
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    
    # Override SessionLocal in app.tasks
    import app.tasks
    monkeypatch.setattr(app.tasks, "SessionLocal", TestSessionLocal)
    
    # Populate test database
    db = TestSessionLocal()
    now = datetime.now(timezone.utc)
    
    # 1. Abandoned key (no name, older than 24h) -> deleted
    c1 = CameraDB(api_key="key1", name=None, last_seen=now - timedelta(hours=25), status="online")
    # 2. Key with name = None but fresh (< 24h) -> not deleted
    c2 = CameraDB(api_key="key2", name=None, last_seen=now - timedelta(hours=10), status="online")
    # 3. Active camera, silent for > 24h -> status offline
    c3 = CameraDB(api_key="key3", name="Cam3", last_seen=now - timedelta(hours=25), status="online")
    # 4. Active camera, fresh -> status online
    c4 = CameraDB(api_key="key4", name="Cam4", last_seen=now - timedelta(hours=10), status="online")
    
    db.add_all([c1, c2, c3, c4])
    db.commit()
    db.close()
    
    # We patch asyncio.sleep to run once and then raise CancelledError
    call_count = 0
    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()
            
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)
    
    # Run the reaper task
    try:
        await reap_the_dead()
    except asyncio.CancelledError:
        pass
        
    # Check database state
    db = TestSessionLocal()
    remaining_keys = [c.api_key for c in db.query(CameraDB).all()]
    assert "key1" not in remaining_keys
    assert "key2" in remaining_keys
    assert "key3" in remaining_keys
    assert "key4" in remaining_keys
    
    cam3 = db.query(CameraDB).filter(CameraDB.api_key == "key3").first()
    assert cam3.status == "offline"
    
    cam4 = db.query(CameraDB).filter(CameraDB.api_key == "key4").first()
    assert cam4.status == "online"
    
    db.close()
    test_engine.dispose()

@pytest.mark.asyncio
async def test_reap_the_dead_exception(monkeypatch, caplog):
    import app.tasks
    
    # Mock SessionLocal to return a mock DB session that raises an error
    mock_db = MagicMock()
    mock_db.__enter__.return_value = mock_db
    mock_db.query.side_effect = Exception("Database connection failed")
    mock_session_local = MagicMock(return_value=mock_db)
    
    monkeypatch.setattr(app.tasks, "SessionLocal", mock_session_local)
    
    # Mock sleep to cancel after one iteration
    async def mock_sleep(seconds):
        raise asyncio.CancelledError()
            
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)
    
    try:
        await reap_the_dead()
    except asyncio.CancelledError:
        pass
        
    mock_db.rollback.assert_called_once()
    mock_db.close.assert_called_once()
    
    assert "Error in Reaper task" in caplog.text

@pytest.mark.asyncio
async def test_check_dead_links_success(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    
    import app.tasks
    monkeypatch.setattr(app.tasks, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)
    
    # Populate DB
    db = TestSessionLocal()
    # Cam1: Valid site & img links
    cam1 = CameraDB(
        api_key="key1", name="Cam1",
        site_url="http://ok-site.com", site_url_valid=False,
        image_url="http://ok-img.com", image_url_valid=False
    )
    # Cam2: Invalid site link (404), valid img link
    cam2 = CameraDB(
        api_key="key2", name="Cam2",
        site_url="http://bad-site.com", site_url_valid=True,
        image_url="http://ok-img.com", image_url_valid=False
    )
    # Cam3: Site raises exception, img raises exception
    cam3 = CameraDB(
        api_key="key3", name="Cam3",
        site_url="http://error-site.com", site_url_valid=True,
        image_url="http://error-site.com/image.jpg", image_url_valid=True
    )
    # Cam4: Empty links
    cam4 = CameraDB(
        api_key="key4", name="Cam4",
        site_url=None, site_url_valid=False,
        image_url="", image_url_valid=False
    )
    # Cam5: Image link returns non-image content-type
    cam5 = CameraDB(
        api_key="key5", name="Cam5",
        site_url=None, site_url_valid=True,
        image_url="http://non-img.com", image_url_valid=True
    )
    db.add_all([cam1, cam2, cam3, cam4, cam5])
    db.commit()
    db.close()
    
    # Mock httpx AsyncClient
    mock_client = MagicMock()
    async def mock_get(url, **kwargs):
        res = MagicMock()
        if "ok-site.com" in url:
            res.status_code = 200
            res.headers = {}
        elif "ok-img.com" in url:
            res.status_code = 200
            res.headers = {"content-type": "image/jpeg"}
        elif "bad-site.com" in url:
            res.status_code = 404
            res.headers = {}
        elif "non-img.com" in url:
            res.status_code = 200
            res.headers = {"content-type": "text/html"}
        elif "error-site.com" in url:
            raise httpx.ConnectError("Connection failed")
        return res
        
    mock_client.get = mock_get
    mock_client.head = mock_get
    
    mock_async_client = MagicMock()
    mock_async_client.__aenter__.return_value = mock_client
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: mock_async_client)
    
    # Mock sleep to run once and exit
    call_count = 0
    async def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()
            
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)
    
    try:
        await check_dead_links()
    except asyncio.CancelledError:
        pass
        
    # Verify results
    db = TestSessionLocal()
    c1_db = db.query(CameraDB).filter(CameraDB.api_key == "key1").first()
    assert c1_db.site_url_valid is True
    assert c1_db.image_url_valid is True
    
    c2_db = db.query(CameraDB).filter(CameraDB.api_key == "key2").first()
    assert c2_db.site_url_valid is False
    assert c2_db.image_url_valid is True
    
    c3_db = db.query(CameraDB).filter(CameraDB.api_key == "key3").first()
    assert c3_db.site_url_valid is False
    assert c3_db.image_url_valid is False
    
    c4_db = db.query(CameraDB).filter(CameraDB.api_key == "key4").first()
    assert c4_db.site_url_valid is True
    assert c4_db.image_url_valid is True
    
    c5_db = db.query(CameraDB).filter(CameraDB.api_key == "key5").first()
    assert c5_db.site_url_valid is True
    assert c5_db.image_url_valid is False
    
    db.close()
    test_engine.dispose()

@pytest.mark.asyncio
async def test_check_dead_links_exception(monkeypatch, caplog):
    import app.tasks
    mock_db = MagicMock()
    mock_db.__enter__.return_value = mock_db
    mock_db.query.side_effect = Exception("DB error")
    mock_session_local = MagicMock(return_value=mock_db)
    monkeypatch.setattr(app.tasks, "SessionLocal", mock_session_local)
    
    async def mock_sleep(seconds):
        raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)
    
    try:
        await check_dead_links()
    except asyncio.CancelledError:
        pass
        
    mock_db.rollback.assert_called_once()
    mock_db.close.assert_called_once()
    assert "Error in Dead Link Checker task" in caplog.text


# ---------------------------------------------------------------------------
# _safe_head unit tests (lines 48, 53-56)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safe_head_unsafe_url_returns_false(monkeypatch):
    """is_safe_url=False → _safe_head returns False immediately (line 48)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: False)
    mock_client = MagicMock()
    result = await _safe_head(mock_client, "http://internal/cam")
    assert result is False
    mock_client.head.assert_not_called()


@pytest.mark.asyncio
async def test_safe_head_redirect_empty_location(monkeypatch):
    """Redirect with empty Location → returns False (lines 53-55)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    mock_res = MagicMock()
    mock_res.status_code = 301
    mock_res.headers = {}  # get("location", "") → ""

    async def fake_head(url, **kw):
        return mock_res

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_head(mock_client, "http://ok.example.com/")
    assert result is False


@pytest.mark.asyncio
async def test_safe_head_redirect_unsafe_location(monkeypatch):
    """Redirect to unsafe location → returns False (lines 54-55)."""
    import app.tasks
    call_count = 0

    def selective_safe(url):
        nonlocal call_count
        call_count += 1
        return call_count == 1  # first URL passes, redirect target blocked

    monkeypatch.setattr(app.tasks, "is_safe_url", selective_safe)

    mock_res = MagicMock()
    mock_res.status_code = 301
    mock_res.headers = {"location": "http://internal.evil/"}

    async def fake_head(url, **kw):
        return mock_res

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_head(mock_client, "http://ok.example.com/")
    assert result is False


@pytest.mark.asyncio
async def test_safe_head_redirect_follows_safe_location(monkeypatch):
    """Redirect to safe location → follows and returns status (line 56-57)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    first_res = MagicMock()
    first_res.status_code = 301
    first_res.headers = {"location": "http://safe.example.com/cam.jpg"}

    second_res = MagicMock()
    second_res.status_code = 200
    second_res.headers = {}

    call_count = 0

    async def fake_head(url, **kw):
        nonlocal call_count
        call_count += 1
        return first_res if call_count == 1 else second_res

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_head(mock_client, "http://ok.example.com/")
    assert result is True


@pytest.mark.asyncio
async def test_safe_head_network_exception(monkeypatch):
    """Network exception → returns False (exception handler)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    async def fake_head(url, **kw):
        raise httpx.ConnectError("timeout")

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_head(mock_client, "http://ok.example.com/")
    assert result is False


# ---------------------------------------------------------------------------
# _safe_get_image unit tests (lines 65, 69-72)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safe_get_image_unsafe_url_returns_false(monkeypatch):
    """is_safe_url=False → _safe_get_image returns False immediately (line 65)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: False)
    mock_client = MagicMock()
    result = await _safe_get_image(mock_client, "http://internal/img.jpg")
    assert result is False
    mock_client.head.assert_not_called()


@pytest.mark.asyncio
async def test_safe_get_image_redirect_empty_location(monkeypatch):
    """Redirect with empty Location → returns False (lines 69-71)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    mock_res = MagicMock()
    mock_res.status_code = 302
    mock_res.headers = {}

    async def fake_head(url, **kw):
        return mock_res

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_get_image(mock_client, "http://ok.example.com/img.jpg")
    assert result is False


@pytest.mark.asyncio
async def test_safe_get_image_redirect_unsafe_location(monkeypatch):
    """Redirect to blocked location → returns False (lines 70-71)."""
    import app.tasks
    call_count = 0

    def selective_safe(url):
        nonlocal call_count
        call_count += 1
        return call_count == 1

    monkeypatch.setattr(app.tasks, "is_safe_url", selective_safe)

    mock_res = MagicMock()
    mock_res.status_code = 301
    mock_res.headers = {"location": "http://bad.internal/img.jpg"}

    async def fake_head(url, **kw):
        return mock_res

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_get_image(mock_client, "http://ok.example.com/img.jpg")
    assert result is False


@pytest.mark.asyncio
async def test_safe_get_image_redirect_follows_safe_location(monkeypatch):
    """Redirect to safe location → checks content-type of destination (line 72)."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    first_res = MagicMock()
    first_res.status_code = 301
    first_res.headers = {"location": "http://cdn.example.com/img.jpg"}

    second_res = MagicMock()
    second_res.status_code = 200
    second_res.headers = {"content-type": "image/jpeg"}

    call_count = 0

    async def fake_head(url, **kw):
        nonlocal call_count
        call_count += 1
        return first_res if call_count == 1 else second_res

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_get_image(mock_client, "http://ok.example.com/img.jpg")
    assert result is True


@pytest.mark.asyncio
async def test_safe_get_image_network_exception(monkeypatch):
    """Network exception → returns False."""
    import app.tasks
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    async def fake_head(url, **kw):
        raise httpx.ConnectError("refused")

    mock_client = MagicMock()
    mock_client.head = fake_head
    result = await _safe_get_image(mock_client, "http://ok.example.com/img.jpg")
    assert result is False


# ---------------------------------------------------------------------------
# save_results exception path (lines 116-118)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_dead_links_save_results_exception(monkeypatch, caplog):
    """DB error during save_results triggers rollback and logs the error."""
    import app.tasks
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Seed one camera so the first DB read succeeds
    db = TestSessionLocal()
    cam = CameraDB(api_key="k1", name="Cam1",
                   site_url="http://ok.com", image_url="http://ok.com/img.jpg")
    db.add(cam)
    db.commit()
    db.close()

    call_count = 0
    real_sessions = []

    def failing_session_local():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: real session (get_cameras_to_check)
            s = TestSessionLocal()
            real_sessions.append(s)
            return s
        else:
            # Second call: mock that fails during save_results (db.get is the new API)
            mock_db = MagicMock()
            mock_db.get.side_effect = Exception("save DB error")
            return mock_db

    monkeypatch.setattr(app.tasks, "SessionLocal", failing_session_local)
    monkeypatch.setattr(app.tasks, "is_safe_url", lambda url: True)

    async def run_one_cycle():
        async def fake_to_thread(fn, *args):
            return fn(*args) if args else fn()

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        # Patch sleep to cancel after one iteration
        async def mock_sleep(_):
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        try:
            await check_dead_links()
        except asyncio.CancelledError:
            pass

    await run_one_cycle()

    # Explicitly close any real sessions to prevent ResourceWarning
    for s in real_sessions:
        s.close()

    assert "Error in Dead Link Checker task" in caplog.text

    test_engine.dispose()
