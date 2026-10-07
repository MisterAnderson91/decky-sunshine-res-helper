#!/usr/bin/env python3
"""Sunshine Resolution Helper Daemon — manages display EDID overrides via Unix socket."""

import argparse
import logging
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from jeepney import DBusAddress, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection
from jeepney.low_level import HeaderFields

from src import display

log = logging.getLogger(__name__)

SUNSHINE_UNIT_PATH = "/org/freedesktop/systemd1/unit/sunshine_2eservice"

_lock = threading.Lock()
_state: dict = {
    "connected": False,
    "connect_args": None,       # (width, height, refresh_rate, device)
    "sleep_was_connected": False,
    "dropped_out": False,
}
_server: socket.socket | None = None
_inhibitor_fd: int | None = None
_running = True


# ---------------------------------------------------------------------------
# Sleep inhibitor (systemd logind delay lock)
# ---------------------------------------------------------------------------

def _acquire_inhibitor() -> int | None:
    for attempt in range(15):
        try:
            conn = open_dbus_connection(bus="SYSTEM")
            addr = DBusAddress(
                "/org/freedesktop/login1",
                bus_name="org.freedesktop.login1",
                interface="org.freedesktop.login1.Manager",
            )
            msg = new_method_call(
                addr,
                "Inhibit",
                "ssss",
                ("sleep", "decky-sunshine-res-helper", "Revert display resolution before sleep", "delay"),
            )
            reply = conn.send_and_get_reply(msg)
            # reply.body[0] is a jeepney.wrappers.UnixFd; .fileno() gives the raw fd
            raw_fd = reply.body[0].fileno()
            # Duplicate so the jeepney connection closing doesn't steal the fd
            owned_fd = os.dup(raw_fd)
            conn.close()
            log.info("Acquired sleep inhibitor lock (fd=%d)", owned_fd)
            return owned_fd
        except ImportError:
            log.warning("jeepney not installed — sleep inhibitor disabled")
            return None
        except Exception as exc:
            if attempt < 14:
                time.sleep(2)
            else:
                log.warning("Could not acquire sleep inhibitor after 30s: %s", exc)
                return None


def _release_inhibitor() -> None:
    global _inhibitor_fd
    if _inhibitor_fd is not None:
        try:
            os.close(_inhibitor_fd)
            log.info("Released sleep inhibitor lock")
        except OSError:
            pass
        _inhibitor_fd = None


# ---------------------------------------------------------------------------
# Sleep / wake handlers
# ---------------------------------------------------------------------------

def _on_sleep(going_to_sleep: bool) -> None:
    global _inhibitor_fd

    if going_to_sleep:
        log.info("System going to sleep")
        with _lock:
            was_connected = _state["connected"]
            if was_connected:
                _state["sleep_was_connected"] = True
                saved_args = _state["connect_args"]
            else:
                _state["sleep_was_connected"] = False
                saved_args = None

        if was_connected:
            log.info("Reverting display resolution before sleep")
            ok = display.disconnect()
            with _lock:
                if ok:
                    _state["connected"] = False
                    log.info("Disconnected before sleep")
                else:
                    log.warning("Disconnect before sleep failed")

        # Release the inhibitor so the system can actually suspend
        _release_inhibitor()

    else:
        log.info("System waking up")
        # Re-acquire inhibitor for the next sleep cycle
        _inhibitor_fd = _acquire_inhibitor()

        with _lock:
            should_reconnect = _state["sleep_was_connected"]
            args = _state["connect_args"]

        if should_reconnect and args:
            width, height, refresh_rate, device = args
            log.info(
                "Re-applying display resolution after wake: %dx%d@%d", width, height, refresh_rate
            )
            ok = display.connect(width, height, refresh_rate, device=device)
            with _lock:
                if ok:
                    _state["connected"] = True
                    _state["sleep_was_connected"] = False
                    log.info("Reconnected after wake")
                else:
                    log.error("Failed to reconnect after wake")


# ---------------------------------------------------------------------------
# DBus sleep signal listener (runs in a background thread)
# ---------------------------------------------------------------------------

def _dbus_query_property(obj_path: str, bus_name: str, interface: str, prop: str):
    """Open a fresh system-bus connection, read one property, close."""
    conn = open_dbus_connection(bus="SYSTEM")
    try:
        addr = DBusAddress(obj_path, bus_name=bus_name,
                           interface="org.freedesktop.DBus.Properties")
        reply = conn.send_and_get_reply(new_method_call(addr, "Get", "ss", (interface, prop)))
        return reply.body[0][1]  # unwrap DBus variant → (sig, value)
    finally:
        conn.close()


