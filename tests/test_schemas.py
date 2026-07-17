from app.schemas import CameraResponse, CameraPing
from pydantic import ValidationError
import pytest

def test_camera_response_schema():
    data = {
        "name": "Cam",
        "owner": "Owner",
        "lat": 1.0,
        "lng": 2.0,
        "siteUrl": "http://site.com",
        "imageUrl": "http://site.com/img.jpg",
        "status": "online",
        "lastSeen": "2026-07-09T06:00:00Z"
    }
    response = CameraResponse(**data)
    assert response.name == "Cam"
    assert response.owner == "Owner"
    assert response.lat == 1.0
    assert response.lng == 2.0
    assert response.site_url == "http://site.com"
    assert response.image_url == "http://site.com/img.jpg"
    assert response.status == "online"
    assert response.last_seen.isoformat().startswith("2026-07-09T06:00:00")


def test_camera_ping_schema_success():
    data = {
        "name": "Cam",
        "owner": "Owner",
        "lat": 10.0,
        "lng": 20.0,
        "siteUrl": "http://site.com",
        "imageBase64": "ZmFrZS1pbWFnZS1ieXRlcw=="
    }
    ping = CameraPing(**data)
    assert ping.name == "Cam"
    assert ping.site_url == "http://site.com"
    assert ping.image_base64 == "ZmFrZS1pbWFnZS1ieXRlcw=="

def test_camera_ping_schema_empty_urls():
    data = {
        "name": "Cam",
        "lat": 10.0,
        "lng": 20.0,
        "siteUrl": "",
        "imageBase64": "ZmFrZS1pbWFnZS1ieXRlcw=="
    }
    ping = CameraPing(**data)
    assert ping.site_url == ""
    assert ping.image_base64 == "ZmFrZS1pbWFnZS1ieXRlcw=="

def test_camera_ping_schema_invalid_url():
    with pytest.raises(ValidationError):
        CameraPing(name="Cam", lat=10.0, lng=20.0, siteUrl="not-a-url", imageBase64="ZmFrZS1pbWFnZS1ieXRlcw==")

