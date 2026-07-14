#!/usr/bin/env bash
# ==============================================================================
# install.sh — Indi-Allsky Map Ping Client Installer
#
# Installs the allsky-map-ping client script, config file, and systemd
# service/timer on the local machine (the camera host).
#
# Usage:
#   sudo bash install.sh            # interactive: prompts for all values
#   sudo bash install.sh --uninstall
# ==============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_NAME="allsky-map-ping"
INSTALL_BIN="/usr/local/bin/${SCRIPT_NAME}"
CONF_DIR="/etc/allsky-map"
CONF_FILE="${CONF_DIR}/ping.conf"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_FILE="${SYSTEMD_DIR}/${SCRIPT_NAME}.service"
TIMER_FILE="${SYSTEMD_DIR}/${SCRIPT_NAME}.timer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

require_root() {
    [[ "${EUID}" -eq 0 ]] || die "This script must be run as root (use: sudo bash $0)"
}

require_file() {
    local file="$1"
    [[ -f "${file}" ]] || die "Required file not found: ${file}\nRun this script from the services/ directory."
}

ask() {
    # ask VAR "Prompt text" ["default"]
    local var="$1" prompt="$2" default="${3:-}"
    local answer
    if [[ -n "${default}" ]]; then
        read -rp "$(echo -e "${BOLD}${prompt}${NC} [${default}]: ")" answer
        printf -v "${var}" '%s' "${answer:-${default}}"
    else
        while true; do
            read -rp "$(echo -e "${BOLD}${prompt}${NC}: ")" answer
            [[ -n "${answer}" ]] && break
            warn "This field is required."
        done
        printf -v "${var}" '%s' "${answer}"
    fi
}

