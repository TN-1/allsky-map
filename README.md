# Allsky Map

A centralized, self-hostable map for [indi-allsky](https://github.com/aaronwmorris/indi-allsky) all-sky camera systems. Camera owners register their systems and periodically "ping" this server with their status and metadata, which is then displayed on an interactive [Leaflet](https://leafletjs.com/) map.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

---

## Features

- 🗺️ **Interactive Leaflet map** showing all registered cameras worldwide
- 🟢 **Live online/offline status** — cameras that haven't pinged in 24h are marked offline
- 📷 **Live image thumbnails** — served via a backend proxy to avoid CORS/mixed-content issues
- 🔒 **Privacy-first** — coordinates are fuzzed to 2 decimal places (~1 km) before being stored or served publicly
- 🔑 **Hashed API keys** — raw keys are never stored; only SHA-256 hashes are saved in the database
- 🛡️ **Hardened backend** — rate limiting, SSRF protection, payload size limits, and strict CSP headers
- 🐳 **Docker-ready** — ships with a full `docker-compose.yml` using Traefik for TLS termination

---

## Architecture

```
indi-allsky camera host  ──POST /api/ping──►  FastAPI server  ──►  SQLite DB
                                                   │
                                        GET /api/cameras
                                                   │
                                            Leaflet frontend
```

---

## Quick Start (Docker)

### 1. Clone the repository

```bash
git clone https://github.com/your-username/allsky-map.git
cd allsky-map
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your values:

```env
DOMAIN_NAME=allsky-map.com
ACME_EMAIL=admin@allsky-map.com
TZ=Australia/Adelaide
```

### 3. Start the stack

```bash
docker compose up -d
```

Traefik will automatically obtain a Let's Encrypt TLS certificate for your domain.

---

## Running Locally (Development)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The server will start at `http://localhost:8000`.

---

## API Reference

### `POST /api/register`
Generates a new API key. Returns the raw key **once** — store it immediately.

```json
{ "api_key": "allsky_live_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" }
```

### `POST /api/ping`
Updates a camera's data and marks it online. Called periodically by `indi-allsky`.

**Headers:** `X-API-Key: <your-api-key>`

**Body:**
```json
{
  "name": "My Allsky Camera",
  "owner": "Jane Smith",
  "lat": -34.92,
  "lng": 138.60,
  "siteUrl": "https://my-camera-website.com",
  "imageBase64": "<base64-encoded-image>",
  "allowResearch": true
}
```

* `allowResearch` *(optional, default: `true`)*: Set to `false` if the operator wishes to opt out of bulk research data harvesting.

### `GET /api/cameras`
Returns all cameras that have pinged at least once.

```json
[
  {
    "id": "a1b2c3d4e5f67890",
    "name": "My Allsky Camera",
    "owner": "Jane Smith",
    "lat": -34.92,
    "lng": 138.6,
    "siteUrl": "https://my-camera-website.com",
    "imageUrl": "local",
    "status": "online",
    "lastSeen": "2025-01-01T00:00:00+00:00",
    "allowResearch": true
  }
]
```

### `GET /api/cameras/{id_or_name}/image`
Returns the proxied image thumbnail (JPEG, max 1280px) for a camera.

### `GET /api/cameras/{id_or_name}/image/full`
Returns the uncompressed original full-resolution image as uploaded by the camera.

### `GET /api/cameras/{name}/widget`
Returns an embeddable HTML status card for a camera.

### `GET /api/bulk/images.zip`
Generates an in-memory ZIP archive of camera thumbnail images and a `manifest.json` metadata summary. Only cameras that have opted in (`allowResearch = true`) are included.

> **Requesting Access:** Access to this endpoint is restricted to preserve server bandwidth and protect community resources. If you are conducting astronomical or atmospheric research and wish to request a bulk API key (`X-Bulk-Key`), please open an issue on the [GitHub repository](https://github.com/TN-1/allsky-map/issues) detailing your project requirements.

**Headers:** `X-Bulk-Key: <YOUR_BULK_KEY>`

**Query Parameters (Optional):**
- `night_only` (boolean, default: `false`): When `true`, filters cameras to only those currently in nighttime, calculated using solar elevation ($\text{altitude} < 0^\circ$) matching the map terminator overlay.
- `min_lat` (float, -90.0 to 90.0): Southern boundary
- `max_lat` (float, -90.0 to 90.0): Northern boundary
- `min_lng` (float, -180.0 to 180.0): Western boundary
- `max_lng` (float, -180.0 to 180.0): Eastern boundary

**Example Requests:**

*Download entire global archive:*
```bash
curl -sS -H "X-Bulk-Key: <BULK_API_KEY>" \
  "https://allsky-map.com/api/bulk/images.zip" \
  -o allsky-images.zip
```

*Download nighttime-only cameras globally:*
```bash
curl -sS -H "X-Bulk-Key: <BULK_API_KEY>" \
  "https://allsky-map.com/api/bulk/images.zip?night_only=true" \
  -o allsky-images-night.zip
```

*Download regional archive (e.g., Australia):*
```bash
curl -sS -H "X-Bulk-Key: <BULK_API_KEY>" \
  "https://allsky-map.com/api/bulk/images.zip?min_lat=-44.0&max_lat=-10.0&min_lng=112.0&max_lng=154.0" \
  -o allsky-images-aus.zip
```

*Download regional archive (e.g., Continental United States):*
```bash
curl -sS -H "X-Bulk-Key: <BULK_API_KEY>" \
  "https://allsky-map.com/api/bulk/images.zip?min_lat=24.0&max_lat=50.0&min_lng=-125.0&max_lng=-66.0" \
  -o allsky-images-usa.zip
```

---

## Background Tasks

The server runs two background tasks on an hourly schedule:

| Task | Behaviour |
| :--- | :--- |
| **Reaper** | Marks cameras as `offline` if they haven't pinged in over 24 hours |
| **Pruner** | Deletes API key registrations that were never activated (no name set) after 24 hours |
| **Link checker** | Validates `siteUrl` for all cameras; hides broken links from the public API |

---

## Security Notes

- API keys are **SHA-256 hashed** before storage — the server never holds raw keys
- Coordinates are **rounded to 2 d.p.** (~1 km accuracy) before being stored or returned
- Outbound HTTP requests (image fetching) are validated against an **SSRF blocklist** (no private IPs, localhost, or link-local ranges)
- The Traefik reverse proxy **drops the `X-API-Key` header** from access logs
- Rate limiting is enforced on `/api/register` and `/api/ping`

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE) for details.

This means: if you run a modified version of this software as a publicly accessible network service, you **must** make your modified source code available to your users.
