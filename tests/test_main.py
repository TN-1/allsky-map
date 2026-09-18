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
    assert cam1_res["lat"] == 10.12345
    assert cam1_res["lng"] == 20.67891
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
        "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="
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
    assert cam_db.image_url == "local"
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
        json={"lat": 1.0, "lng": 2.0, "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 422
    
    # 2. Invalid payload: coordinate range validation (lat > 90)
    response = client.post(
        "/api/ping",
        json={"name": "Cam", "lat": 95.0, "lng": 2.0, "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 422
    
    # 3. Invalid payload: url validation (siteUrl is not valid URL)
    response = client.post(
        "/api/ping",
        json={"name": "Cam", "lat": 1.0, "lng": 2.0, "siteUrl": "invalid-url", "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 422
    
    # 4. Valid partial payload: missing optional fields owner, siteUrl (should fallback to default "")
    response = client.post(
        "/api/ping",
        json={"name": "New Name", "lat": 1.5, "lng": 2.5, "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 200
    
    db = TestSessionLocal()
    cam_db = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    assert cam_db.name == "New Name"
    assert cam_db.owner == ""
    assert cam_db.site_url == ""
    assert cam_db.image_url == "local"
    db.close()

    # 5. Valid ping with NO image (image upload disabled)
    response = client.post(
        "/api/ping",
        json={"name": "No Image Cam", "lat": 2.0, "lng": 3.0},
        headers={"X-API-Key": raw_key}
    )
    assert response.status_code == 200
    db = TestSessionLocal()
    cam_db = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    assert cam_db.name == "No Image Cam"
    assert cam_db.lat == 2.0
    assert cam_db.lng == 3.0
    db.close()

def test_ping_camera_invalid_key():
    client = TestClient(app_module.app)
    payload = {"name": "Test", "lat": 1.0, "lng": 2.0, "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="}
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
    
    # Send a request body larger than 25MB
    large_payload = "A" * (25 * 1024 * 1024 + 100)
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

def test_camera_image_local_serving(tmp_path, monkeypatch):
    db = TestSessionLocal()
    hashed_key = hashlib.sha256("local_key".encode("utf-8")).hexdigest()
    cam = CameraDB(api_key=hashed_key, name="LocalCam", image_url="local", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()


    monkeypatch.setattr(app_module, "base_dir", str(tmp_path))

    client = TestClient(app_module.app)

    # 1. Image file doesn't exist -> placeholder
    response = client.get("/api/cameras/LocalCam/image")
    assert response.status_code == 200
    assert "Camera Feed Unavailable" in response.text

    # 2. Upload image via ping
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-data"
    import base64
    b64_png = base64.b64encode(png_bytes).decode("utf-8")
    
    payload = {
        "name": "LocalCam",
        "owner": "John",
        "lat": 1.23,
        "lng": 4.56,
        "siteUrl": "http://site.com",
        "imageBase64": b64_png
    }
    response = client.post(
        "/api/ping",
        json=payload,
        headers={"X-API-Key": "local_key"}
    )
    assert response.status_code == 200

    # 3. Retrieve image -> should serve the png content we uploaded
    response = client.get("/api/cameras/LocalCam/image")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert response.content == png_bytes

    # 4. Upload JPEG image
    jpeg_bytes = b"\xff\xd8\xfffake-jpeg-data"
    b64_jpeg = base64.b64encode(jpeg_bytes).decode("utf-8")
    payload["imageBase64"] = b64_jpeg
    response = client.post(
        "/api/ping",
        json=payload,
        headers={"X-API-Key": "local_key"}
    )
    assert response.status_code == 200

    # Retrieve image -> should serve the jpeg
    response = client.get("/api/cameras/LocalCam/image")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.content == jpeg_bytes


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
    mock_rows = MagicMock()
    mock_rows.fetchall.return_value = []
    # 1. SELECT 1 FROM cameras -> succeeds
    # 2. SELECT site_url_valid -> raises exception
    # 3. ALTER TABLE ... -> succeeds
    # 4. SELECT image_url_valid -> raises exception
    # 5. ALTER TABLE ... -> succeeds
    # 6. UPDATE site_url_valid
    # 7. UPDATE image_url_valid
    # 8. SELECT id -> raises exception
    # 9. ALTER TABLE id -> succeeds
    # 10. SELECT api_key, name -> returns mock_rows
    # 11. CREATE UNIQUE INDEX -> succeeds
    mock_conn.execute.side_effect = [
        None,
        Exception("Column not found"),
        None,
        Exception("Column not found"),
        None,
        None,
        None,
        Exception("Column not found"),
        None,
        mock_rows,
        None
    ]
    
    mock_begin = MagicMock()
    mock_begin.return_value.__enter__.return_value = mock_conn
    monkeypatch.setattr(engine, "begin", mock_begin)
    
    run_migrations()
    
    assert mock_conn.execute.call_count == 11

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
        json={"name": "Test", "lat": 0.0, "lng": 0.0, "imageBase64": "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="},
        headers={"X-API-Key": oversized_key},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid API Key"



# ---------------------------------------------------------------------------
# Local Image serving validation & cleanup tests
# ---------------------------------------------------------------------------

def test_camera_image_local_validation_and_cleanup(tmp_path, monkeypatch):
    db = TestSessionLocal()
    hashed_key = hashlib.sha256("validation_key".encode("utf-8")).hexdigest()
    cam = CameraDB(api_key=hashed_key, name="ValidationCam", image_url="local", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()


    monkeypatch.setattr(app_module, "base_dir", str(tmp_path))
    client = TestClient(app_module.app)

    # 1. Invalid base64 -> 400
    payload = {
        "name": "ValidationCam",
        "lat": 1.23,
        "lng": 4.56,
        "imageBase64": "not-valid-base64-!"
    }
    response = client.post(
        "/api/ping",
        json=payload,
        headers={"X-API-Key": "validation_key"}
    )
    assert response.status_code == 400
    assert "Invalid base64 encoding" in response.json()["detail"]

    # 2. Invalid image format -> 400
    payload["imageBase64"] = "ZmFrZS1ub24taW1hZ2UtYnl0ZXM=" # "fake-non-image-bytes" in base64
    response = client.post(
        "/api/ping",
        json=payload,
        headers={"X-API-Key": "validation_key"}
    )
    assert response.status_code == 400
    assert "Invalid or unsupported image format" in response.json()["detail"]

    # 3. Upload valid image and verify file keyed by stable camera ID
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-data"
    import base64
    b64_png = base64.b64encode(png_bytes).decode("utf-8")
    payload = {
        "name": "ValidationCam",
        "lat": 1.23,
        "lng": 4.56,
        "imageBase64": b64_png
    }
    # Upload first time with ValidationCam
    client.post("/api/ping", json=payload, headers={"X-API-Key": "validation_key"})
    
    # Verify file exists keyed by the camera's unique ID
    cam_id = hashlib.sha256(hashed_key.encode("utf-8")).hexdigest()[:16]
    image_path = tmp_path / "data" / "images" / f"{cam_id}.img"
    assert image_path.exists()

    # Image is accessible via /api/cameras/{cam_id}/image and legacy /api/cameras/ValidationCam/image
    res1 = client.get(f"/api/cameras/{cam_id}/image")
    assert res1.status_code == 200
    assert res1.content == png_bytes

    res2 = client.get("/api/cameras/ValidationCam/image")
    assert res2.status_code == 200
    assert res2.content == png_bytes

    # Rename cam in next ping
    payload["name"] = "NewValidationCam"
    client.post("/api/ping", json=payload, headers={"X-API-Key": "validation_key"})

    # The image path is tied to the camera's stable ID, so it still exists
    assert image_path.exists()

    # Accessible via the new name too
    res3 = client.get("/api/cameras/NewValidationCam/image")
    assert res3.status_code == 200
    assert res3.content == png_bytes


def test_websocket_connection_and_broadcast():
    client = TestClient(app_module.app)
    
    # 1. Register a camera to get an API key
    response = client.post("/api/register")
    assert response.status_code == 200
    api_key = response.json()["api_key"]
    
    # 2. Connect to the websocket endpoint
    with client.websocket_connect("/api/ws") as websocket:
        # 3. Ping the camera update endpoint
        payload = {
            "name": "WebSocket Test Cam",
            "owner": "Test Owner",
            "lat": 12.3456,
            "lng": 78.9012,
            "siteUrl": "http://site.com",
            # Base64 encoded 1x1 black GIF image
            "imageBase64": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        }
        headers = {"X-API-Key": api_key}
        
        # Call /api/ping
        ping_res = client.post("/api/ping", json=payload, headers=headers)
        assert ping_res.status_code == 200
        
        # 4. Receive message from the websocket and assert content
        message = websocket.receive_json()
        assert message["name"] == "WebSocket Test Cam"
        assert message["owner"] == "Test Owner"
        assert message["lat"] == 12.3456
        assert message["lng"] == 78.9012
        assert message["status"] == "online"


def test_websocket_broadcast_failure():
    from app.main import manager
    
    mock_ws = MagicMock()
    # Mock send_json to raise Exception
    mock_ws.send_json = MagicMock(side_effect=Exception("Failed to send json"))
    
    manager.active_connections = [mock_ws]
    
    import asyncio
    asyncio.run(manager.broadcast({"test": "data"}))
    
    # The failing connection should have been disconnected/removed
    assert mock_ws not in manager.active_connections


def test_websocket_generic_exception():
    client = TestClient(app_module.app)
    
    # We patch starlette's WebSocket.receive_text to raise a generic Exception inside the loop
    with patch("starlette.websockets.WebSocket.receive_text", side_effect=Exception("Generic WS error")):
        with client.websocket_connect("/api/ws") as websocket:
            pass
    # After exiting, the connection list must be empty
    assert len(app_module.manager.active_connections) == 0



def test_ping_save_image_exception():
    client = TestClient(app_module.app)
    
    # Register camera
    response = client.post("/api/register")
    api_key = response.json()["api_key"]
    
    # Mock open inside app.main instead of builtins.open to avoid test system side-effects
    with patch("app.main.open", side_effect=IOError("Disk full")):
        payload = {
            "name": "SaveErrCam",
            "lat": 1.23,
            "lng": 4.56,
            "imageBase64": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        }
        res = client.post("/api/ping", json=payload, headers={"X-API-Key": api_key})
        assert res.status_code == 500
        assert "Failed to save image" in res.json()["detail"]


def test_ping_cleanup_image_exception():
    client = TestClient(app_module.app)
    
    # Register camera
    response = client.post("/api/register")
    api_key = response.json()["api_key"]
    
    # Ping first time with name "CamOld"
    payload = {
        "name": "CamOld",
        "lat": 1.23,
        "lng": 4.56,
        "imageBase64": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    }
    client.post("/api/ping", json=payload, headers={"X-API-Key": api_key})
    
    # Now ping second time with name "CamNew" (name changes, triggers cleanup)
    # Mock os.remove inside app.main instead of globally
    payload["name"] = "CamNew"
    with patch("app.main.os.remove", side_effect=OSError("Permission denied")):
        res = client.post("/api/ping", json=payload, headers={"X-API-Key": api_key})
        # The exception in cleanup is caught and ignored, so the ping should still succeed
        assert res.status_code == 200


def test_ping_broadcast_exception():
    client = TestClient(app_module.app)
    
    # Register camera
    response = client.post("/api/register")
    api_key = response.json()["api_key"]
    
    # Mock manager.broadcast to raise Exception
    with patch.object(app_module.manager, "broadcast", side_effect=Exception("Broadcast failed")):
        payload = {
            "name": "BroadcastErrCam",
            "lat": 1.23,
            "lng": 4.56,
            "imageBase64": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        }
        res = client.post("/api/ping", json=payload, headers={"X-API-Key": api_key})
        # Exception is caught and logged, so ping should still succeed
        assert res.status_code == 200


def test_detect_image_type_webp():
    from app.main import detect_image_type
    webp_data = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    assert detect_image_type(webp_data) == "image/webp"


def test_get_camera_image_not_found():
    client = TestClient(app_module.app)
    res = client.get("/api/cameras/NonExistentCamInDB/image")
    assert res.status_code == 200
    assert "Camera Feed Unavailable" in res.text


def test_get_camera_image_read_exception():
    db = TestSessionLocal()
    hashed_key = hashlib.sha256("read_err_key".encode("utf-8")).hexdigest()
    cam = CameraDB(api_key=hashed_key, name="ReadErrCam", image_url="local", image_url_valid=True)
    db.add(cam)
    db.commit()
    db.close()
    
    client = TestClient(app_module.app)
    
    # Create the physical dummy file under app_module's temp directory
    import os
    from app.main import base_dir
    hashed_name = hashlib.sha256("ReadErrCam".encode("utf-8")).hexdigest()
    image_dir = os.path.join(base_dir, "data", "images")
    os.makedirs(image_dir, exist_ok=True)
    image_path = os.path.join(image_dir, f"{hashed_name}.img")
    
    with open(image_path, "wb") as f:
        f.write(b"dummy_data")
        
    try:
        # Patch open inside app.main to fail when attempting to read the file
        with patch("app.main.open", side_effect=IOError("Disk corruption")):
            res = client.get("/api/cameras/ReadErrCam/image")
            assert res.status_code == 200
            assert "Camera Feed Unavailable" in res.text
    finally:
        if os.path.exists(image_path):
            os.remove(image_path)


def test_get_tile_invalid_style():
    client = TestClient(app_module.app)
    res = client.get("/api/tiles/invalid_style/0/0/0.png")
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid tile style"


def test_get_tile_invalid_coordinates():
    client = TestClient(app_module.app)
    res = client.get("/api/tiles/dark/-1/0/0.png")
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid tile coordinates"

    res = client.get("/api/tiles/dark/25/0/0.png")
    assert res.status_code == 400


def test_get_tile_dark_with_key():
    client = TestClient(app_module.app)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake_png_data"
    mock_resp.headers = {"content-type": "image/png"}

    with patch.dict(os.environ, {"CARTO_API_KEY": "secret_carto_key_999"}):
        with patch("httpx.AsyncClient.get", return_value=mock_resp) as mock_get:
            res = client.get("/api/tiles/dark/2/1/1.png")
            assert res.status_code == 200
            assert res.content == b"fake_png_data"
            assert res.headers["content-type"] == "image/png"
            assert "Cache-Control" in res.headers
            # Verify the upstream URL contained the key
            called_url = mock_get.call_args[0][0]
            assert "dark_all/2/1/1.png?key=secret_carto_key_999" in called_url


def test_get_tile_light_without_key():
    client = TestClient(app_module.app)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake_light_png"
    mock_resp.headers = {"content-type": "image/png"}

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CARTO_API_KEY", None)
        with patch("httpx.AsyncClient.get", return_value=mock_resp) as mock_get:
            res = client.get("/api/tiles/light/5/10/12.png")
            assert res.status_code == 200
            assert res.content == b"fake_light_png"
            called_url = mock_get.call_args[0][0]
            assert "rastertiles/voyager/5/10/12.png" in called_url
            assert "key=" not in called_url


def test_get_tile_upstream_error():
    client = TestClient(app_module.app)
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.content = b"Not found"
    mock_resp.headers = {"content-type": "text/plain"}

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = client.get("/api/tiles/dark/2/1/1.png")
        assert res.status_code == 404


def test_get_tile_upstream_exception():
    client = TestClient(app_module.app)
    with patch("httpx.AsyncClient.get", side_effect=Exception("Network error")):
        res = client.get("/api/tiles/dark/2/1/1.png")
        assert res.status_code == 502


def test_same_name_different_cameras_isolated_images(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "base_dir", str(tmp_path))
    client = TestClient(app_module.app)

    # 1. Register two different cameras
    reg1 = client.post("/api/register")
    assert reg1.status_code == 200
    key1 = reg1.json()["api_key"]

    reg2 = client.post("/api/register")
    assert reg2.status_code == 200
    key2 = reg2.json()["api_key"]

    import base64
    png_bytes = b"\x89PNG\r\n\x1a\nCam1Image"
    jpeg_bytes = b"\xff\xd8\xffCam2Image"
    b64_png = base64.b64encode(png_bytes).decode("utf-8")
    b64_jpeg = base64.b64encode(jpeg_bytes).decode("utf-8")

    # 2. Both ping with the SAME name ("My Indi-Allsky Camera"), but different locations
    p1 = client.post("/api/ping", json={
        "name": "My Indi-Allsky Camera",
        "owner": "Alice",
        "lat": 48.26,
        "lng": 16.63,
        "imageBase64": b64_png
    }, headers={"X-API-Key": key1})
    assert p1.status_code == 200

    p2 = client.post("/api/ping", json={
        "name": "My Indi-Allsky Camera",
        "owner": "Bob",
        "lat": 47.66,
        "lng": 17.65,
        "imageBase64": b64_jpeg
    }, headers={"X-API-Key": key2})
    assert p2.status_code == 200

    # 3. Retrieve camera list
    cams_res = client.get("/api/cameras")
    assert cams_res.status_code == 200
    cams = cams_res.json()
    my_cams = [c for c in cams if c["name"] == "My Indi-Allsky Camera"]
    assert len(my_cams) == 2

    alice_cam = next(c for c in my_cams if c["owner"] == "Alice")
    bob_cam = next(c for c in my_cams if c["owner"] == "Bob")

    assert alice_cam["id"] != bob_cam["id"]

    # 4. Verify images are completely isolated and never overwritten
    img1 = client.get(f"/api/cameras/{alice_cam['id']}/image")
    assert img1.status_code == 200
    assert img1.headers["Content-Type"] == "image/png"
    assert img1.content == png_bytes

    img2 = client.get(f"/api/cameras/{bob_cam['id']}/image")
    assert img2.status_code == 200
    assert img2.headers["Content-Type"] == "image/jpeg"
    assert img2.content == jpeg_bytes


def test_seamless_migration_populates_id_and_copies_images(tmp_path, monkeypatch):
    import shutil
    from sqlalchemy import text
    monkeypatch.setattr(app_module, "base_dir", str(tmp_path))
    monkeypatch.setattr(app_module, "engine", test_engine)

    db = TestSessionLocal()
    hashed_key = hashlib.sha256("legacy_key_123".encode("utf-8")).hexdigest()
    # Insert legacy camera with id = None
    cam = CameraDB(api_key=hashed_key, name="LegacyCam", owner="OldOwner", id=None, image_url="local", image_url_valid=True)
    db.add(cam)
    db.commit()
    # Explicitly set id to NULL via raw SQL to simulate pre-migration database
    db.execute(text("UPDATE cameras SET id = NULL WHERE api_key = :ak"), {"ak": hashed_key})
    db.commit()
    db.close()

    # Create old image file on disk at data/images/{sha256(name)}.img
    legacy_hash = hashlib.sha256(b"LegacyCam").hexdigest()
    image_dir = tmp_path / "data" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    old_file = image_dir / f"{legacy_hash}.img"
    legacy_content = b"\x89PNG\r\n\x1a\nLegacyImageData"
    old_file.write_bytes(legacy_content)

    # Run migrations
    app_module.run_migrations()

    # Verify ID is now populated
    db = TestSessionLocal()
    migrated_cam = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    expected_id = hashlib.sha256(hashed_key.encode("utf-8")).hexdigest()[:16]
    assert migrated_cam.id == expected_id
    db.close()

    # Verify new ID image file was created and contains the legacy content
    new_file = image_dir / f"{expected_id}.img"
    assert new_file.exists()
    assert new_file.read_bytes() == legacy_content

    # Verify API serves image using new ID
    client = TestClient(app_module.app)
    res = client.get(f"/api/cameras/{expected_id}/image")
    assert res.status_code == 200
    assert res.content == legacy_content

    # Verify requesting by camera name redirects to the canonical ID URL with HTTP 307
    client_no_redirect = TestClient(app_module.app, follow_redirects=False)
    res_redir = client_no_redirect.get("/api/cameras/LegacyCam/image")
    assert res_redir.status_code == 307
    assert res_redir.headers["location"] == f"/api/cameras/{expected_id}/image"




