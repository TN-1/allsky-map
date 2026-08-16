import asyncio
import logging
import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import CameraDB

logger = logging.getLogger(__name__)

# SSRF guard is imported from main to avoid circular imports
# Both tasks call is_safe_url before fetching any camera-supplied URL
from app.ssrf import is_safe_url, resolve_safe_url


async def reap_the_dead() -> None:
    while True:
        def run_reaper():
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)
            db = SessionLocal()
            updated_cameras = []
            try:
                db.query(CameraDB).filter(CameraDB.name == None, CameraDB.last_seen < cutoff).delete()
                
                dying_cams = db.query(CameraDB).filter(
                    CameraDB.name != None,
                    CameraDB.last_seen < cutoff,
                    CameraDB.status == "online"
                ).all()

                for cam in dying_cams:
                    cam.status = "offline"
                
                if dying_cams:
                    from app.schemas import CameraResponse
                    updated_cameras = [
                        CameraResponse.model_validate(c).model_dump(by_alias=True, mode="json")
                        for c in dying_cams
                    ]
                
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            logger.info("Reaper cycle complete: pruned inactive entries.")
            return updated_cameras

        try:
            cameras = await asyncio.to_thread(run_reaper)
            if cameras:
                from app.main import manager
                for cam_data in cameras:
                    await manager.broadcast(cam_data)
        except Exception as e:
            logger.exception("Error in Reaper task")

        await asyncio.sleep(3600)


