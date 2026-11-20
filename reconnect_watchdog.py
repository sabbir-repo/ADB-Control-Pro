"""
Auto-Reconnect Watchdog Engine for ADB Phone Control.
Combines:
  1. Keep-Alive Heartbeat (prevents Wi-Fi sleep drops & detects disconnects quickly)
  2. Live mDNS / Zeroconf Discovery (`_adb-tls-connect._tcp.local.`) for Android 11+ random ports
  3. Last-known IP + Fixed Port 5555 auto-reconnect
  4. Fast parallel `/24` Subnet Scanner for port 5555 (~1.2s for 254 IPs)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import socket
import threading
import time
from typing import Callable, Dict, List, Optional

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


def scan_subnet_port(port: int = 5555, timeout: float = 0.35) -> List[str]:
    """Fast parallel TCP scan of local /24 subnet for open ADB port (typically ~1.2s)."""
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


class AutoReconnectWatchdog:
    """Background watchdog that keeps ADB alive and auto-reconnects via mDNS + Port 5555."""

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
        self._zc = None
        self._mdns_targets: List[str] = []
        self._lock = threading.Lock()
        self._running = False
        self.status_text: str = "Idle"
        self.state: Dict[str, str] = self._load_state()

    @property
    def is_running(self) -> bool:
        return self._running

    def _load_state(self) -> Dict[str, str]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

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

    def remember_device(self, serial: str, wifi_ip: str = "") -> None:
        changed = False
        if serial and ":" in serial:
            if self.state.get("last_serial") != serial:
                self.state["last_serial"] = serial
                changed = True
            ip_part = serial.split(":", 1)[0]
            if ip_part and self.state.get("last_ip") != ip_part:
                self.state["last_ip"] = ip_part
                changed = True
        if wifi_ip and self.state.get("last_ip") != wifi_ip:
            self.state["last_ip"] = wifi_ip
            changed = True
        if changed:
            self._save_state()

    def start(self, keep_alive: bool = True) -> str:
        if self._running:
            return "Watchdog ইতিমধ্যেই চালু আছে।"
        self._stop_event.clear()
        self._running = True
        self._keep_alive = bool(keep_alive)
        self._start_mdns()

        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        mdns_lbl = "mDNS + 5555" if self._zc is not None else "Port 5555"
        msg = f"🛡️ Watchdog চালু ({mdns_lbl}" + (" + Heartbeat)" if keep_alive else ")")
        self._emit(msg, msg)
        return msg

    def stop(self) -> str:
        self._running = False
        self._stop_event.set()
        self._stop_mdns()
        self._emit("Idle", "⏹️ Auto-Reconnect Watchdog বন্ধ করা হয়েছে।")
        return "⏹️ Watchdog বন্ধ করা হয়েছে।"

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
                    info = zeroconf.get_service_info(service_type, name, timeout=2500)
                    if not info or not info.parsed_addresses():
                        return
                    target = f"{info.parsed_addresses()[0]}:{info.port}"
                    with self._lock:
                        if target not in self._mdns_targets:
                            self._mdns_targets.insert(0, target)
                            self._mdns_targets = self._mdns_targets[:6]
                    # If currently disconnected, reconnect immediately!
                    if not self._is_current_online():
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

    def _is_current_online(self) -> bool:
        try:
            devs = ADB.list_devices()
            online = [d["serial"] for d in devs if d.get("state") == "device"]
            if not online:
                return False
            if self.adb.serial in online:
                return True
            # Auto-switch to an online device if current serial dropped
            self.adb.serial = online[0]
            self.remember_device(online[0])
            return True
        except Exception:
            return False

    def _try_connect_target(self, target: str, source: str = "Auto") -> bool:
        if not target or ":" not in target:
            return False
        try:
            out = self.adb.run(["connect", target], check=False, timeout=6)
            low = (out or "").lower()
            if "connected to" in low or "already connected" in low:
                self.adb.serial = target
                self.remember_device(target)
                msg = f"🟢 Reconnected ({source}): {target}"
                self._emit(msg, msg)
                if self.on_reconnected:
                    self.on_reconnected(target)
                return True
        except Exception:
            pass
        return False

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._is_current_online():
                    if self._keep_alive and self.adb.serial:
                        # Lightweight heartbeat prevents HiOS/MIUI Wi-Fi sleep drops
                        self.adb.shell("true", check=False, timeout=4)
                        if ":" not in self.adb.serial:
                            ip_raw = self.adb.shell("ip route | grep wlan0", check=False, timeout=4)
                            m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_raw)
                            if m:
                                self.remember_device(self.adb.serial, wifi_ip=m.group(1))
                        else:
                            self.remember_device(self.adb.serial)
                    self.status_text = f"🟢 Connected ({self.adb.serial}) • Watching"
                else:
                    self._emit("⚠️ Disconnected — রিকানেক্ট চেষ্টা চলছে...")
                    reconnected = False
                    with self._lock:
                        candidates = list(self._mdns_targets)
                    last_ip = self.state.get("last_ip", "")
                    last_serial = self.state.get("last_serial", "")
                    if last_ip:
                        candidates.append(f"{last_ip}:5555")
                    if last_serial and last_serial not in candidates:
                        candidates.append(last_serial)

                    seen = set()
                    for t in candidates:
                        if self._stop_event.is_set():
                            break
                        if t in seen:
                            continue
                        seen.add(t)
                        if self._try_connect_target(t, source="Watchdog"):
                            reconnected = True
                            break

                    if not reconnected and not self._stop_event.is_set():
                        self.status_text = "🔍 Searching for phone (mDNS / 5555)..."
            except Exception:
                pass
            self._stop_event.wait(self.heartbeat_sec)
        self._running = False
