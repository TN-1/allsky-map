import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
import html
from datetime import datetime, timezone
from typing import List
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, FileResponse, RedirectResponse
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

base_dir   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(base_dir, "static")


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

                # Ensure existing entries with non-empty URLs start as valid
                conn.execute(text("UPDATE cameras SET site_url_valid = 1 WHERE (site_url_valid IS NULL OR site_url_valid = 0) AND site_url IS NOT NULL AND site_url != ''"))
                conn.execute(text("UPDATE cameras SET image_url_valid = 1 WHERE (image_url_valid IS NULL OR image_url_valid = 0) AND image_url IS NOT NULL AND image_url != ''"))

                try:
                    conn.execute(text("SELECT id FROM cameras LIMIT 1"))
                except Exception:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN id VARCHAR(32)"))

                # Populate missing IDs for any existing records
                rows = conn.execute(text("SELECT api_key, name FROM cameras WHERE id IS NULL OR id = ''")).fetchall()
                image_dir = os.path.join(base_dir, "data", "images")
                for row in rows:
                    ak = row[0]
                    cname = row[1]
                    public_id = hashlib.sha256(ak.encode("utf-8")).hexdigest()[:16]
                    conn.execute(
                        text("UPDATE cameras SET id = :id WHERE api_key = :api_key"),
                        {"id": public_id, "api_key": ak}
                    )
                    # Seamlessly migrate existing images on disk to the new ID-keyed filename
                    if cname:
                        try:
                            old_hash = hashlib.sha256(cname.encode("utf-8")).hexdigest()
                            old_path = os.path.join(image_dir, f"{old_hash}.img")
                            new_path = os.path.join(image_dir, f"{public_id}.img")
                            if os.path.exists(old_path) and not os.path.exists(new_path):
                                shutil.copyfile(old_path, new_path)
                        except Exception as exc:
                            logger.warning("Failed to migrate image file for camera %s: %s", cname, exc)

                try:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_cameras_id ON cameras (id)"))
                except Exception:
                    pass
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
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
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
# WebSocket Connection Manager
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
http_client: httpx.AsyncClient | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=10.0)
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
        if http_client and not http_client.is_closed:
            await http_client.aclose()


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
MAX_PAYLOAD_SIZE = 25 * 1024 * 1024  # 25 MB
MAX_API_KEY_LEN  = 200               # allsky_live_<uuid> is 48 chars; generous headroom

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
        "connect-src 'self' ws: wss:; "   # allowed same-origin and WebSocket connections
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

@app.get(
    "/api/tiles/{style}/{z}/{x}/{y}.png",
    summary="Proxy basemap tiles",
    description="Proxies basemap tiles from CARTO using the server-side CARTO_API_KEY without exposing it to clients.",
)
async def get_tile(style: str, z: int, x: int, y: int) -> Response:
    if style not in ("dark", "light"):
        raise HTTPException(status_code=400, detail="Invalid tile style")
    if z < 0 or z > 22 or x < 0 or y < 0:
        raise HTTPException(status_code=400, detail="Invalid tile coordinates")

    carto_key = os.environ.get("CARTO_API_KEY", "")
    key_param = f"?key={carto_key}" if carto_key else ""
    sub = ("a", "b", "c", "d")[(x + y) % 4]
    
    if style == "light":
        target_url = f"https://{sub}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png{key_param}"
    else:
        target_url = f"https://{sub}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png{key_param}"

    try:
        if http_client is not None and not http_client.is_closed:
            res = await http_client.get(target_url, headers={"User-Agent": "allsky-map-server/1.0"})
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(target_url, headers={"User-Agent": "allsky-map-server/1.0"})

        if res.status_code == 200:
            return Response(
                content=res.content,
                media_type="image/png",
                headers={
                    "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
                },
            )
        return Response(status_code=res.status_code, content=res.content, media_type=res.headers.get("content-type", "text/plain"))
    except Exception as e:
        logger.warning("Failed to proxy tile %s/%s/%s/%s: %s", style, z, x, y, e)
        raise HTTPException(status_code=502, detail="Failed to fetch tile from upstream")

