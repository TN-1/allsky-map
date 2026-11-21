# Terms of Service, Data Use Policy & Privacy Policy
**Effective Date:** November 19, 2026

**Service:** AllSky Map ([https://allsky-map.com](https://allsky-map.com))

**Operator:** Hamish West (TN-1)

**Contact & Issues:** [https://github.com/TN-1/allsky-map](https://github.com/TN-1/allsky-map) / `admin@allsky-map.com`

---

## 1. Overview & Acceptance of Terms
Welcome to AllSky Map (allsky-map.com), a community-driven platform aggregating and displaying live sky conditions from amateur and scientific AllSky camera installations worldwide.

By accessing the website, connecting a camera feed, or interacting with our APIs, you agree to be bound by these Terms of Service, Data Use Policy, and Privacy Policy. If you do not agree with these terms, please discontinue use of the platform and disconnect any automated camera uploads.

---

## 2. Camera Operator Terms & Data Ownership

### 2.1 Ownership & License Grant
* **You Retain Ownership:** Camera operators retain full copyright and ownership of all sky imagery, telemetry, and metadata uploaded from their hardware to AllSky Map.
* **License to Operate:** By configuring your camera software (such as `indi-allsky` or compatible clients) to push data to AllSky Map, you grant the operator a non-exclusive, worldwide, royalty-free license to host, store, transcode, compress, downscale, and publicly display your camera imagery and station metadata on the AllSky Map web interface.

### 2.2 Third-Party Research Sharing & Opt-Out Control
* **Community Research:** Unless you opt out, your compressed camera imagery and basic station coordinates may be included in bulk datasets made available to explicitly authorized, non-commercial community research and archival projects (listed in Section 4).
* **How to Opt Out:** Camera operators have full control over whether their feeds are included in third-party research exports. By default, feeds are opted-in to support open-sky science. You may opt out at any time via the indi-allsky map configuration page on your camera system, or by opening an issue at [https://github.com/TN-1/allsky-map](https://github.com/TN-1/allsky-map). Once opted out, your camera station imagery and metadata are strictly excluded from all bulk research archive exports.

### 2.3 Operator Responsibilities
You agree not to upload imagery depicting private residential interiors, identifiable individuals, or unlawful content, and you confirm that your camera placement complies with local privacy laws in your jurisdiction.

---

## 3. Acceptable Use, Anti-Scraping & API Access
To protect server infrastructure, keep hosting sustainable, and respect camera owners' preferences, automated data collection is strictly controlled.

### 3.1 Strictly Forbidden Activities
Unless explicitly authorized in writing with an issued API key, you must not:
1. Scrape, crawl, harvest, or poll individual camera image endpoints (`/api/cameras/{id}/image`, `/api/cameras/{id}/image/full`, or equivalent) via scripts, cron jobs, bots, or third-party desktop/mobile wrapper applications.
2. Hotlink or embed raw image URLs from allsky-map.com on external websites, dashboards, or commercial services (cross-origin hotlinking is blocked at the origin via Fetch Metadata and Referer verification).
3. Bypass, circumvent, or interfere with Cloudflare WAF rules, in-memory rate limiters (which restrict camera registration to 5 requests per minute, status pings to 60 per minute, and image proxy requests to 180 per minute), Referer checks, or authentication mechanisms.
4. Use AllSky Map data to train commercial machine-learning models or feed commercial products without express written permission.

### 3.2 Requesting Authorized API Access
We welcome collaboration with genuine astronomical, meteorological, and open-source community projects—especially those that contribute value back to the AllSky community.
* **Talk to Us First:** If you wish to access bulk imagery or telemetry for research or archival purposes, contact the operator via [https://github.com/TN-1/allsky-map/issues](https://github.com/TN-1/allsky-map/issues) or `admin@allsky-map.com` before running automated requests.
* **API Key Conditions:** Approved projects are issued a dedicated secret key passed via the `X-Bulk-Key` header, granting access to on-demand bulk ZIP archives containing compressed JPEG thumbnails (capped at 1280 pixels and quality 75) and JSON station manifests. The bulk endpoint supports geographical bounding box query parameters (`min_lat`, `max_lat`, `min_lng`, `max_lng`) to constrain bandwidth consumption. If an authorized project's bandwidth consumption requires upgrading server capacity, the operator reserves the right to pause access or request a modest infrastructure contribution to cover hosting overhead.

---

## 4. Authorized Research & Archival Projects
Only the following external projects are currently authorized to access bulk camera datasets via authenticated API endpoints (excluding all opted-out cameras):

1. **AllSky Lab** ([https://allskylab.ca](https://allskylab.ca), `jerry@allskylab.ca`) — Non-commercial research on night-time cloud detection, comparing satellite cloud products against star visibility in all-sky camera images.
2. **None currently active** — No additional third-party entities hold active authorization keys.

Any automated client attempting unauthorized bulk scraping outside these parameters is subject to immediate IP/ASN blocking at the Cloudflare edge.

---

## 5. Privacy Policy & Data Handling
AllSky Map collects only the minimum technical data required to display live sky maps and keep the service secure.

### 5.1 What Data We Collect & Store
* **Camera Station Data:** Station name (`name`), operator name or callsign (`owner`), latitude and longitude (`lat`, `lng`), external station website URL (`site_url`), operational status (`status`), and timestamp of last check-in (`last_seen`). 
* **Coordinate Precision & Privacy:** Coordinates submitted via `POST /api/ping` are stored as floating-point values; on the public Leaflet map popup, coordinates are rendered rounded to 2 decimal places (~1 km accuracy) to protect private residential locations. In bulk research archive manifests, coordinates are strictly rounded to 2 decimal places (`round(coord, 2)`).
* **Absence of Environmental Telemetry:** The platform does not collect or store ambient weather telemetry, temperature, barometric pressure, sensor humidity, camera gain, exposure parameters, or hardware serial numbers.
* **Account & Authentication Data:** AllSky Map does not offer user accounts, passwords, or personal profile registrations. Camera software authentication utilizes generated API keys (`allsky_live_<uuid>`). The server never stores plaintext API keys; only one-way cryptographic SHA-256 hashes are persisted in the database.
* **Web Visitor & Security Logs:** Standard HTTP request metadata (client IP addresses, User-Agent strings, request paths, and timestamps) processed via Cloudflare and our origin reverse proxy (Traefik v3 with Let's Encrypt TLS and Docker socket proxy isolation) for rate limiting, abuse prevention, and diagnostics. We do not sell visitor data or employ third-party advertising trackers.

### 5.2 Data Retention
* **Camera Images:** Only the single most recent image upload per camera station is stored. Images are overwritten in place with each new upload; historical sequences are not archived.
* **Station Records:** Unactivated camera keys are automatically purged after 24 hours. Active stations that cease checking in are marked offline after 24 hours, but their station profile remains visible until an explicit deletion request is made.
* **Server Logs:** Standard rolling server logs are retained temporarily for diagnostic and abuse-prevention purposes, with authentication credentials stripped.

### 5.3 Infrastructure & Third-Party Processors
AllSky Map is hosted on Onidel AU-SYD and protected by Cloudflare, Inc. for DNS resolution, global CDN caching, DDoS mitigation, and edge Web Application Firewall (WAF) enforcement.

### 5.4 Your Privacy Rights & Data Removal
Because AllSky Map operates without user account dashboards, camera operators may request the permanent deletion of their station records and stored imagery at any time by opening an administrative request at [https://github.com/TN-1/allsky-map/issues](https://github.com/TN-1/allsky-map/issues) or contacting the operator directly via `admin@allsky-map.com`.

---

## 6. Disclaimers & Limitation of Liability
* **"As-Is" Service:** AllSky Map is a community hobbyist project provided on an "as-is" and "as-available" basis without warranties of any kind, express or implied. We do not guarantee continuous uptime, image accuracy, or uninterrupted API access.
* **Limitation of Liability:** To the maximum extent permitted by law, the operator shall not be liable for any direct, indirect, incidental, or consequential damages arising out of your use of (or inability to use) the website, APIs, or hosted imagery.

---

## 7. Changes to These Terms & Governing Law
* **Updates:** We may update this policy from time to time to reflect new features, API rules, or infrastructure changes. Continued use of the service after updates constitutes acceptance of the revised terms.
* **Governing Law:** These terms are governed by and construed in accordance with the laws of South Australia, Australia, and any disputes shall be subject to the exclusive jurisdiction of the courts of South Australia.