async def _safe_head(client: httpx.AsyncClient, url: str, timeout: float = 5.0) -> bool:
    """
    Fetch url with SSRF guard applied on the initial URL and any redirect destination.
    Returns True if the URL is reachable (status < 400) and resolves to a public IP.
    Falls back to GET if HEAD fails or returns status >= 400.
    """
    resolved = await resolve_safe_url(url)
    if not resolved:
        return False
    safe_url, headers, extensions = resolved
    headers_dict = dict(headers)
    headers_dict["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    try:
        res = await client.head(safe_url, headers=headers_dict, extensions=extensions, follow_redirects=False, timeout=timeout)
        if res.status_code in (301, 302, 303, 307, 308):
            redirect = res.headers.get("location", "")
            if not redirect:
                return False
            from urllib.parse import urljoin
            full_redirect = urljoin(url, redirect)
            resolved_redir = await resolve_safe_url(full_redirect)
            if not resolved_redir:
                return False
            safe_redir_url, redir_headers, redir_ext = resolved_redir
            redir_headers_dict = dict(redir_headers)
            redir_headers_dict["User-Agent"] = headers_dict["User-Agent"]
            res = await client.head(safe_redir_url, headers=redir_headers_dict, extensions=redir_ext, follow_redirects=False, timeout=timeout)
        if res.status_code < 400:
            return True
    except Exception:
        pass

    # Fallback to GET if HEAD returned status >= 400 or raised exception (e.g. 405 Method Not Allowed / 403 Forbidden)
    try:
        res = await client.get(safe_url, headers=headers_dict, extensions=extensions, follow_redirects=False, timeout=timeout)
        if res.status_code in (301, 302, 303, 307, 308):
            redirect = res.headers.get("location", "")
            if not redirect:
                return False
            from urllib.parse import urljoin
            full_redirect = urljoin(url, redirect)
            resolved_redir = await resolve_safe_url(full_redirect)
            if not resolved_redir:
                return False
            safe_redir_url, redir_headers, redir_ext = resolved_redir
            redir_headers_dict = dict(redir_headers)
            redir_headers_dict["User-Agent"] = headers_dict["User-Agent"]
            res = await client.get(safe_redir_url, headers=redir_headers_dict, extensions=redir_ext, follow_redirects=False, timeout=timeout)
        return res.status_code < 400
    except Exception:
        return False


async def _safe_get_image(client: httpx.AsyncClient, url: str, timeout: float = 5.0) -> bool:
    """Same as _safe_head but also checks Content-Type contains 'image'."""
    resolved = await resolve_safe_url(url)
    if not resolved:
        return False
    safe_url, headers, extensions = resolved
    headers_dict = dict(headers)
    headers_dict["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    try:
        res = await client.head(safe_url, headers=headers_dict, extensions=extensions, follow_redirects=False, timeout=timeout)
        if res.status_code in (301, 302, 303, 307, 308):
            redirect = res.headers.get("location", "")
            if not redirect:
                return False
            from urllib.parse import urljoin
            full_redirect = urljoin(url, redirect)
            resolved_redir = await resolve_safe_url(full_redirect)
            if not resolved_redir:
                return False
            safe_redir_url, redir_headers, redir_ext = resolved_redir
            redir_headers_dict = dict(redir_headers)
            redir_headers_dict["User-Agent"] = headers_dict["User-Agent"]
            res = await client.head(safe_redir_url, headers=redir_headers_dict, extensions=redir_ext, follow_redirects=False, timeout=timeout)
        if res.status_code < 400:
            content_type = res.headers.get("content-type", "")
            return "image" in content_type or not content_type
    except Exception:
        pass

    try:
        res = await client.get(safe_url, headers=headers_dict, extensions=extensions, follow_redirects=False, timeout=timeout)
        if res.status_code in (301, 302, 303, 307, 308):
            redirect = res.headers.get("location", "")
            if not redirect:
                return False
            from urllib.parse import urljoin
            full_redirect = urljoin(url, redirect)
            resolved_redir = await resolve_safe_url(full_redirect)
            if not resolved_redir:
                return False
            safe_redir_url, redir_headers, redir_ext = resolved_redir
            redir_headers_dict = dict(redir_headers)
            redir_headers_dict["User-Agent"] = headers_dict["User-Agent"]
            res = await client.get(safe_redir_url, headers=redir_headers_dict, extensions=redir_ext, follow_redirects=False, timeout=timeout)
        content_type = res.headers.get("content-type", "")
        return res.status_code < 400 and ("image" in content_type or not content_type)
    except Exception:
        return False


async def check_dead_links() -> None:
    while True:
        def get_cameras_to_check():
            db = SessionLocal()
            try:
                cameras = db.query(CameraDB).filter(CameraDB.name != None).all()
                return [(c.api_key, c.site_url, c.image_url) for c in cameras]
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        try:
            cameras = await asyncio.to_thread(get_cameras_to_check)

            async with httpx.AsyncClient() as client:
                sem = asyncio.Semaphore(10)
                results = []

                async def check_camera(api_key: str, site_url: str | None, image_url: str | None):
                    async with sem:
                        site_valid  = await _safe_head(client, site_url)     if site_url  else True
                        image_valid = True if image_url == "local" else (await _safe_get_image(client, image_url) if image_url else True)
                        results.append((api_key, site_valid, image_valid))


                await asyncio.gather(*(check_camera(*c) for c in cameras))

            def save_results(data_list):
                db = SessionLocal()
                updated_cameras = []
                try:
                    for api_key, site_valid, image_valid in data_list:
                        cam = db.get(CameraDB, api_key)
                        if cam:
                            changed = (cam.site_url_valid != site_valid) or (cam.image_url_valid != image_valid)
                            cam.site_url_valid  = site_valid
                            cam.image_url_valid = image_valid
                            if changed:
                                from app.schemas import CameraResponse
                                updated_cameras.append(
                                    CameraResponse.model_validate(cam).model_dump(by_alias=True, mode="json")
                                )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()
                return updated_cameras

            updated = await asyncio.to_thread(save_results, results)
            if updated:
                from app.main import manager
                for cam_data in updated:
                    await manager.broadcast(cam_data)
            logger.info("Dead link checker cycle complete.")
        except Exception as e:
            logger.exception("Error in Dead Link Checker task")

        await asyncio.sleep(21600)
