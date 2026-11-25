"""
Multi-Device Auto-Reconnect Watchdog Engine for ADB Phone Control.
Combines:
  1. Per-Device Keep-Alive Heartbeat (keeps ALL connected Wi-Fi phones awake)
  2. Live mDNS / Zeroconf Discovery (`_adb-tls-connect._tcp.local.`) tracked per IP
  3. LAN UDP Beacon Listener (port 45454) tracked per phone IP (never overwrites other phones)
  4. Fast parallel `/24` Subnet Scanner for port 5555 (~1.2s for 254 IPs)
  5. Safe Active-Device Preservation (reconnecting Phone B never steals focus from Phone A)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from main import ADB

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconnect_state.json")


def _get_local_subnet_prefix() -> Optional[str]:
    """Detects the PC's active LAN IPv4 /24 prefix (e.g. '192.168.0.')."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        parts = ip.split(".")
        if len(parts) == 4:
            return ".".join(parts[:3]) + "."
    except Exception:
        pass
    return None


def scan_subnet_port(port: int = 5555, timeout: float = 0.9) -> List[str]:
    """Fast parallel TCP scan of local /24 subnet for open ADB port (tuned for weak Wi-Fi too)."""
    prefix = _get_local_subnet_prefix()
    if not prefix:
        return []
    found: List[str] = []

    def probe(host_num: int) -> Optional[str]:
        ip = f"{prefix}{host_num}"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if sock.connect_ex((ip, port)) == 0:
                return f"{ip}:{port}"
        except Exception:
            pass
        finally:
            sock.close()
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(probe, range(1, 255)):
            if res:
                found.append(res)
    return found


def tcp_port_reachable(target: str, timeout: float = 1.8) -> bool:
    """Quick TCP reachability check before running slow ADB TLS handshake on weak Wi-Fi."""
    if not target or ":" not in target:
        return False
    host, port_s = target.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host.strip(), port)) == 0
    except Exception:
        return False
    finally:
        sock.close()


