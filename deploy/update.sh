#!/usr/bin/env bash
# Update the installed service to the latest commit on origin/main.
#
#   Run as root:  /opt/refresh-azure-wanip/deploy/update.sh [--force]
#
# Pulls new commits, reinstalls the package into .venv, makes sure the systemd
# unit is linked from the repo, then restarts the service and checks it's up.
# Exits without changes if already up to date (--force reinstalls and restarts
# anyway) or if a reset is running.
#
# Everything is inside main(), which runs on the last line, so bash has read
# the whole script before `git pull` can replace this file.
set -euo pipefail

INSTALL_DIR="/opt/refresh-azure-wanip"
SERVICE="refresh-azure-wanip.service"
PACKAGE="refresh-azure-wanip"
UNIT_SOURCE="${INSTALL_DIR}/deploy/${SERVICE}"
UNIT_TARGET="/etc/systemd/system/${SERVICE}"
PYTHON="${INSTALL_DIR}/.venv/bin/python"

die() { echo "ERROR: $*" >&2; exit 1; }

# Print the service's /status URL, using host and port from config.toml.
status_url() {
    "$PYTHON" - <<'EOF'
import tomllib
with open('config.toml', 'rb') as f:
    server = tomllib.load(f).get('server', {})
host = server.get('host', '') or '127.0.0.1'
if host in ('0.0.0.0', '::'):
    host = '127.0.0.1'
print(f"http://{host}:{server.get('port', 8780)}/status")
EOF
}

# Print the current reset state ('idle', 'running', ...), or 'down' if the
# service doesn't answer.
reset_state() {
    curl -sS --max-time 5 "$(status_url)" 2>/dev/null \
        | "$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["state"])' \
        2>/dev/null || echo down
}

main() {
    local force=false
    case "${1:-}" in
        --force) force=true ;;
        '') ;;
        *) die "usage: $0 [--force]" ;;
    esac

    # --- Pre-run checks
    [[ $EUID -eq 0 ]] || die "run as root (ssh root@host or sudo -i)"
    [[ -d "$INSTALL_DIR/.git" ]] || die "$INSTALL_DIR is not a git checkout"
    command -v uv >/dev/null || die "uv not found on root's PATH"
    cd "$INSTALL_DIR"

    # --- Anything new?
    local local_commit remote_commit
    git fetch origin main --quiet
    local_commit=$(git rev-parse --short HEAD)
    remote_commit=$(git rev-parse --short origin/main)

    if [[ "$local_commit" == "$remote_commit" ]]; then
        if ! $force; then
            echo "No updates, already at ${local_commit}"
            exit 0
        fi
        echo "=== No new commits (${local_commit}), --force: reinstalling anyway"
    else
        echo "=== Update available: ${local_commit} -> ${remote_commit}"
    fi

    # --- Never restart in the middle of a reset (the old MAC wouldn't be
    #     restored and the run wouldn't be confirmed)
    if [[ "$(reset_state)" == running ]]; then
        die "a reset is running; try again when /status is no longer 'running'"
    fi

    # --- Pull and reinstall
    if [[ "$local_commit" != "$remote_commit" ]]; then
        git pull --ff-only --quiet origin main
        git log --oneline "${local_commit}..HEAD"
    fi
    echo "=== Reinstalling package"
    uv pip install --quiet --reinstall-package "$PACKAGE" .

    # --- Link the unit file from the repo, so pulls also update it
    if [[ "$(readlink "$UNIT_TARGET" 2>/dev/null)" != "$UNIT_SOURCE" ]]; then
        echo "=== Linking ${UNIT_TARGET} -> ${UNIT_SOURCE}"
        ln -sfn "$UNIT_SOURCE" "$UNIT_TARGET"
    fi
    systemctl daemon-reload
    systemctl is-enabled --quiet "$SERVICE" || systemctl enable "$SERVICE"

    # --- Restart and check
    echo "=== Restarting ${SERVICE}"
    systemctl restart "$SERVICE"
    sleep 3
    systemctl status "$SERVICE" --no-pager --lines 5 || true

    systemctl is-active --quiet "$SERVICE" \
        || die "${SERVICE} is not running; see: journalctl -u ${SERVICE} -n 50"
    local state
    state=$(reset_state)
    [[ "$state" != down ]] || die "service is running but /status doesn't answer"

    echo
    echo "=== Done: now at $(git rev-parse --short HEAD), state '${state}'"
}

main "$@"
