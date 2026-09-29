#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo ./uninstall.sh"; exit 1; }

TARGET_USER=${SUDO_USER:-$(logname)}
if [ -z "$TARGET_USER" ]; then
    echo "Could not determine the target user."
    exit 1
fi
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
INSTALL_DIR="${TARGET_HOME}/.local/share/decky-sunshine-res-helper"
SERVICE_DEST=/etc/systemd/system/decky-sunshine-res-helper.service

echo "==> Stopping and disabling decky-sunshine-res-helper service..."
systemctl disable --now decky-sunshine-res-helper || true

echo "==> Removing systemd service file..."
rm -f "$SERVICE_DEST"
systemctl daemon-reload || true

echo "==> Removing project files..."
rm -rf "$INSTALL_DIR"

echo "==> Cleaning up FIFO file..."
rm -f "${TARGET_HOME}/.sunshine-res-helper.fifo" "${TARGET_HOME}/.sunshine-res-helper.ready" "${TARGET_HOME}/.sunshine-res-helper.in" "${TARGET_HOME}/.sunshine-res-helper.out"

echo "Uninstall complete."
