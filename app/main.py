import asyncio
import hashlib
import logging
import os
import time
import uuid
import html
from datetime import datetime, timezone
from typing import List
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import engine, Base, get_db
from app.models import CameraDB
from app.schemas import CameraResponse, CameraPing
from app.tasks import reap_the_dead, check_dead_links
from app.ssrf import is_safe_url, resolve_safe_url

# Configure logging (M-4)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------
def run_migrations():
    try:
        with engine.begin() as conn:
            table_exists = False
            try:
                conn.execute(text("SELECT 1 FROM cameras LIMIT 1"))
                table_exists = True
            except Exception:
                pass

            if table_exists:
                try:
                    conn.execute(text("SELECT site_url_valid FROM cameras LIMIT 1"))
                except Exception:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN site_url_valid BOOLEAN DEFAULT 1 NOT NULL"))
                try:
                    conn.execute(text("SELECT image_url_valid FROM cameras LIMIT 1"))
                except Exception:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN image_url_valid BOOLEAN DEFAULT 1 NOT NULL"))
    except Exception as e:
        logger.exception("Database migration failed: %s", e)
        print(f"Migration error: {e}")


# ---------------------------------------------------------------------------
# In-memory rate limiter
# ---------------------------------------------------------------------------
class InMemoryRateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.requests: dict = {}
        self.last_cleanup = time.time()
        self._lock = asyncio.Lock()

    async def check(self, request: Request):
        # Prefer the real client IP forwarded by Traefik over the proxy IP
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        async with self._lock:
            if now - self.last_cleanup > 300:
                self._cleanup(now)

            window_start = now - self.window
            self.requests[client_ip] = [
                t for t in self.requests.get(client_ip, []) if t > window_start
            ]
            if len(self.requests[client_ip]) >= self.limit:
                raise HTTPException(status_code=429, detail="Too Many Requests")
            self.requests[client_ip].append(now)

    def _cleanup(self, now: float):
        cutoff = now - self.window
        self.requests = {
            ip: [t for t in times if t > cutoff]
            for ip, times in self.requests.items()
            if any(t > cutoff for t in times)
        }
        self.last_cleanup = now

register_limiter = InMemoryRateLimiter(limit=5, window=60)
ping_limiter     = InMemoryRateLimiter(limit=60, window=60)
image_limiter    = InMemoryRateLimiter(limit=30, window=60)   # new: rate-limit image proxy


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    Base.metadata.create_all(bind=engine)
    task1 = asyncio.create_task(reap_the_dead())
    task2 = asyncio.create_task(check_dead_links())
    try:
        yield
    finally:
        task1.cancel()
        task2.cancel()
        await asyncio.gather(task1, task2, return_exceptions=True)


app = FastAPI(
    title="Indi-Allsky Map Server",
    description="Centralized map server for registering and updating indi-allsky camera systems.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------

# Hard limit on request body (covers Content-Length and chunked encoding)
MAX_PAYLOAD_SIZE = 1024 * 1024  # 1 MB
MAX_API_KEY_LEN  = 200          # allsky_live_<uuid> is 48 chars; generous headroom

@app.middleware("http")
async def limit_payload_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_PAYLOAD_SIZE:
        return JSONResponse(status_code=413, content={"detail": "Request Entity Too Large"})
    # Also cap chunked-encoded bodies by buffering and re-injecting
    body = await request.body()
    if len(body) > MAX_PAYLOAD_SIZE:
        return JSONResponse(status_code=413, content={"detail": "Request Entity Too Large"})
    async def receive():
        return {"type": "http.request", "body": body}  # pragma: no cover
    request._receive = receive  # type: ignore[attr-defined]
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        # unsafe-inline retained for Tailwind browser CDN runtime injection —
        # tracked in tech debt (H1).  Remove once Tailwind is compiled offline.
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org; " # allowed map tile providers
        "connect-src 'self'; "            # tightened: API calls are same-origin only
        "frame-ancestors 'none';"         # belt-and-suspenders alongside X-Frame-Options
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
    # CORS is intentionally NOT configured here.  /api/ping is called by server-side
    # indi-allsky software (not a browser), so it needs no CORS allowance.
    # Do NOT add CORSMiddleware with allow_origins=["*"] — that would expose the
    # entire API to cross-origin browser requests.
    return response


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/api/register",
    response_model=dict,
    summary="Register a new camera",
    description="Generates a new prefixed API key, hashes it, and stores the hash. Returns the raw key once.",
)
async def register_camera(request: Request, db: Session = Depends(get_db)) -> dict:
    await register_limiter.check(request)

    raw_key    = f"allsky_live_{uuid.uuid4()}"
    hashed_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    new_entry = CameraDB(api_key=hashed_key, last_seen=datetime.now(timezone.utc))
    db.add(new_entry)
    db.commit()
    return {"api_key": raw_key}


