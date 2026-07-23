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
function loadCameras(silent = false) {
    const refreshBtn = document.getElementById('refresh-btn');
    const refreshIcon = document.getElementById('refresh-icon');
    const refreshSpinner = document.getElementById('refresh-spinner');
    
    if (!silent && refreshBtn && refreshIcon && refreshSpinner) {
        refreshBtn.disabled = true;
        refreshIcon.classList.add('hidden');
        refreshSpinner.classList.remove('hidden');
    }
    
    // Remember which camera's popup is currently open
    const openCam = allCameras.find(cam => cam.marker && cam.marker.isPopupOpen());
    const openCamName = openCam ? openCam.name : null;
    
    return fetch('/api/cameras')
        .then(response => {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.json();
        })
        .then(data => {
            // Remove old markers from cluster group to prevent duplicate visuals/memory issues
            allCameras.forEach(cam => {
                if (cam.marker) {
                    markerCluster.removeLayer(cam.marker);
                }
            });
            
            allCameras = data.map(cam => {
                return {
                    ...cam,
                    marker: createMarker(cam)
                };
            });
            filterCameras();
            
            // Re-open the popup if it was open before
            if (openCamName) {
                const newOpenCam = allCameras.find(cam => cam.name === openCamName);
                if (newOpenCam && newOpenCam.marker) {
                    newOpenCam.marker.openPopup();
                }
            }
        })
        .catch(err => console.error('Error loading cameras from API:', err))
        .finally(() => {
            if (!silent && refreshBtn && refreshIcon && refreshSpinner) {
                // Keep the spinner visible for at least 300ms for a smoother UX
                setTimeout(() => {
                    refreshBtn.disabled = false;
                    refreshSpinner.classList.add('hidden');
                    refreshIcon.classList.remove('hidden');
                }, 300);
            }
        });
}

// Dynamically update a camera entry or add a new one from live events
function updateOrAddCamera(camData) {
    const existingIdx = allCameras.findIndex(c => c.name === camData.name);
    const marker = createMarker(camData);
    const newCamEntry = {
        ...camData,
        marker: marker
    };
    
    let popupWasOpen = false;
    if (existingIdx !== -1) {
        const oldCam = allCameras[existingIdx];
        if (oldCam.marker) {
            popupWasOpen = oldCam.marker.isPopupOpen();
            markerCluster.removeLayer(oldCam.marker);
        }
        allCameras[existingIdx] = newCamEntry;
    } else {
        allCameras.push(newCamEntry);
    }
    
    filterCameras();
    
    // If the popup was open on the old marker, open the new one immediately
    if (popupWasOpen && marker) {
        marker.openPopup();
    }
}

// WebSocket connection state
let socket = null;
let reconnectTimer = null;

function connectWebSocket() {
    if (socket) {
        try { socket.close(); } catch(_) {}
        socket = null;
    }
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws`;
    
    socket = new WebSocket(wsUrl);
    
    socket.onmessage = (event) => {
        try {
            const camData = JSON.parse(event.data);
            if (camData && camData.name) {
                updateOrAddCamera(camData);
            }
        } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
        }
    };
    
    socket.onclose = (event) => {
        if (currentRefreshMode === 'ws') {
            reconnectTimer = setTimeout(connectWebSocket, 5000);
        }
    };
    
    socket.onerror = (err) => {
        console.error('WebSocket error:', err);
    };
}

function disconnectWebSocket() {
    if (socket) {
        try { socket.close(); } catch(_) {}
        socket = null;
    }
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
}

// Current selected refresh mode state
let currentRefreshMode = 'ws';

function setRefreshMode(mode) {
    currentRefreshMode = mode;
    localStorage.setItem('refresh-mode', mode);

    // Update active class and checkmarks on dropdown buttons
    const opts = document.querySelectorAll('.refresh-opt');
    opts.forEach(opt => {
        const check = opt.querySelector('.check-mark');
        if (opt.getAttribute('data-value') === mode) {
            opt.classList.add('active-mode');
            if (check) {
                check.classList.remove('opacity-0');
                check.classList.add('opacity-100');
            }
        } else {
            opt.classList.remove('active-mode');
            if (check) {
                check.classList.remove('opacity-100');
                check.classList.add('opacity-0');
            }
        }
    });

    // Update the button label & pulsing red dot
    const labelEl = document.getElementById('refresh-label');
    const indicatorEl = document.getElementById('live-indicator');
    
    if (labelEl) {
        if (mode === 'ws') {
            labelEl.textContent = 'Live';
        } else if (mode === '60') {
            labelEl.textContent = '1 Min';
        } else if (mode === '300') {
            labelEl.textContent = '5 Mins';
        } else if (mode === 'manual') {
            labelEl.textContent = 'Manual';
        }
    }

    if (indicatorEl) {
        if (mode === 'ws') {
            indicatorEl.classList.remove('hidden');
            indicatorEl.classList.add('flex');
        } else {
            indicatorEl.classList.remove('flex');
            indicatorEl.classList.add('hidden');
        }
    }

    // Close the dropdown details element
    const dropdown = document.getElementById('refresh-dropdown');
    if (dropdown) {
        dropdown.removeAttribute('open');
    }

    // Trigger update strategy
    updateRefreshStrategy();
}

// Timer for active polling interval fallback
let refreshIntervalTimer = null;

function updateRefreshStrategy() {
    if (refreshIntervalTimer) {
        clearInterval(refreshIntervalTimer);
        refreshIntervalTimer = null;
    }
    
    const mode = currentRefreshMode;
    
    if (mode === 'ws') {
        connectWebSocket();
    } else {
        disconnectWebSocket();
        if (mode !== 'manual') {
            const intervalSecs = parseInt(mode, 10);
            if (!isNaN(intervalSecs)) {
                refreshIntervalTimer = setInterval(() => {
                    if (!document.hidden) {
                        loadCameras(true);
                    }
                }, intervalSecs * 1000);
            }
        }
    }
}

// Bind DOM Event Listeners cleanly (separating structure from behavior)
document.addEventListener('DOMContentLoaded', () => {
    // Initialize refresh mode control value from localStorage or default to WebSockets
    const savedMode = localStorage.getItem('refresh-mode') || 'ws';
    setRefreshMode(savedMode);

    // Bind dropdown option click events
    const opts = document.querySelectorAll('.refresh-opt');
    opts.forEach(opt => {
        opt.addEventListener('click', (e) => {
            const val = e.target.getAttribute('data-value');
            setRefreshMode(val);
        });
    });
    
    loadCameras();

    // Bind manual refresh button click event
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadCameras(false);
        });
    }

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