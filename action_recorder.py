"""
Physical Action Recorder for Android via ADB (non-root).
Streams real-time Linux input events via `adb shell getevent -lt`, calibrates
raw touchscreen coordinates to screen pixels, classifies Tap / Long Press /
Swipe / Multi-Touch / Hardware Keys, tracks App switches, and optionally
inspects tapped UI elements via `uiautomator`.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from main import ADB


@dataclass
class TouchCalibration:
    screen_w: int = 1080
    screen_h: int = 2400
    max_x: int = 1080
    max_y: int = 2400
    min_x: int = 0
    min_y: int = 0
    touch_device: str = ""
    nav_mode: int = 0  # 0 = 3-button nav bar, 2 = full-screen gesture nav
    nav_buttons: Dict[str, Tuple[int, int, int, int]] = field(default_factory=dict)

    def to_screen(self, raw_x: int, raw_y: int) -> Tuple[int, int]:
        span_x = max(1, self.max_x - self.min_x)
        span_y = max(1, self.max_y - self.min_y)
        px = int(round((raw_x - self.min_x) * self.screen_w / span_x))
        py = int(round((raw_y - self.min_y) * self.screen_h / span_y))
        px = max(0, min(self.screen_w, px))
        py = max(0, min(self.screen_h, py))
        return px, py


def get_touch_calibration(adb: ADB) -> TouchCalibration:
    """Detects screen resolution (`wm size`) and touchscreen axis bounds (`getevent -p`)."""
    cal = TouchCalibration()

    # 1. Screen resolution (prefer Override size if present)
    wm = adb.shell("wm size", check=False, timeout=6)
    m_ov = re.search(r"Override size:\s*(\d+)x(\d+)", wm)
    m_ph = re.search(r"Physical size:\s*(\d+)x(\d+)", wm)
    m = m_ov or m_ph
    if m:
        cal.screen_w, cal.screen_h = int(m.group(1)), int(m.group(2))
        cal.max_x, cal.max_y = cal.screen_w, cal.screen_h

    # 2. Parse `getevent -p` to find touchscreen device and ABS_MT_POSITION_X/Y max
    raw_p = adb.shell("getevent -p", check=False, timeout=8)
    cur_dev = ""
    cur_min_x = cur_max_x = cur_min_y = cur_max_y = None
    for line in raw_p.splitlines():
        dev_m = re.match(r"add device \d+:\s*(/dev/input/event\d+)", line.strip())
        if dev_m:
            if cur_dev and cur_max_x and cur_max_y:
                cal.touch_device = cur_dev
                cal.min_x, cal.max_x = cur_min_x or 0, cur_max_x
                cal.min_y, cal.max_y = cur_min_y or 0, cur_max_y
                break
            cur_dev = dev_m.group(1)
            cur_min_x = cur_max_x = cur_min_y = cur_max_y = None
            continue

        # 0035 = ABS_MT_POSITION_X, 0036 = ABS_MT_POSITION_Y
        if "0035" in line or "ABS_MT_POSITION_X" in line:
            mm = re.search(r"min\s+(-?\d+),\s*max\s+(\d+)", line)
            if mm:
                cur_min_x, cur_max_x = int(mm.group(1)), int(mm.group(2))
        elif "0036" in line or "ABS_MT_POSITION_Y" in line:
            mm = re.search(r"min\s+(-?\d+),\s*max\s+(\d+)", line)
            if mm:
                cur_min_y, cur_max_y = int(mm.group(1)), int(mm.group(2))

    if cur_dev and cur_max_x and cur_max_y and not cal.touch_device:
        cal.touch_device = cur_dev
        cal.min_x, cal.max_x = cur_min_x or 0, cur_max_x
        cal.min_y, cal.max_y = cur_min_y or 0, cur_max_y

    # 3. Detect navigation mode (0 = 3-button, 2 = gesture) & 3-button bounds
    nav_raw = adb.shell("settings get secure navigation_mode", check=False, timeout=5).strip()
    if nav_raw.isdigit():
        cal.nav_mode = int(nav_raw)

    if cal.nav_mode != 2:
        root = _dump_ui_xml(adb)
        if root is not None:
            for node in root.iter("node"):
                rid = (node.attrib.get("resource-id") or "").lower()
                desc = (node.attrib.get("content-desc") or "").lower()
                b = node.attrib.get("bounds", "")
                nums = list(map(int, re.findall(r"\d+", b)))
                if len(nums) != 4:
                    continue
                if nums[1] < int(cal.screen_h * 0.85):
                    continue
                if rid.endswith(":id/back") or desc in ("back", "navigate up"):
                    cal.nav_buttons["back"] = tuple(nums)  # type: ignore
                elif rid.endswith(":id/home") or desc == "home":
                    cal.nav_buttons["home"] = tuple(nums)  # type: ignore
                elif rid.endswith(":id/recent_apps") or rid.endswith(":id/overview") or "recent" in desc or "overview" in desc:
                    cal.nav_buttons["recents"] = tuple(nums)  # type: ignore

    return cal


def _dump_ui_xml(adb: ADB) -> Optional[ET.Element]:
    remote = "/data/local/tmp/__adb_rec_ui.xml"
    adb.shell(f"uiautomator dump {remote}", check=False, timeout=8)
    raw = adb.shell(f"cat {remote}", check=False, timeout=6)
    adb.shell(f"rm -f {remote}", check=False, timeout=4)
    if not raw or not raw.strip().startswith("<"):
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def find_element_at(adb: ADB, x: int, y: int) -> Optional[dict]:
    """Returns metadata for the tightest UI node containing (x, y)."""
    root = _dump_ui_xml(adb)
    if root is None:
        return None
    best = None
    best_area = 10**12
    for node in root.iter("node"):
        b = node.attrib.get("bounds", "")
        nums = list(map(int, re.findall(r"\d+", b)))
        if len(nums) != 4:
            continue
        x1, y1, x2, y2 = nums
        if x1 <= x <= x2 and y1 <= y <= y2:
            area = max(1, (x2 - x1) * (y2 - y1))
            text = (node.attrib.get("text") or "").strip()
            desc = (node.attrib.get("content-desc") or "").strip()
            rid = (node.attrib.get("resource-id") or "").strip()
            cls = (node.attrib.get("class") or "").split(".")[-1]
            # Prefer smallest node; slightly prefer nodes that have text/desc/id
            effective_area = area if (text or desc or rid) else area * 3
            if effective_area < best_area:
                best_area = effective_area
                best = {
                    "text": text,
                    "desc": desc,
                    "resource_id": rid,
                    "class": cls,
                    "bounds": (x1, y1, x2, y2),
                }
    return best


def find_element_by_query(adb: ADB, query: str) -> Optional[Tuple[int, int, str]]:
    """Finds center (cx, cy) of a UI element matching text, content-desc, or resource-id."""
    q = (query or "").strip().lower()
    if not q:
        return None
    root = _dump_ui_xml(adb)
    if root is None:
        return None
    # Exact match first, then substring match
    candidates: List[Tuple[int, int, int, str]] = []
    for node in root.iter("node"):
        b = node.attrib.get("bounds", "")
        nums = list(map(int, re.findall(r"\d+", b)))
        if len(nums) != 4:
            continue
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        rid = (node.attrib.get("resource-id") or "").strip()
        fields = [text, desc, rid]
        priority = None
        matched_label = text or desc or rid
        for f in fields:
            fl = f.lower()
            if not fl:
                continue
            if fl == q:
                priority = 0
                break
            if q in fl:
                priority = 1 if priority is None else min(priority, 1)
        if priority is not None:
            cx = (nums[0] + nums[2]) // 2
            cy = (nums[1] + nums[3]) // 2
            candidates.append((priority, cx, cy, matched_label))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    _, cx, cy, label = candidates[0]
    return cx, cy, label


# Hardware key name -> Android keycode
HW_KEY_MAP: Dict[str, str] = {
    "KEY_VOLUMEUP": "KEYCODE_VOLUME_UP",
    "KEY_VOLUMEDOWN": "KEYCODE_VOLUME_DOWN",
    "KEY_POWER": "KEYCODE_POWER",
    "KEY_BACK": "KEYCODE_BACK",
    "KEY_HOMEPAGE": "KEYCODE_HOME",
    "KEY_HOME": "KEYCODE_HOME",
    "KEY_CAMERA": "KEYCODE_CAMERA",
}

EVENT_LINE_RE = re.compile(
    r"^\[\s*([\d.]+)\]\s+(?:(/dev/input/event\d+):\s+)?(\S+)\s+(\S+)\s+(\S+)"
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class GestureStateMachine:
    """Pure state-machine that parses `getevent -lt` lines (Protocol A & B) into structured actions."""

    def __init__(self, calibration: TouchCalibration, tap_slop_px: int = 32, long_press_ms: int = 500):
        self.cal = calibration
        self.tap_slop_px = tap_slop_px
        self.long_press_ms = long_press_ms

        self._cur_slot = 0
        self._slots: Dict[int, dict] = {}
        self._max_fingers = 0
        self._touch_active = False
        self._gesture_start_ts: Optional[float] = None
        self._frame_had_mt_point = False
        self._saw_mt_report = False

    def feed_line(self, line: str) -> List[dict]:
        """Feeds one line of `getevent -lt` and returns 0 or more completed action dicts."""
        clean = ANSI_RE.sub("", line).strip()
        m = EVENT_LINE_RE.match(clean)
        if not m:
            return []
        ts = float(m.group(1))
        _dev, ev_type, ev_code, ev_val = m.group(2) or "", m.group(3), m.group(4), m.group(5)
        out: List[dict] = []

        # Hardware key press (record on DOWN / 00000001)
        if ev_type in ("EV_KEY", "0001") and ev_code in HW_KEY_MAP and ev_val in ("DOWN", "00000001", "1"):
            out.append({"action": "keyevent", "arg": HW_KEY_MAP[ev_code], "note": f"Hardware {ev_code}", "ts": ts})
            return out

        # BTN_TOUCH (Protocol A & many Protocol B drivers)
        if ev_type in ("EV_KEY", "0001") and ev_code in ("BTN_TOUCH", "014a"):
            if ev_val in ("DOWN", "00000001", "1"):
                self._touch_active = True
                if self._gesture_start_ts is None:
                    self._gesture_start_ts = ts
            elif ev_val in ("UP", "00000000", "0"):
                ev = self._finalize_gesture(ts)
                if ev:
                    out.append(ev)
            return out

        if ev_type in ("EV_ABS", "0003"):
            try:
                val = int(ev_val, 16)
            except ValueError:
                return out

            if ev_code in ("ABS_MT_SLOT", "002f"):
                self._cur_slot = val
            elif ev_code in ("ABS_MT_TRACKING_ID", "0039"):
                if ev_val.lower() == "ffffffff" or val >= 0xFFFFFFFF:
                    # Finger lifted from current slot
                    if self._cur_slot in self._slots:
                        self._slots[self._cur_slot]["up_ts"] = ts
                        self._slots[self._cur_slot]["active"] = False
                else:
                    self._touch_active = True
                    if self._gesture_start_ts is None:
                        self._gesture_start_ts = ts
                    self._slots[self._cur_slot] = {
                        "active": True,
                        "start_x": None,
                        "start_y": None,
                        "last_x": None,
                        "last_y": None,
                        "down_ts": ts,
                        "move_start_ts": None,
                        "up_ts": ts,
                    }
                    active_cnt = sum(1 for s in self._slots.values() if s["active"])
                    self._max_fingers = max(self._max_fingers, active_cnt)
            elif ev_code in ("ABS_MT_POSITION_X", "0035", "ABS_X", "0000"):
                slot = self._ensure_slot(ts)
                if slot["start_x"] is None:
                    slot["start_x"] = val
                elif slot["move_start_ts"] is None and abs(val - slot["start_x"]) >= 8:
                    slot["move_start_ts"] = ts
                slot["last_x"] = val
                self._frame_had_mt_point = True
            elif ev_code in ("ABS_MT_POSITION_Y", "0036", "ABS_Y", "0001"):
                slot = self._ensure_slot(ts)
                if slot["start_y"] is None:
                    slot["start_y"] = val
                elif slot["move_start_ts"] is None and abs(val - slot["start_y"]) >= 8:
                    slot["move_start_ts"] = ts
                slot["last_y"] = val
                self._frame_had_mt_point = True
            return out

        if ev_type in ("EV_SYN", "0000"):
            if ev_code in ("SYN_MT_REPORT", "0002"):
                self._saw_mt_report = True
                return out
            if ev_code in ("SYN_REPORT", "0000"):
                # Protocol B completion: all tracked slots released
                if self._touch_active and self._slots and not any(s["active"] for s in self._slots.values()):
                    ev = self._finalize_gesture(ts)
                    if ev:
                        out.append(ev)
                # Protocol A completion: empty SYN_MT_REPORT frame (finger lifted)
                elif self._touch_active and self._saw_mt_report and not self._frame_had_mt_point:
                    ev = self._finalize_gesture(ts)
                    if ev:
                        out.append(ev)
                self._frame_had_mt_point = False
                self._saw_mt_report = False
        return out

    def _ensure_slot(self, ts: float) -> dict:
        if self._cur_slot not in self._slots:
            self._touch_active = True
            if self._gesture_start_ts is None:
                self._gesture_start_ts = ts
            self._slots[self._cur_slot] = {
                "active": True,
                "start_x": None,
                "start_y": None,
                "last_x": None,
                "last_y": None,
                "down_ts": ts,
                "move_start_ts": None,
                "up_ts": ts,
            }
            self._max_fingers = max(self._max_fingers, 1)
        return self._slots[self._cur_slot]

    def _finalize_gesture(self, end_ts: float) -> Optional[dict]:
        slots = self._slots
        max_f = self._max_fingers
        start_ts = self._gesture_start_ts or end_ts

        self._slots = {}
        self._cur_slot = 0
        self._max_fingers = 0
        self._touch_active = False
        self._gesture_start_ts = None

        if not slots:
            return None

        # Pick primary finger (slot 0 or first valid slot)
        primary = slots.get(0) or next(iter(slots.values()))
        if primary["start_x"] is None or primary["start_y"] is None:
            return None

        sx, sy = self.cal.to_screen(primary["start_x"], primary["start_y"])
        ex, ey = self.cal.to_screen(
            primary["last_x"] if primary["last_x"] is not None else primary["start_x"],
            primary["last_y"] if primary["last_y"] is not None else primary["start_y"],
        )
        dur_ms = max(40, int(round((end_ts - start_ts) * 1000)))
        move_start = primary.get("move_start_ts") or start_ts
        swipe_dur_ms = max(35, int(round((end_ts - move_start) * 1000)))
        dist = math.hypot(ex - sx, ey - sy)

        if max_f >= 2:
            # Multi-touch logged (replays primary finger swipe as best-effort non-root fallback)
            return {
                "action": "swipe",
                "arg": f"{sx},{sy},{ex},{ey},{swipe_dur_ms}",
                "note": f"Multi-Touch ({max_f} fingers) {swipe_dur_ms}ms",
                "ts": end_ts,
                "x": sx,
                "y": sy,
                "multi_touch": max_f,
            }

        if dist < self.tap_slop_px:
            if dur_ms >= self.long_press_ms:
                return {
                    "action": "swipe",
                    "arg": f"{sx},{sy},{sx},{sy},{dur_ms}",
                    "note": f"Long Press ({dur_ms}ms)",
                    "ts": end_ts,
                    "x": sx,
                    "y": sy,
                }
            # Detect 3-button software Navigation Bar taps (Back / Home / Recents)
            if self.cal.nav_mode != 2:
                nav_hit = self._match_nav_bar_tap(sx, sy)
                if nav_hit:
                    nav_hit["ts"] = end_ts
                    return nav_hit
            return {
                "action": "tap",
                "arg": f"{sx},{sy}",
                "note": f"Tap ({dur_ms}ms)",
                "ts": end_ts,
                "x": sx,
                "y": sy,
            }

        # Detect Full-Screen Gesture Navigation (Edge Back / Bottom Home / Bottom Recents)
        if self.cal.nav_mode == 2:
            gest_hit = self._match_system_gesture(sx, sy, ex, ey, swipe_dur_ms)
            if gest_hit:
                gest_hit["ts"] = end_ts
                return gest_hit

        vel = int(round(dist / max(0.035, swipe_dur_ms / 1000.0)))
        return {
            "action": "swipe",
            "arg": f"{sx},{sy},{ex},{ey},{swipe_dur_ms}",
            "note": f"Swipe {int(dist)}px | {swipe_dur_ms}ms ({vel} px/s)",
            "ts": end_ts,
            "x": sx,
            "y": sy,
        }

    def _match_nav_bar_tap(self, x: int, y: int) -> Optional[dict]:
        # 1. Exact button bounds captured from SystemUI
        for key, (x1, y1, x2, y2) in self.cal.nav_buttons.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                if key == "back":
                    return {"action": "back", "arg": "", "note": f"Nav Bar: Back ({x},{y})"}
                if key == "home":
                    return {"action": "home", "arg": "", "note": f"Nav Bar: Home ({x},{y})"}
                if key == "recents":
                    return {"action": "keyevent", "arg": "KEYCODE_APP_SWITCH", "note": f"Nav Bar: Recents ({x},{y})"}

        # 2. Fallback zone: bottom ~6.5% of screen when SystemUI bounds unavailable
        if not self.cal.nav_buttons and y >= int(self.cal.screen_h * 0.935):
            ratio = x / max(1, self.cal.screen_w)
            if 0.38 <= ratio <= 0.62:
                return {"action": "home", "arg": "", "note": f"Nav Bar: Home ({x},{y})"}
            if ratio < 0.38:
                return {"action": "back", "arg": "", "note": f"Nav Bar: Back ({x},{y})"}
            return {"action": "keyevent", "arg": "KEYCODE_APP_SWITCH", "note": f"Nav Bar: Recents ({x},{y})"}
        return None

    def _match_system_gesture(self, sx: int, sy: int, ex: int, ey: int, dur_ms: int) -> Optional[dict]:
        w, h = self.cal.screen_w, self.cal.screen_h
        edge_x = max(48, int(w * 0.065))
        edge_y = max(75, int(h * 0.055))
        dx, dy = ex - sx, ey - sy

        # Left-edge inward swipe -> Back
        if sx <= edge_x and dx >= 60 and abs(dx) > abs(dy):
            return {"action": "back", "arg": "", "note": f"Gesture: Back (Left Edge, {dur_ms}ms)"}
        # Right-edge inward swipe -> Back
        if sx >= w - edge_x and (-dx) >= 60 and abs(dx) > abs(dy):
            return {"action": "back", "arg": "", "note": f"Gesture: Back (Right Edge, {dur_ms}ms)"}
        # Bottom-edge upward swipe -> Home (quick) or Recents (hold/slow)
        if sy >= h - edge_y and (-dy) >= 80 and abs(dy) > abs(dx):
            if dur_ms >= 420:
                return {"action": "keyevent", "arg": "KEYCODE_APP_SWITCH", "note": f"Gesture: Recents ({dur_ms}ms)"}
            return {"action": "home", "arg": "", "note": f"Gesture: Home ({dur_ms}ms)"}
        return None


class PhysicalActionRecorder:
    """Runs background `getevent -lt` + app-switch monitor and streams recorded macro steps."""

    IGNORED_PACKAGES = {
        "com.android.systemui",
        "android",
        "com.transsion.hilauncher",
        "com.google.android.apps.nexuslauncher",
        "com.sec.android.app.launcher",
        "com.miui.home",
    }

    def __init__(
        self,
        adb: ADB,
        on_step: Callable[[dict, str], None],
        on_status: Optional[Callable[[str], None]] = None,
        on_replace_last: Optional[Callable[[dict, str], None]] = None,
        smart_mode: bool = False,
        direct_mode: bool = False,
        record_waits: bool = True,
        track_apps: bool = True,
    ):
        self.adb = adb
        self.on_step = on_step
        self.on_status = on_status
        self.on_replace_last = on_replace_last
        self.smart_mode = smart_mode
        self.direct_mode = direct_mode
        self.record_waits = record_waits
        self.track_apps = track_apps

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._threads: List[threading.Thread] = []
        self._last_action_wall: Optional[float] = None
        self._last_tap_wall: Optional[float] = None
        self._lock = threading.Lock()
        self.calibration: Optional[TouchCalibration] = None

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        with self._lock:
            self._last_action_wall = None
        self._pause_event.clear()

    def start(self) -> TouchCalibration:
        self._stop_event.clear()
        self._pause_event.clear()
        self._last_action_wall = None

        if self.direct_mode:
            # Direct Page / Activity Mode: no raw touches/swipes, only direct pages/apps!
            self.calibration = TouchCalibration()
            t_dir = threading.Thread(target=self._direct_activity_watch_loop, daemon=True)
            t_dir.start()
            self._threads.append(t_dir)
            return self.calibration

        self.calibration = get_touch_calibration(self.adb)

        t_ev = threading.Thread(target=self._event_loop, daemon=True)
        t_ev.start()
        self._threads.append(t_ev)

        if self.track_apps:
            t_app = threading.Thread(target=self._app_watch_loop, daemon=True)
            t_app.start()
            self._threads.append(t_app)

        return self.calibration

    def stop(self) -> None:
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _emit_step(self, step: dict, log_line: str) -> None:
        now = time.monotonic()
        with self._lock:
            if self.record_waits and self._last_action_wall is not None:
                gap = now - self._last_action_wall
                # Subtract ~0.35s ADB shell startup latency so replayed pace matches real life
                effective_wait = gap - 0.35
                if effective_wait >= 0.25:
                    wait_sec = round(min(effective_wait, 15.0), 2)
                    wait_step = {"action": "wait", "arg": str(wait_sec), "note": "Auto delay"}
                    self.on_step(wait_step, f"⏱️ Wait {wait_sec}s")
            self._last_action_wall = now
            self.on_step(step, log_line)

    def _event_loop(self) -> None:
        assert self.calibration is not None
        sm = GestureStateMachine(self.calibration)

        # -tt allocates a PTY on Android so `getevent` flushes EVERY line immediately
        # instead of block-buffering 4KB on a non-TTY pipe!
        cmd_candidates = [
            self.adb._cmd() + ["shell", "-tt", "getevent", "-lt"],
            self.adb._cmd() + ["shell", "getevent", "-lt"],
        ]

        try:
            for attempt_idx, cmd in enumerate(cmd_candidates):
                if self._stop_event.is_set():
                    break
                started_at = time.monotonic()
                got_event_line = False
                err_lines: List[str] = []

                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if self._proc.stdout is None:
                    continue

                for line in self._proc.stdout:
                    if self._stop_event.is_set():
                        break
                    clean = ANSI_RE.sub("", line).strip()
                    if not clean:
                        continue
                    if EVENT_LINE_RE.match(clean):
                        got_event_line = True
                        for ev in sm.feed_line(clean):
                            self._handle_gesture(ev)
                    elif any(k in clean.lower() for k in ("permission denied", "could not open", "failed", "error")):
                        err_lines.append(clean)

                # If first attempt (-tt) exited immediately with no events, fall back to plain `shell getevent -lt`
                elapsed = time.monotonic() - started_at
                if attempt_idx == 0 and not got_event_line and not self._stop_event.is_set() and elapsed < 2.0:
                    continue

                if err_lines and not got_event_line and self.on_status and not self._stop_event.is_set():
                    self.on_status(f"⚠️ getevent সতর্কতা: {err_lines[0]}")
                break
        except Exception as exc:
            if self.on_status and not self._stop_event.is_set():
                self.on_status(f"Recorder error: {exc}")
        finally:
            self.stop()

    def _handle_gesture(self, ev: dict) -> None:
        if self._pause_event.is_set():
            return
        act = ev["action"]
        arg = ev["arg"]
        note = ev.get("note", "")

        # Smart Element Mode: inspect UI element under Tap coordinate
        if self.smart_mode and act == "tap" and "x" in ev and "y" in ev:
            elem = find_element_at(self.adb, ev["x"], ev["y"])
            if elem:
                label = elem["text"] or elem["desc"] or elem["resource_id"]
                if label:
                    note = f"{elem['class']}: \"{label}\" (at {arg})"
                    # Record as smart tap_text if concise readable label, else keep coordinate tap with note
                    if len(label) <= 40 and not label.startswith("http"):
                        step = {"action": "tap_text", "arg": label, "note": note}
                        self._last_tap_wall = time.monotonic()
                        self._emit_step(step, f"🧠 Smart Tap \"{label}\" ({arg})")
                        return

        step = {"action": act, "arg": arg, "note": note}
        if act in ("tap", "tap_text"):
            self._last_tap_wall = time.monotonic()
        self._emit_step(step, f"👆 {act} → {arg}  ({note})")

    def _app_watch_loop(self) -> None:
        last_pkg: Optional[str] = None
        pkg_re = re.compile(r"([a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)/")
        while not self._stop_event.is_set():
            try:
                focus = self.adb.current_activity()
                m = pkg_re.search(focus or "")
                pkg = m.group(1) if m else None
                if pkg and pkg != last_pkg:
                    if not self._pause_event.is_set() and last_pkg is not None and pkg not in self.IGNORED_PACKAGES:
                        recent_tap = (
                            self._last_tap_wall is not None
                            and (time.monotonic() - self._last_tap_wall) <= 2.5
                        )
                        from_launcher = last_pkg in self.IGNORED_PACKAGES
                        if recent_tap and from_launcher and self.on_replace_last:
                            # Home Screen আইকন ট্যাপ করে অ্যাপ খুললে ডুপ্লিকেট না করে Tap-কে launch_app দিয়ে রিপ্লেস করি
                            step = {"action": "launch_app", "arg": pkg, "note": "Opened from Home/Launcher"}
                            self._last_tap_wall = None
                            self.on_replace_last(step, f"📱 Launcher Tap → Launch {pkg} (No duplicate)")
                        elif recent_tap and not from_launcher:
                            # অন্য অ্যাপের ভেতরে ট্যাপ করে (যেমন লিংক/শেয়ার) নতুন অ্যাপ খুললে ডুপ্লিকেট launch_app বাদ দিই
                            if self.on_status:
                                self.on_status(f"ℹ️ App switched to {pkg} via tap (duplicate launch skipped)")
                        else:
                            step = {"action": "launch_app", "arg": pkg, "note": "App switched"}
                            self._emit_step(step, f"📱 App Opened: {pkg}")
                    last_pkg = pkg
            except Exception:
                pass
            self._stop_event.wait(0.9)

    def _direct_activity_watch_loop(self) -> None:
        """Records only foreground App/Page (Activity) transitions — zero raw taps or swipes."""
        last_comp: Optional[str] = None
        while not self._stop_event.is_set():
            try:
                pkg, comp = self.adb.get_top_component()
                if comp and comp != last_comp:
                    if not self._pause_event.is_set() and last_comp is not None and pkg not in self.IGNORED_PACKAGES:
                        short_act = comp.split("/")[-1].split(".")[-1]
                        step = {
                            "action": "open_activity",
                            "arg": comp,
                            "note": f"Direct Page ({short_act})",
                        }
                        self._emit_step(step, f"🎯 Direct Page: {comp}")
                    last_comp = comp
            except Exception:
                pass
            self._stop_event.wait(0.6)