@app.get(
    "/api/cameras",
    response_model=List[CameraResponse],
    summary="List all cameras",
    description="Retrieves all cameras that have checked in. Coordinates are fuzzed to 2 d.p.",
)
async def get_cameras(db: Session = Depends(get_db)) -> List[CameraDB]:
    return db.query(CameraDB).filter(CameraDB.name != None).all()


@app.post(
    "/api/ping",
    response_model=dict,
    summary="Update camera status",
    description="Updates camera data and marks it online. Requires a valid X-API-Key header.",
)
async def update_camera(
    data: CameraPing,
    request: Request,
    x_api_key: str = Header(..., description="The raw API key with prefix"),
    db: Session = Depends(get_db),
) -> dict:
    # Cap header length before hashing to prevent CPU-exhaustion via huge headers
    if len(x_api_key) > MAX_API_KEY_LEN:
        raise HTTPException(status_code=400, detail="Invalid API Key")

    await ping_limiter.check(request)
    hashed_key = hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()

    cam = db.query(CameraDB).filter(CameraDB.api_key == hashed_key).first()
    if not cam:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    cam.name      = data.name
    cam.owner     = data.owner
    cam.lat       = data.lat
    cam.lng       = data.lng
    cam.site_url  = data.site_url
    cam.image_url = data.image_url
    cam.last_seen = datetime.now(timezone.utc)
    cam.status    = "online"
    db.commit()
    return {"message": "Success"}


# ---------------------------------------------------------------------------
# Image Proxy — SSRF-hardened, streaming, Content-Type whitelisted
# ---------------------------------------------------------------------------


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES     = 10 * 1024 * 1024  # 10 MB

def default_placeholder_image() -> Response:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" width="640" height="480">'
        '<rect width="640" height="480" fill="#2c3e50"/>'
        '<text x="50%" y="45%" font-family="system-ui,-apple-system,sans-serif" '
        'font-size="24" font-weight="bold" fill="#ecf0f1" text-anchor="middle">'
        'Camera Feed Unavailable</text>'
        '<text x="50%" y="55%" font-family="system-ui,-apple-system,sans-serif" '
        'font-size="14" fill="#bdc3c7" text-anchor="middle">'
        'The camera image could not be loaded at this time.</text>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")


