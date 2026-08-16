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

def test_camera_response_last_seen_serialization():
    from datetime import datetime, timezone
    # 1. Timezone naive
    dt_naive = datetime(2026, 7, 17, 6, 17)
    res_naive = CameraResponse(
        name="Cam", owner="Owner", lat=1.0, lng=2.0, site_url="http://site.com",
        image_url="local", status="online", last_seen=dt_naive
    )
    dumped_naive = res_naive.model_dump()
    assert "+00:00" in dumped_naive["last_seen"] or "Z" in dumped_naive["last_seen"]

    # 2. Timezone aware
    dt_aware = datetime(2026, 7, 17, 6, 17, tzinfo=timezone.utc)
    res_aware = CameraResponse(
        name="Cam", owner="Owner", lat=1.0, lng=2.0, site_url="http://site.com",
        image_url="local", status="online", last_seen=dt_aware
    )
    dumped_aware = res_aware.model_dump()
    assert "+00:00" in dumped_aware["last_seen"] or "Z" in dumped_aware["last_seen"]


def test_camera_response_last_seen_none():
    res = CameraResponse(
        name="Cam", owner="Owner", lat=1.0, lng=2.0, site_url="http://site.com",
        image_url="local", status="online", last_seen=None
    )
    dumped = res.model_dump()
    assert dumped["last_seen"] == ""

def test_camera_ping_site_url_auto_scheme():
    ping = CameraPing(
        name="Cam", lat=10.0, lng=20.0, siteUrl="my-allsky-site.org", imageBase64="ZmFrZS1pbWFnZS1ieXRlcw=="
    )
    assert ping.site_url == "https://my-allsky-site.org"



