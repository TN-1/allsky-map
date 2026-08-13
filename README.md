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
ACME_EMAIL=admin@example.com
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
  "imageBase64": "<base64-encoded-image>"
}
```

### `GET /api/cameras`
Returns all cameras that have pinged at least once.

```json
[
  {
    "name": "My Allsky Camera",
    "owner": "Jane Smith",
    "lat": -34.92,
    "lng": 138.6,
    "siteUrl": "https://my-camera-website.com",
    "imageUrl": "",
    "status": "online",
    "lastSeen": "2025-01-01T00:00:00+00:00"
  }
]
```

### `GET /api/cameras/{name}/image`
Returns the proxied live image for a camera (JPEG).

### `GET /api/cameras/{name}/widget`
Returns an embeddable HTML status card for a camera.

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
