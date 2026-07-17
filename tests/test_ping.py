import sys
import os
import json
import io
import urllib.request
import urllib.error
import builtins
from unittest.mock import patch, MagicMock, mock_open
import pytest

# Load the extensionless/hyphenated script dynamically as a module
from importlib.machinery import SourceFileLoader
import importlib.util
script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../services/allsky-map-ping"))
loader = SourceFileLoader("allsky_map_ping", script_path)
spec = importlib.util.spec_from_file_location("allsky_map_ping", script_path, loader=loader)
ping_client = importlib.util.module_from_spec(spec)
sys.modules["allsky_map_ping"] = ping_client
spec.loader.exec_module(ping_client)

@pytest.fixture(autouse=True)
def mock_image_setup(monkeypatch):
    monkeypatch.setenv("CAMERA_IMAGE_PATH", "/fake/latest.jpg")
    orig_exists = os.path.exists
    def mock_exists(path):
        if "latest.jpg" in str(path):
            return True
        if "flask.json" in str(path):
            return False
        return orig_exists(path)
    monkeypatch.setattr(os.path, "exists", mock_exists)

    orig_open = builtins.open
    def mock_open_fn(file, mode="r", *args, **kwargs):
        if "latest.jpg" in str(file):
            return io.BytesIO(b"\xff\xd8\xfffake-image-bytes")
        return orig_open(file, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", mock_open_fn)

def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("API_URL", "http://env-url")
    monkeypatch.setenv("API_KEY", "env-key")
    monkeypatch.setenv("CAMERA_NAME", "env-name")
    
    # Make sure we don't pick up actual local files
    monkeypatch.setattr(os.path, "exists", lambda p: True if "latest.jpg" in str(p) else False)
    
    config = ping_client.load_config()
    assert config["API_URL"] == "http://env-url"
    assert config["API_KEY"] == "env-key"
    assert config["CAMERA_NAME"] == "env-name"

def test_load_config_from_file(monkeypatch):
    # Clear environment variables
    for var in ["API_URL", "API_KEY", "CAMERA_NAME", "CAMERA_OWNER", "CAMERA_LAT", "CAMERA_LNG", "CAMERA_SITE_URL", "CAMERA_IMAGE_PATH"]:
        monkeypatch.delenv(var, raising=False)
        
    config_content = """
    # Comments should be skipped
    
    API_URL = http://file-url
    API_KEY = "file-key"
    CAMERA_NAME = 'file-name'
    CAMERA_OWNER = file-owner
    CAMERA_LAT = 12.34
    CAMERA_LNG = 56.78
    CAMERA_SITE_URL = http://site
    CAMERA_IMAGE_PATH = /fake/latest.jpg
    INVALID_LINE_WITHOUT_EQUALS
    """
    
    # We mock os.path.exists to return True only for the first matched path, say "/etc/allsky-map/ping.conf"
    def mock_exists(path):
        return path == "/etc/allsky-map/ping.conf" or "latest.jpg" in str(path)
        
    monkeypatch.setattr(os.path, "exists", mock_exists)
    
    with patch("builtins.open", mock_open(read_data=config_content)):
        config = ping_client.load_config()
        
    assert config["API_URL"] == "http://file-url"
    assert config["API_KEY"] == "file-key"
    assert config["CAMERA_NAME"] == "file-name"
    assert config["CAMERA_OWNER"] == "file-owner"
    assert config["CAMERA_LAT"] == "12.34"
    assert config["CAMERA_LNG"] == "56.78"
    assert config["CAMERA_SITE_URL"] == "http://site"
    assert config["CAMERA_IMAGE_PATH"] == "/fake/latest.jpg"

def test_load_config_env_priority(monkeypatch):
    monkeypatch.setenv("API_URL", "http://env-url")
    for var in ["API_KEY", "CAMERA_NAME"]:
        monkeypatch.delenv(var, raising=False)
        
    config_content = """
    API_URL = http://file-url
    API_KEY = file-key
    CAMERA_NAME = file-name
    """
    
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/etc/allsky-map/ping.conf" or "latest.jpg" in str(p))
    
    with patch("builtins.open", mock_open(read_data=config_content)):
        config = ping_client.load_config()
        
    assert config["API_URL"] == "http://env-url"  # Env takes priority
    assert config["API_KEY"] == "file-key"        # File fallback
    assert config["CAMERA_NAME"] == "file-name"  # File fallback

def test_load_config_file_exception(monkeypatch, capsys):
    monkeypatch.setattr(os.path, "exists", lambda p: p == "./ping.conf" or "latest.jpg" in str(p))
    
    # Mock open to raise an exception
    mock_open_error = MagicMock(side_effect=PermissionError("Permission denied"))
    
    with patch("builtins.open", mock_open_error):
        config = ping_client.load_config()
        
    captured = capsys.readouterr()
    assert "Warning: Failed to read config file ./ping.conf: Permission denied" in captured.err

def test_main_missing_config(monkeypatch, capsys):
    monkeypatch.setattr(ping_client, "load_config", lambda: {})
    
    with pytest.raises(SystemExit) as excinfo:
        ping_client.main()
        
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Invalid configuration:" in captured.err
    assert "  - API_URL is missing" in captured.err
    assert "  - API_KEY is missing" in captured.err
    assert "  - CAMERA_NAME is missing" in captured.err


def test_main_success(monkeypatch, capsys):
    config = {
        "API_URL": "http://test-api/ping",
        "API_KEY": "test-key",
        "CAMERA_NAME": "Test Camera",
        "CAMERA_OWNER": "John",
        "CAMERA_LAT": "12.34",
        "CAMERA_LNG": "56.78",
        "CAMERA_SITE_URL": "http://site",
        "CAMERA_IMAGE_PATH": "/fake/latest.jpg"
    }
    monkeypatch.setattr(ping_client, "load_config", lambda: config)
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"message": "Success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        ping_client.main()
        
        # Verify request parameters
        assert mock_urlopen.call_count == 1
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://test-api/ping"
        assert req.get_header("X-api-key") == "test-key"
        
        sent_payload = json.loads(req.data.decode("utf-8"))
        assert sent_payload["name"] == "Test Camera"
        assert sent_payload["owner"] == "John"
        assert sent_payload["lat"] == 12.34
        assert sent_payload["lng"] == 56.78
        assert sent_payload["siteUrl"] == "http://site"
        assert "imageUrl" not in sent_payload
        assert sent_payload["imageBase64"] == "/9j/ZmFrZS1pbWFnZS1ieXRlcw=="


def test_main_invalid_lat_lng(monkeypatch, capsys):
    config = {
        "API_URL": "http://test-api/ping",
        "API_KEY": "test-key",
        "CAMERA_NAME": "Test Camera",
        "CAMERA_LAT": "invalid_lat",
        "CAMERA_LNG": "invalid_lng",
        "CAMERA_IMAGE_PATH": "/fake/latest.jpg"
    }
    monkeypatch.setattr(ping_client, "load_config", lambda: config)
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"message": "Success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        ping_client.main()
        
        # Verify default 0.0 was sent
        req = mock_urlopen.call_args[0][0]
        sent_payload = json.loads(req.data.decode("utf-8"))
        assert sent_payload["lat"] == 0.0
        assert sent_payload["lng"] == 0.0
        
        captured = capsys.readouterr()
        assert "Warning: Invalid CAMERA_LAT 'invalid_lat', using 0.0" in captured.err
        assert "Warning: Invalid CAMERA_LNG 'invalid_lng', using 0.0" in captured.err

def test_main_missing_lat_lng(monkeypatch, capsys):
    config = {
        "API_URL": "http://test-api/ping",
        "API_KEY": "test-key",
        "CAMERA_NAME": "Test Camera",
        "CAMERA_IMAGE_PATH": "/fake/latest.jpg"
    }
    monkeypatch.setattr(ping_client, "load_config", lambda: config)
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"message": "Success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        ping_client.main()
        
        # Verify default 0.0 was sent
        req = mock_urlopen.call_args[0][0]
        sent_payload = json.loads(req.data.decode("utf-8"))
        assert sent_payload["lat"] == 0.0
        assert sent_payload["lng"] == 0.0

def test_main_http_error(monkeypatch, capsys):
    config = {
        "API_URL": "http://test-api/ping",
        "API_KEY": "test-key",
        "CAMERA_NAME": "Test Camera",
        "CAMERA_IMAGE_PATH": "/fake/latest.jpg"
    }
    monkeypatch.setattr(ping_client, "load_config", lambda: config)
    
    err_fp = io.BytesIO(b"Unauthorized API Key")
    http_error = urllib.error.HTTPError("http://test-api/ping", 401, "Unauthorized", {}, err_fp)
    
    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(SystemExit) as excinfo:
            ping_client.main()
            
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "HTTP Error 401: Unauthorized API Key" in captured.err

def test_main_url_error(monkeypatch, capsys):
    config = {
        "API_URL": "http://test-api/ping",
        "API_KEY": "test-key",
        "CAMERA_NAME": "Test Camera",
        "CAMERA_IMAGE_PATH": "/fake/latest.jpg"
    }
    monkeypatch.setattr(ping_client, "load_config", lambda: config)
    
    url_error = urllib.error.URLError("Connection refused")
    
    with patch("urllib.request.urlopen", side_effect=url_error):
        with pytest.raises(SystemExit) as excinfo:
            ping_client.main()
            
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Network Warning: Offline or unreachable (Connection refused)" in captured.err

def test_main_unexpected_error(monkeypatch, capsys):
    config = {
        "API_URL": "http://test-api/ping",
        "API_KEY": "test-key",
        "CAMERA_NAME": "Test Camera",
        "CAMERA_IMAGE_PATH": "/fake/latest.jpg"
    }
    monkeypatch.setattr(ping_client, "load_config", lambda: config)
    
    unexpected_err = RuntimeError("Something went wrong")
    
    with patch("urllib.request.urlopen", side_effect=unexpected_err):
        with pytest.raises(SystemExit) as excinfo:
            ping_client.main()
            
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Unexpected Error: Something went wrong" in captured.err

def test_ping_main_block(monkeypatch):
    import runpy
    
    # Set env vars so load_config succeeds without files
    monkeypatch.setenv("API_URL", "http://test")
    monkeypatch.setenv("API_KEY", "test")
    monkeypatch.setenv("CAMERA_NAME", "test")
    monkeypatch.setattr(os.path, "exists", lambda p: True if "latest.jpg" in str(p) else False)
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"message": "Success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        # Run the file as main
        runpy.run_path(os.path.abspath(os.path.join(os.path.dirname(__file__), "../services/allsky-map-ping")), run_name="__main__")
        
        assert mock_urlopen.call_count == 1