@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


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
    public_id  = hashlib.sha256(hashed_key.encode("utf-8")).hexdigest()[:16]

    new_entry = CameraDB(id=public_id, api_key=hashed_key, last_seen=datetime.now(timezone.utc))
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

    if not cam.id:
        cam.id = hashlib.sha256(cam.api_key.encode("utf-8")).hexdigest()[:16]

    old_name = cam.name

    cam.name      = data.name
    cam.owner     = data.owner
    cam.lat       = data.lat
    cam.lng       = data.lng
    cam.site_url  = data.site_url
    cam.site_url_valid = True

    if data.image_base64 and data.image_base64.strip():
        import base64
        try:
            decoded_image = base64.b64decode(data.image_base64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding for image")
        
        # Verify content type
        content_type = detect_image_type(decoded_image)
        if not content_type or content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid or unsupported image format")

        try:
            image_dir = os.path.join(base_dir, "data", "images")
            os.makedirs(image_dir, exist_ok=True)
            image_path = os.path.join(image_dir, f"{cam.id}.img")
            
            with open(image_path, "wb") as f:
                f.write(decoded_image)
            
            cam.image_url = "local"
            cam.image_url_valid = True
        except Exception as e:
            logger.exception("Failed to save uploaded image: %s", e)
            raise HTTPException(status_code=500, detail="Failed to save image")

    cam.last_seen = datetime.now(timezone.utc)
    cam.status    = "online"
    db.commit()
    db.refresh(cam)

    try:
        response_schema = CameraResponse.model_validate(cam)
        data_dict = response_schema.model_dump(by_alias=True, mode="json")
        await manager.broadcast(data_dict)
    except Exception as e:
        logger.exception("Failed to broadcast camera update on ping: %s", e)

    return {"message": "Success"}




# ---------------------------------------------------------------------------
# Image Proxy — SSRF-hardened, streaming, Content-Type whitelisted
# ---------------------------------------------------------------------------


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES     = 20 * 1024 * 1024  # 20 MB

def detect_image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    elif data.startswith(b"RIFF") and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None

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
    "/api/cameras/{camera_identifier}/image",
    summary="Get camera image",
    description="Serves the locally uploaded camera image by camera ID or name.",
)
async def get_camera_image(
    camera_identifier: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    await image_limiter.check(request)

    cam = db.query(CameraDB).filter(CameraDB.id == camera_identifier).first()
    if not cam:
        cam_by_name = db.query(CameraDB).filter(CameraDB.name == camera_identifier).first()
        if cam_by_name and cam_by_name.id:
            return RedirectResponse(url=f"/api/cameras/{cam_by_name.id}/image", status_code=307)

    image_dir = os.path.join(base_dir, "data", "images")
    image_path = None

    if cam:
        if cam.id:
            cand = os.path.join(image_dir, f"{cam.id}.img")
            if os.path.exists(cand):
                image_path = cand
        if not image_path and cam.name:
            hashed_name = hashlib.sha256(cam.name.encode("utf-8")).hexdigest()
            cand = os.path.join(image_dir, f"{hashed_name}.img")
            if os.path.exists(cand):
                image_path = cand
    else:
        cand_id = os.path.join(image_dir, f"{camera_identifier}.img")
        hashed_id = os.path.join(image_dir, f"{hashlib.sha256(camera_identifier.encode('utf-8')).hexdigest()}.img")
        if os.path.exists(cand_id):
            image_path = cand_id
        elif os.path.exists(hashed_id):
            image_path = hashed_id

    if image_path and os.path.exists(image_path):
        try:
            with open(image_path, "rb") as f:
                header = f.read(16)
            content_type = detect_image_type(header) or "image/jpeg"
            return FileResponse(image_path, media_type=content_type)
        except Exception as exc:
            logger.warning("Failed to read local image for camera %r: %s", camera_identifier, exc)
            return default_placeholder_image()

    return default_placeholder_image()



# ---------------------------------------------------------------------------
# SVG Status Widget
# ---------------------------------------------------------------------------

@app.get(
    "/api/cameras/{camera_identifier}/widget",
    summary="Get camera status widget",
    description="Returns an SVG status card for the given camera by ID or name.",
)
async def get_camera_widget(camera_identifier: str, db: Session = Depends(get_db)) -> Response:
    cam = db.query(CameraDB).filter(CameraDB.id == camera_identifier).first()
    if not cam:
        cam = db.query(CameraDB).filter(CameraDB.name == camera_identifier).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")

    camera_name  = cam.name or "Unknown Camera"
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
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