def _get_sunshine_pid() -> int | None:
    """Find the Sunshine process PID by scanning /proc/*/comm."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() == "sunshine":
                return int(entry.name)
        except OSError:
            continue
    log.warning("Could not find a running 'sunshine' process")
    return None


def _watch_sunshine_pid(pid: int) -> None:
    """Block on a pidfd until Sunshine exits, then disconnect."""
    try:
        pidfd = os.pidfd_open(pid)
    except OSError as exc:
        log.warning("pidfd_open(%d) failed: %s", pid, exc)
        return

    log.info("Watching Sunshine PID %d", pid)
    try:
        while _running:
            ready, _, _ = select.select([pidfd], [], [], 1.0)
            if ready:
                break
    finally:
        os.close(pidfd)

    if not _running:
        return

    with _lock:
        connected = _state["connected"]
    if connected:
        log.warning("Sunshine PID %d exited — reverting display resolution", pid)
        ok = display.disconnect()
        with _lock:
            if ok:
                _state["connected"] = False
                _state["connect_args"] = None


def _on_shutdown_signal(shutting_down: bool) -> None:
    if not shutting_down:
        return
    log.info("System shutting down — reverting display resolution")
    with _lock:
        connected = _state["connected"]
    if connected:
        ok = display.disconnect()
        with _lock:
            if ok:
                _state["connected"] = False


def _on_sunshine_unit_changed(body) -> None:
    try:
        iface, changed, invalidated = body
    except (ValueError, TypeError):
        return

    if iface != "org.freedesktop.systemd1.Unit":
        return

    if "ActiveState" in changed:
        state = changed["ActiveState"][1]
    elif "ActiveState" in invalidated:
        try:
            state = _dbus_query_property(
                SUNSHINE_UNIT_PATH,
                "org.freedesktop.systemd1",
                "org.freedesktop.systemd1.Unit",
                "ActiveState",
            )
        except Exception as exc:
            log.debug("Could not query Sunshine ActiveState: %s", exc)
            return
    else:
        return

    if state in ("failed", "inactive"):
        with _lock:
            connected = _state["connected"]
        if connected:
            log.warning("Sunshine became %s — reverting display resolution", state)
            ok = display.disconnect()
            with _lock:
                if ok:
                    _state["connected"] = False
                    _state["connect_args"] = None


def _dbus_listener() -> None:
    try:
        conn = open_dbus_connection(bus="SYSTEM")

        match_rules = [
            "type='signal',interface='org.freedesktop.login1.Manager',"
            "member='PrepareForSleep',path='/org/freedesktop/login1'",
            "type='signal',interface='org.freedesktop.login1.Manager',"
            "member='PrepareForShutdown',path='/org/freedesktop/login1'",
            f"type='signal',interface='org.freedesktop.DBus.Properties',"
            f"member='PropertiesChanged',path='{SUNSHINE_UNIT_PATH}'",
        ]
        for rule in match_rules:
            conn.send_and_get_reply(new_method_call(message_bus, "AddMatch", "s", (rule,)))

        # Tell systemd to emit unit property signals
        systemd_mgr = DBusAddress(
            "/org/freedesktop/systemd1",
            bus_name="org.freedesktop.systemd1",
            interface="org.freedesktop.systemd1.Manager",
        )
        try:
            conn.send_and_get_reply(new_method_call(systemd_mgr, "Subscribe", "", ()))
        except Exception as exc:
            log.warning("Could not subscribe to systemd signals: %s", exc)

        # Acquire the inhibitor here, after the connection is proven to work,
        # rather than racing at daemon startup before the bus is fully ready.
        _inhibitor_fd = _acquire_inhibitor()

        log.info("DBus listener ready (sleep, shutdown, Sunshine unit)")

        while _running:
            try:
                msg = conn.receive()
            except Exception:
                if not _running:
                    break
                raise

            member = msg.header.fields.get(HeaderFields.member)
            path = msg.header.fields.get(HeaderFields.path)

            if member == "PrepareForSleep":
                _on_sleep(bool(msg.body[0]))
            elif member == "PrepareForShutdown":
                _on_shutdown_signal(bool(msg.body[0]))
            elif member == "PropertiesChanged" and path == SUNSHINE_UNIT_PATH:
                _on_sunshine_unit_changed(msg.body)

    except Exception as exc:
        log.error("DBus listener failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Sunshine Log Watcher (handles ping timeouts when Sunshine skips prep cmds)
# ---------------------------------------------------------------------------

def _watch_sunshine_logs(target_home: str) -> None:
    log_path = "/root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.log"
    
    proc = subprocess.Popen(
        ["tail", "-F", log_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    
    disconnect_timer = None
    
    def do_disconnect():
        with _lock:
            connected = _state["connected"]
        if connected:
            log.info("Network drop timeout reached — auto-reverting display resolution")
            ok = display.disconnect()
            with _lock:
                if ok:
                    _state["connected"] = False
                    _state["dropped_out"] = True

    try:
        while _running:
            line = proc.stdout.readline()
            if not line:
                # Give it a short sleep if EOF, though tail -F shouldn't hit EOF
                time.sleep(0.5)
                continue
            
            if "CLIENT DISCONNECTED" in line:
                if disconnect_timer is not None:
                    disconnect_timer.cancel()
                # Give Sunshine 15 seconds to gracefully disconnect, or reconnect
                disconnect_timer = threading.Timer(15.0, do_disconnect)
                disconnect_timer.start()
                
            elif "CLIENT CONNECTED" in line:
                if disconnect_timer is not None:
                    disconnect_timer.cancel()
                    disconnect_timer = None
                
                with _lock:
                    dropped_out = _state.get("dropped_out", False)
                    args = _state.get("connect_args")
                
                if dropped_out and args:
                    width, height, refresh_rate, device = args
                    log.info(
                        "Reconnected after drop — auto-applying display resolution: %dx%d@%d",
                        width, height, refresh_rate
                    )
                    ok = display.connect(width, height, refresh_rate, device=device)
                    with _lock:
                        if ok:
                            _state["connected"] = True
                            _state["dropped_out"] = False

    except Exception as exc:
        log.error("Log watcher failed: %s", exc)
    finally:
        proc.terminate()


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="decky-sunshine-res-helper")
    p.add_argument("--connect", action="store_true")
    p.add_argument("--disconnect", action="store_true")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--refresh-rate", type=int, default=60)
    p.add_argument("--hdr", type=str, default="false")
    p.add_argument("-d", "--device", type=str, default=None)
    return p


def _handle_command(args: list[str]) -> None:
    try:
        parsed = _make_parser().parse_args(args)
    except SystemExit:
        log.warning("Could not parse command: %s", args)
        return

    if parsed.connect:
        if not parsed.width or not parsed.height:
            log.error("--connect requires --width and --height")
            return
        log.info(
            "Applying display resolution: %dx%d@%d, HDR: %s", parsed.width, parsed.height, parsed.refresh_rate, parsed.hdr
        )
        ok = display.connect(
            parsed.width, parsed.height, parsed.refresh_rate, device=parsed.device, enable_hdr=(parsed.hdr.lower() == "true")
        )
        with _lock:
            if ok:
                _state["connected"] = True
                _state["dropped_out"] = False
                _state["connect_args"] = (
                    parsed.width,
                    parsed.height,
                    parsed.refresh_rate,
                    parsed.device,
                    parsed.hdr,
                )
            else:
                log.error("connect() failed")

        if ok:
            pid = _get_sunshine_pid()
            if pid:
                threading.Thread(
                    target=_watch_sunshine_pid,
                    args=(pid,),
                    daemon=True,
                    name="sunshine-pid-watch",
                ).start()

    elif parsed.disconnect:
        log.info("Reverting display resolution")
        ok = display.disconnect()
        with _lock:
            if ok:
                _state["connected"] = False
                _state["dropped_out"] = False
                _state["connect_args"] = None
            else:
                log.error("disconnect() failed")

    else:
        log.warning("Received command with neither --connect nor --disconnect: %s", args)


# ---------------------------------------------------------------------------
# Cleanup and shutdown
# ---------------------------------------------------------------------------

def _cleanup() -> None:
    _release_inhibitor()
    if _server is not None:
        try:
            _server.close()
        except OSError:
            pass
    try:
        if _fifo_in:
            os.remove(_fifo_in)
    except OSError:
        pass
    try:
        if _fifo_out:
            os.remove(_fifo_out)
    except OSError:
        pass


def _shutdown(signum, frame) -> None:
    global _running
    log.info("Received signal %d — shutting down", signum)
    _running = False

    with _lock:
        connected = _state["connected"]

    if connected:
        log.info("Reverting display resolution before exit")
        ok = display.disconnect()
        with _lock:
            if ok:
                _state["connected"] = False

    _cleanup()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _server, _fifo_in, _fifo_out
    
    _fifo_in = None
    _fifo_out = None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )

    if os.geteuid() != 0:
        log.error("Daemon must be run as root")
        sys.exit(1)

    target_user = "deck"
    if len(sys.argv) >= 3 and sys.argv[1] == "--user":
        target_user = sys.argv[2]
        
    import pwd
    try:
        target_home = pwd.getpwnam(target_user).pw_dir
    except KeyError:
        target_home = f"/home/{target_user}"
        
    _fifo_in = "/root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.in"
    _fifo_out = "/root/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/.sunshine-res-helper.out"
    
    display.target_user = target_user

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Recover from ungraceful shutdown/reboot:
    current_boot_id = display._get_boot_id()
    sunshine_pid = _get_sunshine_pid()

    def _is_stale(state_file: Path, boot_id_idx: int) -> bool:
        if not state_file.exists():
            return False
        lines = state_file.read_text().strip().split("\n")
        saved_boot_id = lines[boot_id_idx] if len(lines) > boot_id_idx else ""
        if current_boot_id and saved_boot_id and saved_boot_id != current_boot_id:
            return True  # From a previous boot
        return not sunshine_pid  # From this boot, but Sunshine crashed/stopped

    stale_res = _is_stale(display.STEAM_RES_STATE_FILE, 1)
    stale_fc = _is_stale(display.FC_STATE_FILE, 1)
    
    if stale_res or stale_fc:
        log.info("Found stale Steam settings (likely from a previous boot/crash). Waiting for Steam to be ready to restore...")
        
        for attempt in range(15):
            display._restore_steam_settings()
            
            needs_res = stale_res and display.STEAM_RES_STATE_FILE.exists()
            needs_fc = stale_fc and display.FC_STATE_FILE.exists()
            
            if not needs_res and not needs_fc:
                log.info("Successfully restored stale Steam settings on boot.")
                break
                
            log.info("Steam might not be ready yet. Retrying in 20 seconds...")
            time.sleep(20)
        else:
            log.error("Failed to restore all stale Steam settings after 5 minutes. Giving up.")

    stale_virt = display.SCRIPT_DIR / "virt_display.state"
    if _is_stale(stale_virt, 4):
        log.info("Cleaning up stale virtual display state file...")
        stale_virt.unlink()

    sleep_thread = threading.Thread(target=_dbus_listener, daemon=True, name="dbus-listener")
    sleep_thread.start()
    
    log_thread = threading.Thread(
        target=_watch_sunshine_logs, args=(target_home,), daemon=True, name="log-watcher"
    )
    log_thread.start()

    try:
        os.remove(_fifo_in)
    except FileNotFoundError:
        pass
    try:
        os.remove(_fifo_out)
    except FileNotFoundError:
        pass

    os.mkfifo(_fifo_in)
    os.chmod(_fifo_in, 0o666)
    os.mkfifo(_fifo_out)
    os.chmod(_fifo_out, 0o666)

    log.info("Daemon listening on %s (out: %s)", _fifo_in, _fifo_out)

    fd_in = os.open(_fifo_in, os.O_RDONLY | os.O_NONBLOCK)
    dummy_fd = os.open(_fifo_in, os.O_WRONLY)

    while _running:
        ready, _, _ = select.select([fd_in], [], [], 1.0)
        if not ready:
            continue
        
        try:
            data = os.read(fd_in, 256)
            if data:
                args = data.decode("utf-8").strip().split(",")
                log.info("Received command: %s", args)
                _handle_command(args)
                
                # Unblock the client by writing to FIFO_OUT
                try:
                    # Retry opening for up to 1 second in case the reader hasn't started yet
                    # O_NONBLOCK prevents us from blocking forever if the reader never starts
                    out_fd = -1
                    for _ in range(10):
                        try:
                            out_fd = os.open(_fifo_out, os.O_WRONLY | os.O_NONBLOCK)
                            break
                        except OSError as e:
                            if e.errno == 6:  # ENXIO (no reader)
                                time.sleep(0.1)
                            else:
                                raise
                    
                    if out_fd != -1:
                        os.write(out_fd, b"OK\n")
                        os.close(out_fd)
                    else:
                        log.warning("No reader attached to out fifo, skipping response")
                except OSError as e:
                    log.warning("Could not write to out fifo: %s", e)
        except Exception as exc:
            log.error("Error reading from fifo: %s", exc)
            
    os.close(dummy_fd)
    os.close(fd_in)


if __name__ == "__main__":
    main()
