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
    --exclude='gamescope_game_resolution_global.state' \
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
systemctl enable decky-sunshine-res-helper
systemctl restart decky-sunshine-res-helper

echo ""
echo "Done. Status:"
systemctl status decky-sunshine-res-helper --no-pager || true

echo "==> Configuring Sunshine global_prep_cmd..."
python3 -c "
import sys, json, os, re
CONF_PATH = '/root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.conf'
TARGET_HOME = sys.argv[1]
MODE = sys.argv[2]
if not os.path.exists(CONF_PATH):
    print(f'Sunshine config not found at {CONF_PATH}, skipping automation.')
    sys.exit(0)
do_cmd = 'sh -c \"echo --connect,--width,\${SUNSHINE_CLIENT_WIDTH},--height,\${SUNSHINE_CLIENT_HEIGHT},--refresh-rate,\${SUNSHINE_CLIENT_FPS} > /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in && cat /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out\"'
undo_cmd = 'sh -c \"echo --disconnect > /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in && cat /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out\"'
our_cmd_obj = {'do': do_cmd, 'undo': undo_cmd}
old_do_cmd = f'sh -c \"echo --connect,--width,\${{SUNSHINE_CLIENT_WIDTH}},--height,\${{SUNSHINE_CLIENT_HEIGHT}},--refresh-rate,\${{SUNSHINE_CLIENT_FPS}} > {TARGET_HOME}/.sunshine-res-helper.in && cat {TARGET_HOME}/.sunshine-res-helper.out\"'
old_undo_cmd = f'sh -c \"echo --disconnect > {TARGET_HOME}/.sunshine-res-helper.in && cat {TARGET_HOME}/.sunshine-res-helper.out\"'
with open(CONF_PATH, 'r') as f: lines = f.readlines()
new_lines = []
found = False
for line in lines:
    if line.startswith('global_prep_cmd'):
        found = True
        match = re.match(r'global_prep_cmd\s*=\s*(.*)', line)
        if match:
            try: cmds = json.loads(match.group(1).strip())
            except Exception: cmds = []
            if not isinstance(cmds, list): cmds = []
            cmds = [cmd for cmd in cmds if not (
                (cmd.get('do') == do_cmd and cmd.get('undo') == undo_cmd) or
                (cmd.get('do') == old_do_cmd and cmd.get('undo') == old_undo_cmd)
            )]
            if MODE == 'install': cmds.append(our_cmd_obj)
            if len(cmds) > 0: new_lines.append(f'global_prep_cmd = {json.dumps(cmds, separators=(\",\", \":\"))}\\n')
        else: new_lines.append(line)
    else: new_lines.append(line)
if MODE == 'install' and not found:
    if len(new_lines) > 0 and not new_lines[-1].endswith('\n'): new_lines[-1] += '\n'
    new_lines.append(f'global_prep_cmd = {json.dumps([our_cmd_obj], separators=(\",\", \":\"))}\\n')
with open(CONF_PATH, 'w') as f: f.writelines(new_lines)
print(f'Successfully updated Sunshine config for {MODE}.')
" "$TARGET_HOME" "install"

echo ""
echo "=================================================================="
echo "Installation Complete!"
echo "These commands are added automatically by the installer. They are"
echo "provided here in case they weren't added automatically (or if you"
echo "need to copy them manually):"
echo ""
echo "Do Command:"
echo "sh -c \"echo --connect,--width,\${SUNSHINE_CLIENT_WIDTH},--height,\${SUNSHINE_CLIENT_HEIGHT},--refresh-rate,\${SUNSHINE_CLIENT_FPS} > /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in && cat /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out\""
echo ""
echo "Undo Command:"
echo "sh -c \"echo --disconnect > /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in && cat /root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out\""
echo "=================================================================="