@app.get(
    "/api/cameras/{camera_name}/image",
    summary="Proxy camera image",
    description="Proxies the camera image to resolve CORS and mixed-content issues.",
)
async def get_camera_image(
    camera_name: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    await image_limiter.check(request)

    cam = db.query(CameraDB).filter(CameraDB.name == camera_name).first()
    if not cam or not cam.image_url:
        return default_placeholder_image()
    if not getattr(cam, "image_url_valid", True):
        return default_placeholder_image()

    # SSRF guard: resolve hostname asynchronously and validate IPs to prevent DNS rebinding (SEC-02)
    resolved = await resolve_safe_url(cam.image_url)
    if not resolved:
        logger.warning("SSRF blocked for camera %r: %s", camera_name, cam.image_url)
        return default_placeholder_image()

    safe_url, headers, extensions = resolved

    try:
        # Separate connect & read timeouts to prevent slow-loris attacks (ROB-02)
        timeout = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(safe_url, headers=headers, extensions=extensions, follow_redirects=False)

            # Handle one redirect hop, re-checking destination for SSRF (SEC-03)
            if res.status_code in (301, 302, 303, 307, 308):
                redirect_url = res.headers.get("location", "")
                if not redirect_url:
                    return default_placeholder_image()
                from urllib.parse import urljoin
                full_redirect_url = urljoin(cam.image_url, redirect_url)
                resolved_redir = await resolve_safe_url(full_redirect_url)
                if not resolved_redir:
                    logger.warning("SSRF blocked for camera %r on redirect: %s", camera_name, full_redirect_url)
                    return default_placeholder_image()
                safe_redir_url, redir_headers, redir_ext = resolved_redir
                res = await client.get(safe_redir_url, headers=redir_headers, extensions=redir_ext, follow_redirects=False)

            if res.status_code != 200:
                return default_placeholder_image()

            # Whitelist Content-Type to prevent XSS-via-proxy
            raw_ct = res.headers.get("content-type", "")
            content_type = raw_ct.split(";")[0].strip().lower()
            if content_type not in ALLOWED_IMAGE_TYPES:
                return default_placeholder_image()

            # Hard size cap to prevent memory exhaustion
            content = res.content
            if len(content) > MAX_IMAGE_BYTES:
                return default_placeholder_image()

            return Response(content=content, media_type=content_type)

    except Exception as exc:
        logger.warning("Image proxy failed for camera %r: %s", camera_name, exc, exc_info=True)

    return default_placeholder_image()


# ---------------------------------------------------------------------------
# SVG Status Widget
# ---------------------------------------------------------------------------

@app.get(
    "/api/cameras/{camera_name}/widget",
    summary="Get camera status widget",
    description="Returns an SVG status card for the given camera.",
)
async def get_camera_widget(camera_name: str, db: Session = Depends(get_db)) -> Response:
    cam = db.query(CameraDB).filter(CameraDB.name == camera_name).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")

    # Whitelist status to prevent DB-tampered values from leaking into SVG
    status       = cam.status if cam.status in ("online", "offline") else "offline"
    owner        = cam.owner or "Unknown Owner"
    last_seen_str = cam.last_seen.strftime("%Y-%m-%d %H:%M UTC") if cam.last_seen else "Never"

    dot_color    = "#2ecc71" if status == "online" else "#95a5a6"
    status_text  = "Online"  if status == "online" else "Offline"
    status_color = "#2ecc71" if status == "online" else "#7f8c8d"

    # Escape ALL user-supplied fields before inserting into SVG XML
    safe_camera_name = html.escape(camera_name)
    safe_owner       = html.escape(owner)
    safe_last_seen   = html.escape(last_seen_str)

    svg_content = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 80" width="300" height="80">'
        '<rect width="300" height="80" rx="10" fill="#1e1e24" stroke="#2b2b36" stroke-width="1.5"/>'
        f'<text x="15" y="30" font-family="system-ui,-apple-system,sans-serif" font-size="16" font-weight="bold" fill="#ffffff">{safe_camera_name}</text>'
        f'<text x="15" y="48" font-family="system-ui,-apple-system,sans-serif" font-size="11" fill="#a0a0b0">Owner: {safe_owner}</text>'
        f'<text x="15" y="62" font-family="system-ui,-apple-system,sans-serif" font-size="9" fill="#707080">Last Seen: {safe_last_seen}</text>'
        f'<circle cx="245" cy="40" r="5" fill="{dot_color}"/>'
        f'<text x="256" y="43" font-family="system-ui,-apple-system,sans-serif" font-size="11" font-weight="bold" fill="{status_color}">{status_text}</text>'
        "</svg>"
    )

    return Response(content=svg_content, media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Static file mount
# ---------------------------------------------------------------------------
base_dir   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(base_dir, "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
