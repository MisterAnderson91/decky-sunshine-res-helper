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

echo "==> Removing from Sunshine global_prep_cmd..."
python3 -c "
import sys, json, os, re
CONF_PATH = '/root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.conf'
TARGET_HOME = sys.argv[1]
MODE = sys.argv[2]
if not os.path.exists(CONF_PATH):
    print(f'Sunshine config not found at {CONF_PATH}, skipping automation.')
    sys.exit(0)
do_cmd = f'sh -c \"echo --connect,--width,\${{SUNSHINE_CLIENT_WIDTH}},--height,\${{SUNSHINE_CLIENT_HEIGHT}},--refresh-rate,\${{SUNSHINE_CLIENT_FPS}} > {TARGET_HOME}/.sunshine-res-helper.in && cat {TARGET_HOME}/.sunshine-res-helper.out\"'
undo_cmd = f'sh -c \"echo --disconnect > {TARGET_HOME}/.sunshine-res-helper.in && cat {TARGET_HOME}/.sunshine-res-helper.out\"'
our_cmd_obj = {'do': do_cmd, 'undo': undo_cmd}
with open(CONF_PATH, 'r') as f: lines = f.readlines()
new_lines = []
for line in lines:
    if line.startswith('global_prep_cmd'):
        match = re.match(r'global_prep_cmd\s*=\s*(.*)', line)
        if match:
            try: cmds = json.loads(match.group(1).strip())
            except Exception: cmds = []
            if not isinstance(cmds, list): cmds = []
            cmds = [cmd for cmd in cmds if not (cmd.get('do') == do_cmd and cmd.get('undo') == undo_cmd)]
            if len(cmds) > 0: new_lines.append(f'global_prep_cmd = {json.dumps(cmds, separators=(\",\", \":\"))}\\n')
        else: new_lines.append(line)
    else: new_lines.append(line)
with open(CONF_PATH, 'w') as f: f.writelines(new_lines)
print(f'Successfully updated Sunshine config for {MODE}.')
" "$TARGET_HOME" "uninstall"

echo "Uninstall complete."
