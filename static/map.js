// Set default view to the world with a zoom level of 2
const map = L.map('map').setView([0, 0], 2);

// Add map tiles
const tiles = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener noreferrer">CARTO</a>'
    }).addTo(map);

// Create the terminator layer
const nightOverlay = L.terminator({
    fillColor: '#000',
    fillOpacity: 0.5,
    color: '#000',
    weight: 0
}).addTo(map);

// Create the single unified Marker Cluster Group
const markerCluster = L.markerClusterGroup({
    showCoverageOnHover: false,
    maxClusterRadius: 40
}).addTo(map);

// Create dummy Layer Groups for toggles
const onlineLayer = L.layerGroup().addTo(map);
const offlineLayer = L.layerGroup().addTo(map);

// Define the overlay object for the control UI
const overlayMaps = {
    "Online Cameras": onlineLayer,
    "Offline Cameras": offlineLayer,
    "Day/Night Cycle": nightOverlay
};

// Trigger re-filtering when overlays are toggled
map.on('overlayadd overlayremove', function() {
    filterCameras();
});

// Global camera storage for live filtering
let allCameras = [];

// Relative time formatter helper
function formatRelativeTime(isoStr) {
    if (!isoStr) return "Never";
    try {
        const date = new Date(isoStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        if (diffMins < 1) return "Just now";
        if (diffMins < 60) return `${diffMins}m ago`;
        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return `${diffHours}h ago`;
        const diffDays = Math.floor(diffHours / 24);
        return `${diffDays}d ago`;
    } catch (e) {
        return "Unknown";
    }
}

// ---------------------------------------------------------------------------
// XSS-safe DOM helpers — never pass untrusted strings through innerHTML
// ---------------------------------------------------------------------------

/** Create an element with optional class, textContent, style, and attributes */
function el(tag, opts = {}) {
    const node = document.createElement(tag);
    if (opts.cls)              node.className    = opts.cls;
    if (opts.text !== undefined) node.textContent = opts.text;
    if (opts.style)            node.style.cssText = opts.style;
    if (opts.attrs) {
        for (const [k, v] of Object.entries(opts.attrs)) {
            node.setAttribute(k, v);
        }
    }
    return node;
}

/** Only allow http:// and https:// in href attributes — strips javascript: and data: URIs */
function safeHref(url) {
    try {
        const parsed = new URL(url);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return url;
    } catch (_) {}
    return '#';
}

// Define a function to create markers with different colors based on status
function createMarker(data) {
    const isOnline    = data.status === 'online';
    const badgeText   = isOnline ? 'Online' : 'Offline';
    const badgeClass  = isOnline ? 'online' : 'offline';
    const color       = isOnline ? '#2ecc71' : '#95a5a6';
    const lastSeenStr = formatRelativeTime(data.lastSeen);

    const icon = L.divIcon({
        className: 'custom-marker-icon',
        html: `<div style="background-color:${color};width:12px;height:12px;border-radius:50%;border:2px solid white;box-shadow:0 0 4px rgba(0,0,0,0.5);"></div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8]
    });

    const marker = L.marker([data.lat, data.lng], { icon });

    // -----------------------------------------------------------------------
    // Build popup with DOM methods — NO template literal → innerHTML injection
    // -----------------------------------------------------------------------
    const container = el('div', { cls: 'allsky-popup text-base-content' });

    container.appendChild(el('div', { cls: `popup-badge ${badgeClass}`, text: badgeText }));

    container.appendChild(el('div', {
        text: data.name,
        style: 'font-size:15px;font-weight:bold;margin-bottom:2px;'
    }));

    container.appendChild(el('div', {
        text: `Owner: ${data.owner || 'Unknown'}`,
        style: 'font-size:12px;opacity:0.8;margin-bottom:5px;'
    }));

    container.appendChild(el('div', {
        text: `Coords: ${data.lat.toFixed(2)}, ${data.lng.toFixed(2)}`,
        style: 'font-size:11px;opacity:0.7;'
    }));

    container.appendChild(el('div', { cls: 'popup-last-seen', text: `Last Seen: ${lastSeenStr}` }));

    const hr = document.createElement('hr');
    hr.style.cssText = 'border:0;border-top:1px solid rgba(255,255,255,0.1);margin:8px 0;';
    container.appendChild(hr);

    // Image — URL is our own proxy endpoint built from camera name, not raw user data
    if (data.imageUrl) {
        const proxyImgUrl = `/api/cameras/${encodeURIComponent(data.name)}/image`;

        const link = el('a', {
            cls: 'allsky-popup-img-link',
            attrs: {
                href: proxyImgUrl,
                target: '_blank',
                rel: 'noopener noreferrer',
                'aria-label': `View full size image for ${data.name}`
            }
        });

        const img = el('img', {
            cls: 'allsky-popup-img',
            attrs: { src: proxyImgUrl, alt: `${data.name} latest image` }
        });

        // Safe error handler — no inline onerror= attribute
        img.addEventListener('error', () => {
            img.replaceWith(el('div', { cls: 'allsky-img-err', text: 'Image Feed Offline' }));
        });

        link.appendChild(img);
        container.appendChild(link);
    }

    // Site URL — validated to prevent javascript: and data: URIs
    if (data.siteUrl) {
        container.appendChild(el('a', {
            text: 'View Camera Website',
            style: 'display:block;margin-top:10px;text-align:center;text-decoration:none;color:#2ecc71;font-weight:bold;transition:color 0.3s ease;',
            attrs: { href: safeHref(data.siteUrl), target: '_blank', rel: 'noopener noreferrer' }
        }));
    }

    marker.bindPopup(container, { autoPanPadding: [20, 80] });
    return marker;
}

// Live client-side search filtering (optimized: re-uses cached marker objects)
function filterCameras() {
    const queryEl = document.getElementById('search-input');
    if (!queryEl) return;
    const query = queryEl.value.toLowerCase().trim();
    
    // Clear the cluster group
    markerCluster.clearLayers();
    
    // Check which overlay layers are currently enabled
    const showOnline = map.hasLayer(onlineLayer);
    const showOffline = map.hasLayer(offlineLayer);
    
    allCameras.forEach(cam => {
        // Filter out cameras based on toggled layers
        if (cam.status === 'online' && !showOnline) return;
        if (cam.status === 'offline' && !showOffline) return;
        
        const nameMatch = cam.name ? cam.name.toLowerCase().includes(query) : false;
        const ownerMatch = cam.owner ? cam.owner.toLowerCase().includes(query) : false;
        const statusMatch = cam.status ? cam.status.toLowerCase().includes(query) : false;
        
        if (nameMatch || ownerMatch || statusMatch) {
            if (cam.marker) {
                markerCluster.addLayer(cam.marker);
            }
        }
    });
}

// Fetch camera data from the API and instantiate markers
function loadCameras() {
    fetch('/api/cameras')
        .then(response => {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.json();
        })
        .then(data => {
            // Instantiate markers once during data load to cache and optimize filtering
            allCameras = data.map(cam => {
                return {
                    ...cam,
                    marker: createMarker(cam)
                };
            });
            filterCameras();
        })
        .catch(err => console.error('Error loading cameras from API:', err));
}

// Bind DOM Event Listeners cleanly (separating structure from behavior)
document.addEventListener('DOMContentLoaded', () => {
    // Initial load of cameras when the DOM is ready
    loadCameras();

    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        // Debounce search updates to prevent excessive calculations during active typing
        let debounceTimer;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(filterCameras, 150);
        });
    }

    const aboutBtn = document.getElementById('about-btn');
    if (aboutBtn) {
        aboutBtn.addEventListener('click', () => {
            const modal = document.getElementById('aboutModal');
            if (modal) modal.showModal(); // Opens native HTML5 dialog modal
        });
    }
});

// Create our update tasks
// Make it update every minute so terminator moves in real-time
setInterval(function() {
    nightOverlay.setTime();
}, 60000);
// Auto-refresh the map every 5 minutes (L-7 Visibility API)
setInterval(() => {
    if (!document.hidden) {
        loadCameras();
    }
}, 300000);

// Add the control box to the map at the bottom right to prevent overlapping with top-right search box
L.control.layers(null, overlayMaps, { collapsed: false, position: 'bottomright' }).addTo(map);

// Theme toggle tile layer switcher & persistence (M-3, L-5)
const themeToggle = document.querySelector('.theme-controller');
if (themeToggle) {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'light') {
        themeToggle.checked = true;
        document.documentElement.setAttribute('data-theme', 'light');
        tiles.setUrl('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png');
    } else {
        themeToggle.checked = false;
        document.documentElement.setAttribute('data-theme', 'dark');
        tiles.setUrl('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png');
    }

    themeToggle.addEventListener('change', (e) => {
        const isLight = e.target.checked;
        if (isLight) {
            localStorage.setItem('theme', 'light');
            document.documentElement.setAttribute('data-theme', 'light');
            tiles.setUrl('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png');
        } else {
            localStorage.setItem('theme', 'dark');
            document.documentElement.setAttribute('data-theme', 'dark');
            tiles.setUrl('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png');
        }
    });
}