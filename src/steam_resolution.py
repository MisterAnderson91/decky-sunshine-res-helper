"""
Read and write SteamOS' "Maximum Game Resolution" setting.

The setting is the Steam client setting ``gamescope_game_resolution_global``
(field 21012 of the ``CMsgClientSettings`` protobuf). Steam caches it in memory
and periodically rewrites ``localconfig.vdf``, so editing the file directly is
reverted. Instead we:

* READ it from ``~/.local/share/Steam/userdata/<accountid>/config/localconfig.vdf``
  (key ``GameResolutionGlobal``), which Steam updates as soon as it changes.
* WRITE it by calling ``SteamClient.Settings.SetSetting(<base64 protobuf>)`` in
  Steam's SharedJSContext over the CEF remote-debugging port (enabled by Decky).

Every public function here is best-effort: failures are logged and reported via
the return value, never raised.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pwd
import re
import socket
import struct
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SETTING_NAME = "gamescope_game_resolution_global"
SETTING_FIELD = 21012
VDF_KEY = "GameResolutionGlobal"

FC_SETTING_NAME = "gamescope_force_composite"
FC_SETTING_FIELD = 21011
FC_VDF_KEY = "ForceComposite"
STEAMID64_BASE = 76561197960265728

CEF_DEBUG_HOST = "127.0.0.1"
CEF_DEBUG_PORT = 8080
CEF_TARGET_TITLE = "SharedJSContext"
CEF_TIMEOUT = 5.0

_VALID_VALUE = re.compile(r"^(Native|Default|\d{2,5}x\d{2,5})$")


# ---------------------------------------------------------------------------
# Reading (localconfig.vdf)
# ---------------------------------------------------------------------------

def _steam_root(user: str) -> Path:
    try:
        home = Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        home = Path(f"/home/{user}")
    return home / ".local" / "share" / "Steam"


def _active_account_id(steam_root: Path) -> str | None:
    """Return the userdata folder name of the active Steam account."""
    login = steam_root / "config" / "loginusers.vdf"
    try:
        if login.exists():
            text = login.read_text(errors="ignore")
            users = re.findall(r'"(\d{17})"\s*\{(.*?)\}', text, re.S)
            if len(users) == 1:
                return str(int(users[0][0]) - STEAMID64_BASE)
            for sid, body in users:
                if re.search(r'"MostRecent"\s*"1"', body) or re.search(r'"AutoLogin"\s*"1"', body):
                    return str(int(sid) - STEAMID64_BASE)
    except OSError as exc:
        log.debug("Could not read %s: %s", login, exc)

    # Fallback: the most recently modified localconfig.vdf
    configs = sorted(
        steam_root.glob("userdata/*/config/localconfig.vdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return configs[0].parent.parent.name if configs else None


def read_global_resolution(user: str) -> str | None:
    """
    Return the current Maximum Game Resolution string (e.g. "800x500", "Native",
    "Default"), or None if it could not be determined.
    """
    try:
        steam_root = _steam_root(user)
        account = _active_account_id(steam_root)
        if not account:
            log.error("Steam resolution: could not determine active Steam account under %s", steam_root)
            return None

        cfg = steam_root / "userdata" / account / "config" / "localconfig.vdf"
        text = cfg.read_text(errors="ignore")
        match = re.search(rf'"{VDF_KEY}"\s*"([^"]*)"', text)
        if not match:
            # Steam omits the key until the user changes it; its built-in value is "Default".
            log.info("Steam resolution: %s not present in %s — assuming \"Default\"", VDF_KEY, cfg)
            return "Default"
        value = match.group(1)
        if not _VALID_VALUE.match(value):
            log.error("Steam resolution: unexpected %s value %r in %s", VDF_KEY, value, cfg)
            return None
        return value
    except Exception as exc:
        log.error("Steam resolution: failed to read current value: %s", exc)
        return None

def read_force_composite(user: str) -> bool:
    """
    Return the current ForceComposite boolean value from localconfig.vdf.
    Defaults to False if not present.
    """
    try:
        steam_root = _steam_root(user)
        account = _active_account_id(steam_root)
        if not account:
            log.error("ForceComposite: could not determine active Steam account under %s", steam_root)
            return False

        cfg = steam_root / "userdata" / account / "config" / "localconfig.vdf"
        text = cfg.read_text(errors="ignore")
        match = re.search(rf'"{FC_VDF_KEY}"\s*"([^"]*)"', text)
        if not match:
            return False
        return match.group(1) == "1"
    except Exception as exc:
        log.error("ForceComposite: failed to read current value: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Writing (CEF remote debugging -> SteamClient.Settings.SetSetting)
# ---------------------------------------------------------------------------

class _WebSocket:
    """Just enough of a WebSocket client (RFC 6455) to send/receive CDP JSON text frames."""

    def __init__(self, url: str, timeout: float):
        u = urlparse(url)
        self._sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake: connection closed")
            buf += chunk
        status_line = buf.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise ConnectionError(f"WebSocket handshake failed: {status_line!r}")
        self._buf = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        header = bytearray([0x81])  # FIN + text frame
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self._sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        message = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if (b1 & 0x0F) == 0x8:
                raise ConnectionError("WebSocket closed by peer")
            message += payload
            if b1 & 0x80:
                return message.decode(errors="ignore")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _cef_evaluate(js: str) -> dict:
    """Evaluate JS in Steam's SharedJSContext and return the CDP Runtime.evaluate result."""
    list_url = f"http://{CEF_DEBUG_HOST}:{CEF_DEBUG_PORT}/json"
    with urllib.request.urlopen(list_url, timeout=CEF_TIMEOUT) as resp:
        targets = json.load(resp)
    target = next((t for t in targets if t.get("title") == CEF_TARGET_TITLE), None)
    if not target or "webSocketDebuggerUrl" not in target:
        raise RuntimeError(f"{CEF_TARGET_TITLE} not found on CEF debug port (is Steam running in Game Mode?)")

    ws = _WebSocket(target["webSocketDebuggerUrl"], CEF_TIMEOUT)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": js, "awaitPromise": True, "returnByValue": True},
        }))
        deadline = time.monotonic() + CEF_TIMEOUT
        while time.monotonic() < deadline:
            reply = json.loads(ws.recv())
            if reply.get("id") == 1:
                return reply
        raise TimeoutError("No reply to Runtime.evaluate")
    finally:
        ws.close()


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)

