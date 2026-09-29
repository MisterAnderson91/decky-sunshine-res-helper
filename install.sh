#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo ./install.sh"; exit 1; }

TARGET_USER=${SUDO_USER:-$(logname)}
if [ -z "$TARGET_USER" ]; then
    echo "Could not determine the target user."
    exit 1
fi
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
INSTALL_DIR="${TARGET_HOME}/.local/share/decky-sunshine-res-helper"
SERVICE_DEST=/etc/systemd/system/decky-sunshine-res-helper.service

echo "==> Downloading jeepney locally..."
python3 -c "
import urllib.request, json, zipfile, io, os
url = 'https://pypi.org/pypi/jeepney/json'
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    urls = data['urls']
    wheel_url = next(u['url'] for u in urls if u['url'].endswith('.whl'))
print('Downloading jeepney from', wheel_url)
import shutil
os.makedirs('/tmp/jeepney_dl', exist_ok=True)
with urllib.request.urlopen(wheel_url) as response:
    with zipfile.ZipFile(io.BytesIO(response.read())) as z:
        for member in z.namelist():
            if member.startswith('jeepney/'):
                z.extract(member, '/tmp/jeepney_dl')
"

echo "==> Copying project to $INSTALL_DIR..."
install -d "$INSTALL_DIR"
rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.coverage' \
    --exclude='custom_edid.bin' \
    --exclude='virt_display.state' \
    . "$INSTALL_DIR/"

echo "==> Installing jeepney to $INSTALL_DIR..."
cp -r /tmp/jeepney_dl/jeepney "$INSTALL_DIR/"
rm -rf /tmp/jeepney_dl

echo "==> Installing systemd service..."
cat > "$SERVICE_DEST" <<EOF
[Unit]
Description=Decky Sunshine Res-Helper Daemon
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/src/daemon/daemon.py --user ${TARGET_USER}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable --now decky-sunshine-res-helper

echo ""
echo "Done. Status:"
systemctl status decky-sunshine-res-helper --no-pager || true

echo ""
echo "=================================================================="
echo "Installation Complete!"
echo "Please add the following to your Sunshine 'General' configuration:"
echo ""
echo "Do Command:"
echo "sh -c \"echo --connect,--width,\${SUNSHINE_CLIENT_WIDTH},--height,\${SUNSHINE_CLIENT_HEIGHT},--refresh-rate,\${SUNSHINE_CLIENT_FPS} > ${TARGET_HOME}/.sunshine-res-helper.in && cat ${TARGET_HOME}/.sunshine-res-helper.out\""
echo ""
echo "Undo Command:"
echo "sh -c \"echo --disconnect > ${TARGET_HOME}/.sunshine-res-helper.in && cat ${TARGET_HOME}/.sunshine-res-helper.out\""
echo "=================================================================="