class AutoReconnectWatchdog:
    """Multi-device background watchdog: keeps all phones alive & auto-reconnects dropped phones."""

    def __init__(
        self,
        adb: ADB,
        on_event: Optional[Callable[[str, str], None]] = None,
        on_reconnected: Optional[Callable[[str], None]] = None,
        heartbeat_sec: float = 15.0,
        state_path: str = STATE_FILE,
    ):
        self.adb = adb
        self.on_event = on_event
        self.on_reconnected = on_reconnected
        self.heartbeat_sec = max(5.0, float(heartbeat_sec))
        self.state_path = state_path

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._udp_thread: Optional[threading.Thread] = None
        self._udp_sock: Optional[socket.socket] = None
        self._zc = None
        self._mdns_by_ip: Dict[str, str] = {}
        self._last_attempt_ts: Dict[str, float] = {}
        self._manual_disconnected_ips: Set[str] = set()
        self._lock = threading.Lock()
        self._running = False
        self._keep_alive = True
        self.status_text: str = "Idle"
        self.state: Dict[str, Any] = self._load_state()

    @property
    def is_running(self) -> bool:
        return self._running

    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return {"devices": {}}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"devices": {}}
            devices = data.get("devices")
            if not isinstance(devices, dict):
                devices = {}
            # Migrate legacy single-device format cleanly
            old_ip = str(data.get("last_ip") or "").strip()
            old_serial = str(data.get("last_serial") or "").strip()
            if old_ip and old_ip not in devices:
                devices[old_ip] = {
                    "ip": old_ip,
                    "last_serial": old_serial if old_serial.startswith(f"{old_ip}:") else f"{old_ip}:5555",
                    "model": "",
                    "last_seen": time.time(),
                }
            data["devices"] = devices
            return data
        except Exception:
            return {"devices": {}}

    def _save_state(self) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _emit(self, status: str, log: str = "") -> None:
        self.status_text = status
        if self.on_event:
            self.on_event(status, log or status)

    def mark_manual_disconnect(self, serial: str) -> None:
        """Prevents watchdog from immediately reconnecting a phone the user intentionally disconnected."""
        if serial and ":" in serial:
            ip = serial.split(":", 1)[0].strip()
            if ip:
                with self._lock:
                    self._manual_disconnected_ips.add(ip)

    def clear_manual_disconnect(self, target: str) -> None:
        if target and ":" in target:
            ip = target.split(":", 1)[0].strip()
            with self._lock:
                self._manual_disconnected_ips.discard(ip)

    def remember_device(self, serial: str, wifi_ip: str = "", model: str = "") -> None:
        ip = (wifi_ip or "").strip()
        if not ip and serial and ":" in serial:
            ip = serial.split(":", 1)[0].strip()
        if not ip:
            return

        with self._lock:
            devices: Dict[str, dict] = self.state.setdefault("devices", {})
            entry = devices.get(ip)
            changed = False
            if not isinstance(entry, dict):
                entry = {"ip": ip, "last_serial": "", "model": "", "last_seen": 0}
                devices[ip] = entry
                changed = True
            if serial and ":" in serial and entry.get("last_serial") != serial:
                entry["last_serial"] = serial
                changed = True
            if model and entry.get("model") != model:
                entry["model"] = model
                changed = True
            entry["last_seen"] = round(time.time(), 1)
            # Keep last_ip/last_serial mirrored for legacy compatibility
            if self.state.get("last_ip") != ip:
                self.state["last_ip"] = ip
                changed = True
            if serial and ":" in serial and self.state.get("last_serial") != serial:
                self.state["last_serial"] = serial
                changed = True
            if changed:
                self._save_state()

    def start(self, keep_alive: bool = True) -> str:
        if self._running:
            return "Watchdog ইতিমধ্যেই চালু আছে।"
        self._stop_event.clear()
        with self._lock:
            # User explicitly enabled Watchdog → clear manual-disconnect blocks & cooldowns so it reconnects immediately
            self._manual_disconnected_ips.clear()
            self._last_attempt_ts.clear()
        self._running = True
        self._keep_alive = bool(keep_alive)
        self._start_mdns()
        self._start_udp_beacon_listener()

        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        mdns_lbl = "mDNS + UDP + 5555" if self._zc is not None else "UDP + Port 5555"
        msg = f"🛡️ Multi-Device Watchdog চালু ({mdns_lbl}" + (" + Heartbeat)" if keep_alive else ")")
        self._emit(msg, msg)
        return msg

    def stop(self) -> str:
        self._running = False
        self._stop_event.set()
        self._stop_mdns()
        if self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None
        self._emit("Idle", "⏹️ Auto-Reconnect Watchdog বন্ধ করা হয়েছে।")
        return "⏹️ Watchdog বন্ধ করা হয়েছে।"

    @staticmethod
    def _online_serials() -> List[str]:
        try:
            devs = ADB.list_devices()
            return [d["serial"] for d in devs if d.get("state") == "device"]
        except Exception:
            return []

    @staticmethod
    def _is_ip_online(ip: str, online_serials: List[str]) -> bool:
        prefix = f"{ip}:"
        return any(s.startswith(prefix) for s in online_serials)

    def _can_attempt_ip(self, ip: str, cooldown_sec: float = 10.0) -> bool:
        now = time.monotonic()
        with self._lock:
            if ip in self._manual_disconnected_ips:
                return False
            last = self._last_attempt_ts.get(ip, 0.0)
            if (now - last) < cooldown_sec:
                return False
            self._last_attempt_ts[ip] = now
            return True

    def _start_udp_beacon_listener(self, port: int = 45454) -> None:
        """Listens for Phone Companion UDP beacons from ALL phones on the LAN."""
        def loop():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", port))
                sock.settimeout(1.5)
                self._udp_sock = sock
            except Exception:
                return
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(1024)
                    payload = json.loads(data.decode("utf-8", errors="ignore"))
                    if payload.get("app") != "adb_companion":
                        continue
                    phone_ip = (payload.get("ip") or addr[0] or "").strip()
                    model = str(payload.get("model") or "").strip()
                    if not phone_ip:
                        continue
                    self.remember_device("", wifi_ip=phone_ip, model=model)
                    online = self._online_serials()
                    if not self._is_ip_online(phone_ip, online) and self._can_attempt_ip(phone_ip, cooldown_sec=10.0):
                        # Prefer latest mDNS target for this IP if available, else port 5555
                        with self._lock:
                            mdns_target = self._mdns_by_ip.get(phone_ip)
                        src = f"UDP Beacon ({model})" if model else "UDP Beacon"
                        if mdns_target and self._try_connect_target(mdns_target, source=src):
                            continue
                        self._try_connect_target(f"{phone_ip}:5555", source=src)
                except socket.timeout:
                    continue
                except Exception:
                    if self._stop_event.is_set():
                        break
            try:
                sock.close()
            except Exception:
                pass

        self._udp_thread = threading.Thread(target=loop, daemon=True)
        self._udp_thread.start()

    def _start_mdns(self) -> None:
        self._stop_mdns()
        try:
            from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

            zc = Zeroconf()
            self._zc = zc

            def on_service(zeroconf, service_type, name, state_change):
                if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
                    return
                if self._stop_event.is_set():
                    return
                try:
                    info = zeroconf.get_service_info(service_type, name, timeout=5000)
                    if not info or not info.parsed_addresses():
                        return
                    ip = info.parsed_addresses()[0]
                    target = f"{ip}:{info.port}"
                    with self._lock:
                        prev_target = self._mdns_by_ip.get(ip)
                        self._mdns_by_ip[ip] = target
                        is_manual_off = ip in self._manual_disconnected_ips
                    self.remember_device(target, wifi_ip=ip)
                    if is_manual_off:
                        return
                    # Reconnect THIS specific phone immediately when a fresh mDNS target arrives
                    online = self._online_serials()
                    if not self._is_ip_online(ip, online):
                        if target != prev_target or self._can_attempt_ip(ip, cooldown_sec=4.0):
                            self._try_connect_target(target, source="mDNS")
                except Exception:
                    pass

            ServiceBrowser(zc, "_adb-tls-connect._tcp.local.", handlers=[on_service])
        except Exception:
            self._zc = None

    def _stop_mdns(self) -> None:
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None

    @staticmethod
    def _clear_offline_zombie(target: str) -> bool:
        """Disconnects a stuck `offline` Wi-Fi ADB socket so a fresh reconnect can succeed on weak Wi-Fi."""
        try:
            devs = ADB.list_devices()
            ip_prefix = target.split(":", 1)[0] + ":"
            cleared = False
            for d in devs:
                s = d.get("serial", "")
                st = d.get("state", "")
                if (s == target or s.startswith(ip_prefix)) and st == "offline":
                    ADB.run_global(["disconnect", s], check=False, timeout=6)
                    cleared = True
            if cleared:
                time.sleep(0.5)
            return cleared
        except Exception:
            return False

    def _try_connect_target(self, target: str, source: str = "Auto", retries: int = 2) -> bool:
        """Weak-Wi-Fi resilient connect: clears zombie `offline` sockets + adaptive timeouts (12s → 16s)."""
        if not target or ":" not in target:
            return False
        # Clear any stuck zombie `offline` session for this phone first
        self._clear_offline_zombie(target)

        timeouts = (12, 16)
        attempts = max(1, min(int(retries), len(timeouts)))
        for idx in range(attempts):
            if self._stop_event.is_set():
                return False
            try:
                out = ADB.run_global(["connect", target], check=False, timeout=timeouts[idx])
                low = (out or "").lower()
                if "connected to" in low or "already connected" in low:
                    # Verify it is genuinely `device` (not stuck in zombie `offline` state!)
                    online = self._online_serials()
                    if target in online or self._is_ip_online(target.split(":", 1)[0], online):
                        self.remember_device(target)
                        if not self.adb.serial or self.adb.serial not in online:
                            self.adb.serial = target
                        msg = f"🟢 Reconnected ({source}): {target}"
                        self._emit(msg, msg)
                        if self.on_reconnected:
                            self.on_reconnected(target)
                        return True
                    # `already connected` but still `offline` → force disconnect & retry
                    self._clear_offline_zombie(target)
            except Exception:
                pass
            if idx + 1 < attempts and not self._stop_event.is_set():
                self._stop_event.wait(1.0)
        return False

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                devs = ADB.list_devices()
                online = [d["serial"] for d in devs if d.get("state") == "device"]
                offline_wifi = [d["serial"] for d in devs if d.get("state") == "offline" and ":" in d.get("serial", "")]

                # 0. Auto-heal zombie `offline` Wi-Fi sessions caused by weak signal drops
                for off_s in offline_wifi:
                    if self._stop_event.is_set():
                        break
                    off_ip = off_s.split(":", 1)[0]
                    # If this same phone IP is ALREADY online on another port (e.g. fixed :5555), just drop the stale offline port!
                    if self._is_ip_online(off_ip, online):
                        ADB.run_global(["disconnect", off_s], check=False, timeout=5)
                        continue
                    with self._lock:
                        is_manual_off = off_ip in self._manual_disconnected_ips
                        mdns_target = self._mdns_by_ip.get(off_ip)
                    if is_manual_off:
                        continue
                    self._emit(f"⚠️ Weak Wi-Fi offline ({off_s}) — রিকভার চেষ্টা চলছে...")
                    # Prefer fixed :5555 if reachable, else mDNS target, else the offline target itself
                    target = f"{off_ip}:5555" if tcp_port_reachable(f"{off_ip}:5555", timeout=1.2) else (mdns_target or off_s)
                    if self._try_connect_target(target, source="Offline-Heal", retries=2):
                        online = self._online_serials()

                # 1. Keep-alive heartbeat & IP discovery for EVERY connected device (pinned ADB per serial)
                for s in online:
                    if self._stop_event.is_set():
                        break
                    pinned = ADB(serial=s, timeout=8)
                    if self._keep_alive:
                        pinned.shell("true", check=False, timeout=8)
                    if ":" in s:
                        self.remember_device(s)
                        self.clear_manual_disconnect(s)
                    else:
                        ip_raw = pinned.shell("ip route | grep wlan0", check=False, timeout=6)
                        m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_raw)
                        if m:
                            self.remember_device(s, wifi_ip=m.group(1))

                # Auto-select first online device ONLY if nothing is selected
                if not self.adb.serial and online:
                    self.adb.serial = online[0]
                    if self.on_reconnected:
                        self.on_reconnected(online[0])

                # 2. Check for any known/mDNS phones that dropped offline & reconnect them individually
                with self._lock:
                    known_devices = dict(self.state.get("devices", {}))
                    mdns_map = dict(self._mdns_by_ip)
                    manual_off = set(self._manual_disconnected_ips)

                all_known_ips = set(known_devices.keys()) | set(mdns_map.keys())
                for ip in all_known_ips:
                    if self._stop_event.is_set():
                        break
                    if ip in manual_off or self._is_ip_online(ip, online):
                        continue
                    if not self._can_attempt_ip(ip, cooldown_sec=max(12.0, self.heartbeat_sec)):
                        continue
                    candidates: List[str] = []
                    if ip in mdns_map:
                        candidates.append(mdns_map[ip])
                    entry = known_devices.get(ip) or {}
                    last_s = str(entry.get("last_serial") or "").strip()
                    if last_s and last_s not in candidates:
                        candidates.append(last_s)
                    p5555 = f"{ip}:5555"
                    if p5555 not in candidates:
                        candidates.append(p5555)

                    for t in candidates:
                        if self._stop_event.is_set():
                            break
                        if not tcp_port_reachable(t, timeout=1.8):
                            continue
                        if self._try_connect_target(t, source="Watchdog", retries=2):
                            online = self._online_serials()
                            break

                # 3. Update multi-device status text
                if len(online) > 1:
                    self.status_text = f"🟢 Connected ({len(online)} devices) • Watching"
                elif len(online) == 1:
                    self.status_text = f"🟢 Connected ({online[0]}) • Watching"
                else:
                    self.status_text = "🔍 Searching for phones (mDNS / UDP / 5555)..."
            except Exception:
                pass
            self._stop_event.wait(self.heartbeat_sec)
        self._running = False