def _encode_setting(value: str) -> str:
    """Encode {gamescope_game_resolution_global: value} as a base64 CMsgClientSettings protobuf."""
    raw = value.encode()
    msg = _varint((SETTING_FIELD << 3) | 2) + _varint(len(raw)) + raw
    return base64.b64encode(msg).decode()

def _encode_bool_setting(field_id: int, value: bool) -> str:
    """Encode a boolean setting as a base64 CMsgClientSettings protobuf (wire type 0)."""
    msg = _varint((field_id << 3) | 0) + _varint(1 if value else 0)
    return base64.b64encode(msg).decode()


def write_global_resolution(value: str, user: str | None = None) -> bool:
    """
    Set Maximum Game Resolution via Steam. Returns True if Steam accepted the call.
    If ``user`` is given, also waits briefly for localconfig.vdf to reflect the change.
    """
    if not _VALID_VALUE.match(value):
        log.error("Steam resolution: refusing to write invalid value %r", value)
        return False

    js = (
        "(() => { if (!window.SteamClient?.Settings?.SetSetting) return 'no SteamClient.Settings.SetSetting';"
        f" SteamClient.Settings.SetSetting({json.dumps(_encode_setting(value))}); return 'ok'; }})()"
    )
    try:
        reply = _cef_evaluate(js)
    except Exception as exc:
        log.error(
            "Steam resolution: could not reach Steam's CEF debugger at %s:%d to set %r: %s",
            CEF_DEBUG_HOST, CEF_DEBUG_PORT, value, exc,
        )
        return False

    result = reply.get("result", {})
    if "exceptionDetails" in result or "error" in reply:
        details = result.get("exceptionDetails") or reply.get("error")
        log.error("Steam resolution: SetSetting(%r) raised: %s", value, json.dumps(details)[:500])
        return False
    outcome = result.get("result", {}).get("value")
    if outcome != "ok":
        log.error("Steam resolution: SetSetting(%r) failed: %s", value, outcome)
        return False

    if user:
        for _ in range(10):
            if read_global_resolution(user) == value:
                break
            time.sleep(0.2)
        else:
            log.warning("Steam resolution: Steam accepted %r but localconfig.vdf has not updated yet", value)
    return True

def write_force_composite(enabled: bool, user: str | None = None) -> bool:
    """
    Set Force Composite via Steam. Returns True if Steam accepted the call.
    """
    js = (
        "(() => { if (!window.SteamClient?.Settings?.SetSetting) return 'no SteamClient.Settings.SetSetting';"
        f" SteamClient.Settings.SetSetting({json.dumps(_encode_bool_setting(FC_SETTING_FIELD, enabled))}); return 'ok'; }})()"
    )
    try:
        reply = _cef_evaluate(js)
    except Exception as exc:
        log.error("ForceComposite: could not reach Steam's CEF debugger to set %r: %s", enabled, exc)
        return False

    result = reply.get("result", {})
    if "exceptionDetails" in result or "error" in reply:
        details = result.get("exceptionDetails") or reply.get("error")
        log.error("ForceComposite: SetSetting(%r) raised: %s", enabled, json.dumps(details)[:500])
        return False
    outcome = result.get("result", {}).get("value")
    if outcome != "ok":
        log.error("ForceComposite: SetSetting(%r) failed: %s", enabled, outcome)
        return False

    if user:
        for _ in range(10):
            if read_force_composite(user) == enabled:
                break
            time.sleep(0.2)
        else:
            log.warning("ForceComposite: Steam accepted %r but localconfig.vdf has not updated yet", enabled)
    return True