ask_optional() {
    local var="$1" prompt="$2" default="${3:-}"
    read -rp "$(echo -e "${BOLD}${prompt}${NC} [${default:-(leave blank)}]: ")" answer
    printf -v "${var}" '%s' "${answer:-${default}}"
}

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Ensure a URL has a scheme; auto-prepend https:// if missing.
# Skips empty strings (optional fields).
ensure_url_scheme() {
    local var="$1" val
    val="${!var}"
    [[ -z "${val}" ]] && return 0
    if [[ ! "${val}" =~ ^https?:// ]]; then
        warn "'${val}' has no scheme — prepending 'https://'."
        printf -v "${var}" 'https://%s' "${val}"
    fi
}

# Validate that a (non-empty) string looks like a URL.
# Requires a scheme and at least one dot in the hostname.
validate_url() {
    local label="$1" val="$2"
    [[ -z "${val}" ]] && return 0
    if [[ ! "${val}" =~ ^https?://[^/]+\.[^/] ]]; then
        die "${label} '${val}' does not look like a valid URL (expected https://hostname/...)."
    fi
}

# Validate that a value is a decimal number within [min, max].
validate_coord() {
    local label="$1" val="$2" min="$3" max="$4"
    if [[ ! "${val}" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
        die "${label} '${val}' is not a valid decimal number."
    fi
    if ! awk -v v="${val}" -v lo="${min}" -v hi="${max}" \
         'BEGIN { exit !(v >= lo && v <= hi) }'; then
        die "${label} '${val}' is out of range [${min}, ${max}]."
    fi
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
uninstall() {
    info "Uninstalling ${SCRIPT_NAME}..."

    if systemctl is-active --quiet "${SCRIPT_NAME}.timer" 2>/dev/null; then
        systemctl stop "${SCRIPT_NAME}.timer"
        success "Stopped timer."
    fi
    if systemctl is-enabled --quiet "${SCRIPT_NAME}.timer" 2>/dev/null; then
        systemctl disable "${SCRIPT_NAME}.timer"
        success "Disabled timer."
    fi

    for f in "${SERVICE_FILE}" "${TIMER_FILE}"; do
        [[ -f "${f}" ]] && { rm -f "${f}"; success "Removed ${f}"; }
    done

    systemctl daemon-reload
    success "Reloaded systemd."

    if [[ -f "${INSTALL_BIN}" ]]; then
        rm -f "${INSTALL_BIN}"
        success "Removed ${INSTALL_BIN}"
    fi

    echo
    warn "Configuration at ${CONF_DIR}/ was NOT removed (it contains your API key)."
    warn "Remove it manually with:  sudo rm -rf ${CONF_DIR}"
    warn "System user 'allsky-map' was NOT removed."
    warn "Remove it manually with:  sudo userdel allsky-map"
    echo
    success "Uninstall complete."
}

# ---------------------------------------------------------------------------
# Main install flow
# ---------------------------------------------------------------------------
do_install() {
    echo
    echo -e "${BOLD}================================================${NC}"
    echo -e "${BOLD}  Indi-Allsky Map Ping Client — Installer${NC}"
    echo -e "${BOLD}================================================${NC}"
    echo

    # --- Verify source files exist ---
    require_file "${SCRIPT_DIR}/${SCRIPT_NAME}"
    require_file "${SCRIPT_DIR}/${SCRIPT_NAME}.conf"
    require_file "${SCRIPT_DIR}/${SCRIPT_NAME}.service"
    require_file "${SCRIPT_DIR}/${SCRIPT_NAME}.timer"

    # --- Gather configuration interactively ---
    echo -e "${CYAN}Please enter your camera details.${NC}"
    echo -e "${CYAN}These will be written to ${CONF_FILE}.${NC}"
    echo

    ask         API_URL  "Allsky Map API URL (e.g. https://map.example.com/api/ping)"
    ask         API_KEY  "Your API Key (allsky_live_...)"
    ask         CAM_NAME "Camera name"
    ask_optional CAM_OWNER "Owner name/handle" ""
    ask_optional CAM_LAT   "Latitude (decimal degrees, e.g. -34.92)" "0.0"
    ask_optional CAM_LNG   "Longitude (decimal degrees, e.g. 138.60)" "0.0"
    ask_optional CAM_SITE  "Camera website URL (optional)" ""
    ask_optional CAM_IMG   "Live image URL (optional)" ""

    # --- Validate all inputs before showing the summary ---

    # 1. Auto-fix missing URL schemes (turns bare hostnames into https:// URLs)
    ensure_url_scheme API_URL
    ensure_url_scheme CAM_SITE
    ensure_url_scheme CAM_IMG

    # 2. Check URL formats are valid
    validate_url "API URL"    "${API_URL}"
    validate_url "Site URL"   "${CAM_SITE}"
    validate_url "Image URL"  "${CAM_IMG}"

    # 3. API URL should end with /api/ping
    if [[ ! "${API_URL}" =~ /api/ping$ ]]; then
        warn "API URL '${API_URL}' doesn't end with /api/ping — are you sure it's correct?"
        read -rp "$(echo -e "${BOLD}Continue anyway? [y/N]: ${NC}")" yn
        [[ "${yn,,}" == "y" ]] || die "Aborted."
    fi

    # 4. API key format: must start with allsky_live_ followed by a UUID
    if [[ ! "${API_KEY}" =~ ^allsky_live_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        warn "API key '${API_KEY:0:20}...' doesn't match the expected format (allsky_live_<uuid>)."
        warn "Make sure you copied it exactly from the registration page."
        read -rp "$(echo -e "${BOLD}Continue anyway? [y/N]: ${NC}")" yn
        [[ "${yn,,}" == "y" ]] || die "Aborted."
    fi

    # 5. Camera name length
    if [[ ${#CAM_NAME} -gt 100 ]]; then
        die "Camera name is too long (${#CAM_NAME} chars) — maximum is 100."
    fi

    # 6. Coordinate ranges
    validate_coord "Latitude"  "${CAM_LAT}" -90  90
    validate_coord "Longitude" "${CAM_LNG}" -180 180

    echo
    echo -e "${BOLD}--- Summary ---${NC}"
    echo -e "  API URL:     ${API_URL}"
    echo -e "  API Key:     ${API_KEY:0:20}... (truncated)"
    echo -e "  Camera:      ${CAM_NAME}"
    echo -e "  Owner:       ${CAM_OWNER:-<not set>}"
    echo -e "  Lat/Lng:     ${CAM_LAT} / ${CAM_LNG}"
    echo -e "  Site URL:    ${CAM_SITE:-<not set>}"
    echo -e "  Image URL:   ${CAM_IMG:-<not set>}"
    echo
    read -rp "$(echo -e "${BOLD}Proceed with installation? [Y/n]: ${NC}")" confirm
    [[ "${confirm,,}" != "n" ]] || { info "Aborted."; exit 0; }
    echo

    # --- 1. Create dedicated system user (idempotent) ---
    info "Creating system user 'allsky-map' (if not already present)..."
    if ! id -u allsky-map &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin --user-group allsky-map
        success "System user 'allsky-map' created."
    else
        success "System user 'allsky-map' already exists — skipping."
    fi

    # --- 2. Install the script ---
    info "Installing ${SCRIPT_NAME} to ${INSTALL_BIN}..."
    install -m 0755 "${SCRIPT_DIR}/${SCRIPT_NAME}" "${INSTALL_BIN}"
    success "Script installed."

    # --- 3. Write config ---
    info "Creating configuration at ${CONF_FILE}..."
    mkdir -p "${CONF_DIR}"

    # Preserve existing config with a backup
    if [[ -f "${CONF_FILE}" ]]; then
        local backup="${CONF_FILE}.bak.$(date +%Y%m%d%H%M%S)"
        cp --preserve=mode "${CONF_FILE}" "${backup}"
        warn "Existing config backed up to ${backup}"
    fi

    cat > "${CONF_FILE}" <<EOF
# ==============================================================================
# Indi-Allsky Map Ping Client — Configuration
# Generated by install.sh on $(date)
# Permissions: root:allsky-map 640 (service user can read, world cannot).
# ==============================================================================

API_URL="${API_URL}"
API_KEY="${API_KEY}"

CAMERA_NAME="${CAM_NAME}"
CAMERA_OWNER="${CAM_OWNER}"
CAMERA_LAT="${CAM_LAT}"
CAMERA_LNG="${CAM_LNG}"
CAMERA_SITE_URL="${CAM_SITE}"
CAMERA_IMAGE_URL="${CAM_IMG}"
EOF

    chown root:allsky-map "${CONF_FILE}"
    chmod 640 "${CONF_FILE}"
    success "Config written (root:allsky-map 640 — service user can read, world cannot)."

    # --- 4. Install systemd units ---
    info "Installing systemd units..."
    install -m 0644 "${SCRIPT_DIR}/${SCRIPT_NAME}.service" "${SERVICE_FILE}"
    install -m 0644 "${SCRIPT_DIR}/${SCRIPT_NAME}.timer"   "${TIMER_FILE}"
    success "Systemd units installed."

    # --- 5. Enable and start ---
    info "Reloading systemd and enabling timer..."
    systemctl daemon-reload
    systemctl enable --now "${SCRIPT_NAME}.timer"
    success "Timer enabled and started."

    # --- 6. Run immediately to verify ---
    echo
    info "Running a test ping now to verify the configuration..."
    if systemctl start "${SCRIPT_NAME}.service"; then
        echo
        success "Test ping succeeded! Check the output with:"
        echo -e "    ${BOLD}journalctl -u ${SCRIPT_NAME}.service -n 20${NC}"
    else
        echo
        warn "Test ping failed. Check the logs for details:"
        echo -e "    ${BOLD}journalctl -u ${SCRIPT_NAME}.service -n 20${NC}"
        echo -e "    ${BOLD}journalctl -xe${NC}"
    fi

    # --- Final summary ---
    echo
    echo -e "${BOLD}================================================${NC}"
    echo -e "${GREEN}${BOLD}  Installation complete!${NC}"
    echo -e "${BOLD}================================================${NC}"
    echo
    echo -e "  Script:    ${INSTALL_BIN}"
    echo -e "  Config:    ${CONF_FILE}"
    echo -e "  Service:   ${SERVICE_FILE}"
    echo -e "  Timer:     ${TIMER_FILE}"
    echo
    echo -e "Useful commands:"
    echo -e "  ${BOLD}systemctl status ${SCRIPT_NAME}.timer${NC}            — check timer status"
    echo -e "  ${BOLD}systemctl list-timers --all | grep allsky${NC}     — see next run time"
    echo -e "  ${BOLD}journalctl -u ${SCRIPT_NAME}.service -f${NC}         — follow logs"
    echo -e "  ${BOLD}sudo bash $0 --uninstall${NC}                      — remove everything"
    echo
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
require_root

case "${1:-}" in
    --uninstall|-u) uninstall ;;
    --help|-h)
        echo "Usage: sudo bash $0             # install"
        echo "       sudo bash $0 --uninstall # remove"
        ;;
    *) do_install ;;
esac
