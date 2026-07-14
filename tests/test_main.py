import os
import pytest
import hashlib
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.database import Base, get_db
from app.models import CameraDB

# Setup test database engine and session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_FILE = "./test_allsky_map.db"
test_engine = create_engine(f"sqlite:///{DATABASE_FILE}", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Import app.main after we define/override
import app.main as app_module

app_module.app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    # Ensure the file exists so we cover the deletion code
    with open(DATABASE_FILE, "w") as f:
        f.write("")
    os.remove(DATABASE_FILE)
            
    Base.metadata.create_all(bind=test_engine)
    
    yield
    
    # Dispose engine to close all connections and release file lock
    test_engine.dispose()
    os.remove(DATABASE_FILE)

@pytest.fixture(autouse=True)
def clean_db():
    # Clear the database tables before each test
    db = TestSessionLocal()
    db.query(CameraDB).delete()
    db.commit()
    db.close()
    
    # Reset the rate limiter requests in the tests so they don't block each other
    app_module.register_limiter.requests.clear()
    app_module.ping_limiter.requests.clear()

def test_register_camera():
    client = TestClient(app_module.app)
    response = client.post("/api/register")
    assert response.status_code == 200
    data = response.json()
    assert "api_key" in data
    assert data["api_key"].startswith("allsky_live_")
    
    # Verify DB entry using SHA-256 hash of the api_key
    db = TestSessionLocal()
    hashed_key = hashlib.sha256(data["api_key"].encode("utf-8")).hexdigest()
    cam = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    assert cam is not None
    assert cam.name is None
    db.close()

def test_get_cameras_coordinate_fuzzing():
    db = TestSessionLocal()
    
    # Camera with high-precision coordinates -> should be fuzzed/rounded in output
    cam1 = CameraDB(
        api_key="key1",
        name="Cam 1",
        owner="Owner 1",
        lat=10.12345,
        lng=20.67891,
        site_url="http://site1.com",
        site_url_valid=True,
        image_url="http://site1.com/img.jpg",
        image_url_valid=True,
        status="online"
    )
    # Camera with None coordinates
    cam2 = CameraDB(
        api_key="key2",
        name="Cam 2",
        owner=None,
        lat=None,
        lng=None,
        site_url=None,
        image_url=None,
        status="offline"
    )
    # Camera with invalid urls -> should be hidden in output
    cam3 = CameraDB(
        api_key="key3",
        name="Cam 3",
        owner="Owner 3",
        lat=10.0,
        lng=20.0,
        site_url="http://dead-site.com",
        site_url_valid=False,
        image_url="http://dead-img.com",
        image_url_valid=False,
        status="online"
    )
    
    db.add_all([cam1, cam2, cam3])
    db.commit()
    db.close()
    
    client = TestClient(app_module.app)
    response = client.get("/api/cameras")
    assert response.status_code == 200
    res_data = response.json()
    assert len(res_data) == 3
    
    cam1_res = [c for c in res_data if c["name"] == "Cam 1"][0]
    assert cam1_res["lat"] == 10.12  # Fuzzed to 2 decimals
    assert cam1_res["lng"] == 20.68  # Fuzzed to 2 decimals
    assert cam1_res["siteUrl"] == "http://site1.com"
    assert cam1_res["imageUrl"] == "http://site1.com/img.jpg"
    assert cam1_res["lastSeen"] != ""
    
    cam2_res = [c for c in res_data if c["name"] == "Cam 2"][0]
    assert cam2_res["lat"] == 0.0
    assert cam2_res["lng"] == 0.0
    
    cam3_res = [c for c in res_data if c["name"] == "Cam 3"][0]
    assert cam3_res["siteUrl"] == ""  # Hidden
    assert cam3_res["imageUrl"] == ""  # Hidden

def test_ping_camera_success():
    db = TestSessionLocal()
    # Create a registered camera using hashed API key
    raw_key = "allsky_live_test_key"
    hashed_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    cam = CameraDB(api_key=hashed_key, name="Old Name")
    db.add(cam)
    db.commit()
    db.close()
    
    client = TestClient(app_module.app)
    payload = {
        "name": "New Name",
        "owner": "New Owner",
        "lat": 1.23,
        "lng": 4.56,
        "siteUrl": "http://new.com",
        "imageUrl": "http://new.com/img.jpg"
    }
    response = client.post(
        "/api/ping",
        json=payload,
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 200
    assert response.json() == {"message": "Success"}
    
    # Verify DB update
    db = TestSessionLocal()
    cam_db = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    assert cam_db.name == "New Name"
    assert cam_db.owner == "New Owner"
    assert cam_db.lat == 1.23
    assert cam_db.lng == 4.56
    assert cam_db.site_url == "http://new.com"
    assert cam_db.image_url == "http://new.com/img.jpg"
    assert cam_db.status == "online"
    db.close()

def test_ping_camera_partial_fields_and_validation():
    db = TestSessionLocal()
    raw_key = "allsky_live_test_key"
    hashed_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    cam = CameraDB(
        api_key=hashed_key,
        name="Original Name",
        owner="Original Owner",
        lat=1.0,
        lng=2.0,
        status="offline"
    )
    db.add(cam)
    db.commit()
    db.close()
    
    client = TestClient(app_module.app)
    
    # 1. Invalid payload: missing name (required)
    response = client.post(
        "/api/ping",
        json={"lat": 1.0, "lng": 2.0},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 422
    
    # 2. Invalid payload: coordinate range validation (lat > 90)
    response = client.post(
        "/api/ping",
        json={"name": "Cam", "lat": 95.0, "lng": 2.0},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 422
    
    # 3. Invalid payload: url validation (siteUrl is not valid URL)
    response = client.post(
        "/api/ping",
        json={"name": "Cam", "lat": 1.0, "lng": 2.0, "siteUrl": "invalid-url"},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 422
    
    # 4. Valid partial payload: missing optional fields owner, siteUrl, imageUrl (should fallback to default "")
    response = client.post(
        "/api/ping",
        json={"name": "New Name", "lat": 1.5, "lng": 2.5},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 200
    
    db = TestSessionLocal()
    cam_db = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    assert cam_db.name == "New Name"
    assert cam_db.owner == ""
    assert cam_db.site_url == ""
    assert cam_db.image_url == ""
    db.close()

def test_ping_camera_invalid_key():
    client = TestClient(app_module.app)
    payload = {"name": "Test", "lat": 1.0, "lng": 2.0}
    response = client.post(
        "/api/ping",
        json=payload,
        headers={"X-API-Key": "non-existent-key"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API Key"

def test_rate_limiting():
    client = TestClient(app_module.app)
    
    # `/api/register` rate limiter check (limit = 5 requests per window)
    for _ in range(5):
        response = client.post("/api/register")
        assert response.status_code == 200
        
    # The 6th request should be blocked with 429 Too Many Requests
    response = client.post("/api/register")
    assert response.status_code == 429
    assert response.json()["detail"] == "Too Many Requests"

def test_payload_limits():
    client = TestClient(app_module.app)
    
    # Send a request body larger than 1MB
    large_payload = "A" * (1024 * 1024 + 100)
    response = client.post("/api/ping", content=large_payload, headers={"X-API-Key": "somekey", "Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["detail"] == "Request Entity Too Large"

def test_security_headers():
    client = TestClient(app_module.app)
    response = client.get("/")
    assert response.status_code == 200
    
    headers = response.headers
    assert "Content-Security-Policy" in headers
    assert "X-Frame-Options" in headers
    assert headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" in headers
    assert "X-Content-Type-Options" in headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Referrer-Policy" in headers

def test_static_files():
    client = TestClient(app_module.app)
    response = client.get("/")
    assert response.status_code == 200
    
    response = client.get("/index.html")
    assert response.status_code == 200

def test_startup_event(monkeypatch):
    mock_reap = MagicMock()
    mock_check = MagicMock()
    async def dummy_reap():
        mock_reap()
    async def dummy_check():
        mock_check()
        
    monkeypatch.setattr(app_module, "reap_the_dead", dummy_reap)
    monkeypatch.setattr(app_module, "check_dead_links", dummy_check)
    
    with TestClient(app_module.app):
        pass
        
    mock_reap.assert_called_once()
    mock_check.assert_called_once()

def test_camera_widget():
    db = TestSessionLocal()
    # Create an online camera and an offline camera
    cam1 = CameraDB(api_key="key1", name="OnlineCam", status="online", owner="Alice")
    cam2 = CameraDB(api_key="key2", name="OfflineCam", status="offline", owner=None)
    db.add_all([cam1, cam2])
    db.commit()
    db.close()
    
    client = TestClient(app_module.app)
    
    # 1. Test online widget
    response = client.get("/api/cameras/OnlineCam/widget")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/svg+xml"
    svg = response.text
    assert "OnlineCam" in svg
    assert "Alice" in svg
    assert "Online" in svg
    assert "#2ecc71" in svg  # green color for online status
    
    # 2. Test offline widget
    response = client.get("/api/cameras/OfflineCam/widget")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/svg+xml"
    svg = response.text
    assert "OfflineCam" in svg
    assert "Unknown Owner" in svg
    assert "Offline" in svg
    assert "#95a5a6" in svg  # grey color for offline status
    
    # 3. Test not found
    response = client.get("/api/cameras/NonExistent/widget")
    assert response.status_code == 404

def test_camera_image_proxy(monkeypatch):
    db = TestSessionLocal()
    # Cam1: valid link
    cam1 = CameraDB(api_key="key1", name="Cam1", image_url="http://valid-site.com/cam1.jpg", image_url_valid=True)
    # Cam2: invalid link
    cam2 = CameraDB(api_key="key2", name="Cam2", image_url="http://invalid-site.com/cam2.jpg", image_url_valid=False)
    # Cam3: empty image url
    cam3 = CameraDB(api_key="key3", name="Cam3", image_url="")
    # Cam4: valid image link but server returns 404
    cam4 = CameraDB(api_key="key4", name="Cam4", image_url="http://not-found-site.com/cam4.jpg", image_url_valid=True)
    db.add_all([cam1, cam2, cam3, cam4])
    db.commit()
    db.close()

    client = TestClient(app_module.app)

    # Bypass SSRF DNS resolution for fake test hostnames
    monkeypatch.setattr(app_module, "is_safe_url", lambda url: True)

    # Mock httpx.AsyncClient.get
    async def mock_get(url, **kwargs):
        res = MagicMock()
        if "valid-site" in url:
            res.status_code = 200
            res.headers = {"content-type": "image/png"}
            res.content = b"fake-image-bytes"
        else:
            res.status_code = 404
            res.content = b""
        return res

    mock_async_client = MagicMock()
    mock_async_client.__aenter__.return_value = MagicMock(get=mock_get)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: mock_async_client)

    # 1. Success case
    response = client.get("/api/cameras/Cam1/image")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert response.content == b"fake-image-bytes"

    # 2. Invalid image link -> returns SVG placeholder
    response = client.get("/api/cameras/Cam2/image")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/svg+xml"
    assert "Camera Feed Unavailable" in response.text

    # 3. Empty image link -> returns SVG placeholder
    response = client.get("/api/cameras/Cam3/image")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/svg+xml"
    assert "Camera Feed Unavailable" in response.text

    # 4. Camera not found -> returns placeholder
    response = client.get("/api/cameras/NonExistent/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text

    # 5. Proxy returns 404 status -> returns placeholder
    response = client.get("/api/cameras/Cam4/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text

    # 6. Network exception during fetch -> returns placeholder
    async def mock_get_error(url, **kwargs):
        raise Exception("Network timeout")
    mock_async_client_error = MagicMock()
    mock_async_client_error.__aenter__.return_value = MagicMock(get=mock_get_error)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: mock_async_client_error)

    response = client.get("/api/cameras/Cam1/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text

def test_migration_exception(monkeypatch, capsys):
    from app.main import run_migrations, engine
    mock_begin = MagicMock(side_effect=Exception("Migration connection failed"))
    monkeypatch.setattr(engine, "begin", mock_begin)
    
    run_migrations()
    
    captured = capsys.readouterr()
    assert "Migration error: Migration connection failed" in captured.out

def test_migration_alter_table(monkeypatch):
    from app.main import run_migrations, engine
    
    mock_conn = MagicMock()
    # 1. SELECT 1 FROM cameras -> succeeds
    # 2. SELECT site_url_valid -> raises exception
    # 3. ALTER TABLE ... -> succeeds
    # 4. SELECT image_url_valid -> raises exception
    # 5. ALTER TABLE ... -> succeeds
    mock_conn.execute.side_effect = [
        None,
        Exception("Column not found"),
        None,
        Exception("Column not found"),
        None
    ]
    
    mock_begin = MagicMock()
    mock_begin.return_value.__enter__.return_value = mock_conn
    monkeypatch.setattr(engine, "begin", mock_begin)
    
    run_migrations()
    
    assert mock_conn.execute.call_count == 5

def test_migration_table_does_not_exist(monkeypatch):
    from app.main import run_migrations, engine
    
    mock_conn = MagicMock()
    # First call (SELECT 1 FROM cameras) raises exception (no table)
    # The rest are skipped because table_exists is False
    mock_conn.execute.side_effect = Exception("no such table")
    
    mock_begin = MagicMock()
    mock_begin.return_value.__enter__.return_value = mock_conn
    monkeypatch.setattr(engine, "begin", mock_begin)
    
    run_migrations()
    
    assert mock_conn.execute.call_count == 1


# ---------------------------------------------------------------------------
# Rate limiter _cleanup (line 74 + 85-91)
# ---------------------------------------------------------------------------

def test_rate_limiter_cleanup_removes_stale_ips():
    """
    Trigger the stale-IP cleanup path: seed requests dict with an old
    timestamp so the next check() call runs _cleanup and evicts it.
    """
    import time
    limiter = app_module.InMemoryRateLimiter(limit=5, window=60)

    # Inject a stale entry (timestamp way in the past)
    old_time = time.time() - 400
    limiter.requests["192.0.2.1"] = [old_time]

    # Force last_cleanup to be >300s ago so _cleanup fires
    limiter.last_cleanup = time.time() - 400

    client = TestClient(app_module.app)
    # A real GET request will trigger check() on one of the app limiters,
    # but we call _cleanup directly for precision
    limiter._cleanup(time.time())

    # Stale IP should have been evicted entirely
    assert "192.0.2.1" not in limiter.requests


def test_rate_limiter_cleanup_keeps_active_ips():
    """Active (recent) timestamps survive the cleanup pass."""
    import time
    limiter = app_module.InMemoryRateLimiter(limit=5, window=60)

    now = time.time()
    limiter.requests["10.0.0.1"] = [now - 10]  # within window
    limiter.requests["10.0.0.2"] = [now - 400]  # outside window

    limiter._cleanup(now)

    assert "10.0.0.1" in limiter.requests
    assert "10.0.0.2" not in limiter.requests


@pytest.mark.asyncio
async def test_rate_limiter_cleanup_via_check():
    """_cleanup is called via check() when last_cleanup is stale (line 74)."""
    import time
    limiter = app_module.InMemoryRateLimiter(limit=100, window=60)

    # Plant a stale IP and mark cleanup as overdue
    old_time = time.time() - 400
    limiter.requests["192.0.2.99"] = [old_time]
    limiter.last_cleanup = old_time

    # Drive check() through a fake request
    fake_request = MagicMock()
    fake_request.headers = {}
    fake_request.client.host = "127.0.0.1"

    await limiter.check(fake_request)

    # Stale entry should be gone after cleanup
    assert "192.0.2.99" not in limiter.requests


# ---------------------------------------------------------------------------
# Payload size middleware — chunked body path (lines 140-141, 143)
# ---------------------------------------------------------------------------

def test_payload_size_limit_chunked_body():
    """Chunked body exceeding MAX_PAYLOAD_SIZE → 413 (lines 140-141).
    We monkeypatch request.body() to return an oversized bytes object so
    the body-buffering branch fires even though TestClient sends Content-Length.
    """
    import asyncio as _asyncio
    _orig_limit = app_module.MAX_PAYLOAD_SIZE
    # Temporarily lower the limit so even a tiny body triggers it
    app_module.MAX_PAYLOAD_SIZE = 1
    try:
        client = TestClient(app_module.app, raise_server_exceptions=False)
        response = client.post(
            "/api/ping",
            # Send without Content-Length by using a generator
            content=iter([b"ab"]),  # 2 bytes, above our patched limit of 1
            headers={"X-API-Key": "allsky_live_test", "Content-Type": "application/json"},
        )
        assert response.status_code == 413
    finally:
        app_module.MAX_PAYLOAD_SIZE = _orig_limit


# ---------------------------------------------------------------------------
# Oversized X-API-Key header (line 219)
# ---------------------------------------------------------------------------

def test_ping_oversized_api_key():
    """X-API-Key longer than MAX_API_KEY_LEN must return 400 before hashing."""
    client = TestClient(app_module.app)
    oversized_key = "allsky_live_" + "A" * (app_module.MAX_API_KEY_LEN + 1)
    response = client.post(
        "/api/ping",
        json={"name": "Test", "lat": 0.0, "lng": 0.0},
        headers={"X-API-Key": oversized_key},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid API Key"


# ---------------------------------------------------------------------------
# Image proxy — remaining paths (lines 283-284, 292-295, 304, 309)
# ---------------------------------------------------------------------------

def test_image_proxy_ssrf_blocked(monkeypatch):
    """is_safe_url returns False → placeholder (lines 283-284)."""
    db = TestSessionLocal()
    cam = CameraDB(api_key="ssrf_key", name="SSRFCam",
                   image_url="http://internal.host/cam.jpg", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()

    # SSRF guard blocks this URL
    monkeypatch.setattr(app_module, "is_safe_url", lambda url: False)

    client = TestClient(app_module.app)
    response = client.get("/api/cameras/SSRFCam/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text


def test_image_proxy_redirect_missing_location(monkeypatch):
    """Redirect with no Location header → placeholder (lines 292-295)."""
    db = TestSessionLocal()
    cam = CameraDB(api_key="redir_key", name="RedirCam",
                   image_url="http://example.com/cam.jpg", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()

    monkeypatch.setattr(app_module, "is_safe_url", lambda url: True)

    async def mock_get(url, **kwargs):
        res = MagicMock()
        res.status_code = 301
        res.headers = {}  # no Location header
        return res

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = MagicMock(get=mock_get)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *a, **kw: mock_client)

    client = TestClient(app_module.app)
    response = client.get("/api/cameras/RedirCam/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text


def test_image_proxy_redirect_unsafe_location(monkeypatch):
    """Redirect to unsafe URL → placeholder (lines 293-294)."""
    db = TestSessionLocal()
    cam = CameraDB(api_key="unsafe_redir_key", name="UnsafeRedirCam",
                   image_url="http://example.com/cam.jpg", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()

    # First call (original URL) passes; redirect destination is blocked
    call_count = 0

    def safe_url_selective(url):
        nonlocal call_count
        call_count += 1
        return call_count == 1  # only first URL passes

    monkeypatch.setattr(app_module, "is_safe_url", safe_url_selective)

    async def mock_get(url, **kwargs):
        res = MagicMock()
        res.status_code = 301
        res.headers = {"location": "http://internal.evil/cam.jpg"}
        return res

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = MagicMock(get=mock_get)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *a, **kw: mock_client)

    client = TestClient(app_module.app)
    response = client.get("/api/cameras/UnsafeRedirCam/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text


def test_image_proxy_redirect_follows_safe_url(monkeypatch):
    """Redirect to safe destination → fetches destination and returns image (line 295)."""
    db = TestSessionLocal()
    cam = CameraDB(api_key="safe_redir_key", name="SafeRedirCam",
                   image_url="http://example.com/cam.jpg", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()

    monkeypatch.setattr(app_module, "is_safe_url", lambda url: True)

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        res = MagicMock()
        if call_count == 1:
            # First request: 301 redirect to safe destination
            res.status_code = 301
            res.headers = {"location": "http://cdn.example.com/cam.jpg"}
        else:
            # Second request (redirect destination): valid image
            res.status_code = 200
            res.headers = {"content-type": "image/jpeg"}
            res.content = b"image-bytes-from-cdn"
        return res

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = MagicMock(get=mock_get)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *a, **kw: mock_client)

    client = TestClient(app_module.app)
    response = client.get("/api/cameras/SafeRedirCam/image")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.content == b"image-bytes-from-cdn"


def test_image_proxy_wrong_content_type(monkeypatch):
    """Non-image content-type → placeholder (line 304)."""
    db = TestSessionLocal()
    cam = CameraDB(api_key="ct_key", name="CTCam",
                   image_url="http://example.com/cam.html", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()

    monkeypatch.setattr(app_module, "is_safe_url", lambda url: True)

    async def mock_get(url, **kwargs):
        res = MagicMock()
        res.status_code = 200
        res.headers = {"content-type": "text/html; charset=utf-8"}
        res.content = b"<html>not an image</html>"
        return res

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = MagicMock(get=mock_get)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *a, **kw: mock_client)

    client = TestClient(app_module.app)
    response = client.get("/api/cameras/CTCam/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text


def test_image_proxy_oversized_image(monkeypatch):
    """Response body exceeds MAX_IMAGE_BYTES → placeholder (line 309)."""
    db = TestSessionLocal()
    cam = CameraDB(api_key="size_key", name="SizeCam",
                   image_url="http://example.com/huge.jpg", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()

    monkeypatch.setattr(app_module, "is_safe_url", lambda url: True)

    async def mock_get(url, **kwargs):
        res = MagicMock()
        res.status_code = 200
        res.headers = {"content-type": "image/jpeg"}
        res.content = b"x" * (app_module.MAX_IMAGE_BYTES + 1)
        return res

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = MagicMock(get=mock_get)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *a, **kw: mock_client)

    client = TestClient(app_module.app)
    response = client.get("/api/cameras/SizeCam/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text
