#!/usr/bin/env python3
"""
Device Profile Storage & Smart 1-Click Unlock Engine.
Identifies each Android device uniquely by its `ro.build.fingerprint`
and stores device-specific lock type, unlock process, unlock value, and notes.
"""

import json
import os
import time
from typing import Dict, List, Optional

from main import ADB, ADBError

PROFILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_profiles.json")

LOCK_TYPES = [
    "None / Swipe",
    "PIN",
    "Password",
    "Pattern / Swipe",
    "Custom ADB",
]

UNLOCK_PROCESSES = [
    "Wake + Swipe Up",
    "Wake + Swipe Up + Input + Enter",
    "Wake + Input + Enter",
    "Wake + Pattern (1-9 or X,Y)",
    "Custom Shell Commands",
]


class DeviceProfileStore:
    """Manages persistent JSON storage for device profiles keyed by Fingerprint."""

    def __init__(self, path: str = PROFILE_FILE):
        self.path = path

    def load(self) -> Dict[str, dict]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, profiles: Dict[str, dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)

    def get(self, fingerprint: str) -> Optional[dict]:
        if not fingerprint:
            return None
        return self.load().get(fingerprint.strip())

    def upsert(self, fingerprint: str, profile: dict) -> dict:
        fp = fingerprint.strip()
        if not fp:
            raise ValueError("Fingerprint খালি হতে পারবে না।")
        profiles = self.load()
        entry = {
            "fingerprint": fp,
            "name": profile.get("name", "").strip() or (fp.split("/")[1] if "/" in fp else fp),
            "lock_type": profile.get("lock_type", "PIN"),
            "unlock_process": profile.get("unlock_process", "Wake + Swipe Up + Input + Enter"),
            "unlock_value": profile.get("unlock_value", ""),
            "swipe_coords": profile.get("swipe_coords", "auto").strip() or "auto",
            "notes": profile.get("notes", "").strip(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        profiles[fp] = entry
        self.save(profiles)
        return entry

    def delete(self, fingerprint: str) -> bool:
        fp = fingerprint.strip()
        profiles = self.load()
        if fp in profiles:
            del profiles[fp]
            self.save(profiles)
            return True
        return False

    def list_all(self) -> List[dict]:
        return list(self.load().values())


def _get_screen_size(adb: ADB) -> tuple[int, int]:
    """Reads phone screen resolution via `wm size` so swipe works on 720p, 1080p, etc."""
    try:
        out = adb.shell("wm size", check=False, timeout=5)
        for line in reversed(out.splitlines()):
            if "x" in line and ":" in line:
                size_part = line.split(":")[-1].strip()
                w_str, h_str = size_part.lower().split("x", 1)
                return int(w_str.strip()), int(h_str.strip())
    except Exception:
        pass
    return 720, 1600


def _resolve_swipe_coords(adb: ADB, coords_str: str) -> tuple[int, int, int, int]:
    cleaned = (coords_str or "").strip().lower()
    if cleaned and cleaned != "auto":
        try:
            parts = [int(p.strip()) for p in cleaned.split(",")]
            if len(parts) == 4:
                return parts[0], parts[1], parts[2], parts[3]
        except ValueError:
            pass
    w, h = _get_screen_size(adb)
    return w // 2, int(h * 0.78), w // 2, int(h * 0.25)


def _resolve_pattern_points(adb: ADB, value: str) -> List[tuple[int, int]]:
    """
    Parses pattern value in two formats:
    1) Dot numbers 1-9 (e.g. '12369' or '1-2-3-6-9' or '1,2,3,6,9')
    2) Explicit pixel coordinates (e.g. '200,800;540,800;540,1150')
    """
    cleaned = value.strip()
    if ";" in cleaned:
        pts: List[tuple[int, int]] = []
        for pair in cleaned.split(";"):
            pair = pair.strip()
            if "," in pair:
                xs, ys = pair.split(",", 1)
                pts.append((int(xs.strip()), int(ys.strip())))
        if len(pts) >= 2:
            return pts

    digits = [int(ch) for ch in cleaned if ch in "123456789"]
    if len(digits) < 2:
        raise ADBError("Pattern-এর জন্য কমপক্ষে ২টি ডট দিন (যেমন: 12369 অথবা 1,4,7,8,9)।")

    w, h = _get_screen_size(adb)
    # 3x3 প্যাটার্ন গ্রিড: প্রস্থের 24%-50%-76% এবং উচ্চতার 52%-64%-76%
    pts = []
    for d in digits:
        row, col = divmod(d - 1, 3)
        px = int(w * (0.24 + col * 0.26))
        py = int(h * (0.52 + row * 0.12))
        pts.append((px, py))
    return pts


def _draw_pattern_motionevent(adb: ADB, points: List[tuple[int, int]]) -> None:
    """Draws a continuous touch gesture across pattern dots in a single adb shell call."""
    cmds = [f"input motionevent DOWN {points[0][0]} {points[0][1]}"]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        steps = 4
        for s in range(1, steps + 1):
            ix = int(x1 + (x2 - x1) * s / steps)
            iy = int(y1 + (y2 - y1) * s / steps)
            cmds.append(f"input motionevent MOVE {ix} {iy}")
    cmds.append(f"input motionevent UP {points[-1][0]} {points[-1][1]}")
    adb.shell(" && ".join(cmds), check=False, timeout=15)


def smart_unlock_device(adb: ADB, profile: dict) -> str:
    """Executes the saved unlock sequence for the connected device with safe animations delays."""
    process = profile.get("unlock_process", "Wake + Swipe Up")
    value = profile.get("unlock_value", "")
    coords = _resolve_swipe_coords(adb, profile.get("swipe_coords", "auto"))
    name = profile.get("name", "Device")

    # ১. স্ক্রিন জাগানো এবং লক স্ক্রিন অ্যানিমেশন পুরোপুরি লোড হওয়ার জন্য পর্যাপ্ত সময় দেওয়া
    adb.keyevent("KEYCODE_WAKEUP")
    time.sleep(0.9)

    if process == "Wake + Swipe Up":
        adb.shell("wm dismiss-keyguard", check=False)
        time.sleep(0.25)
        adb.swipe(*coords, duration=350)
        return f"✔ '{name}' Wake + Swipe Up সম্পন্ন হয়েছে।"

    if process == "Wake + Swipe Up + Input + Enter":
        if not value:
            raise ADBError(f"'{name}' প্রোফাইলে Unlock Value (PIN/Password) সেভ করা নেই!")
        # Android 11-14+ এ wm dismiss-keyguard সরাসরি PIN/Password স্ক্রিন ওপেন করে
        adb.shell("wm dismiss-keyguard", check=False)
        time.sleep(0.35)
        adb.swipe(*coords, duration=350)
        time.sleep(0.8)
        adb.input_text(value)
        time.sleep(0.35)
        adb.keyevent("KEYCODE_ENTER")
        return f"✔ '{name}' সফলভাবে 1-Click Unlock (Swipe + Input + Enter) করা হয়েছে!"

    if process == "Wake + Input + Enter":
        if not value:
            raise ADBError(f"'{name}' প্রোফাইলে Unlock Value (PIN/Password) সেভ করা নেই!")
        adb.input_text(value)
        time.sleep(0.35)
        adb.keyevent("KEYCODE_ENTER")
        return f"✔ '{name}' সফলভাবে 1-Click Unlock (Input + Enter) করা হয়েছে!"

    if process == "Wake + Pattern (1-9 or X,Y)":
        if not value.strip():
            raise ADBError(f"'{name}' প্রোফাইলে Pattern Value (যেমন: 12369) সেভ করা নেই!")
        adb.shell("wm dismiss-keyguard", check=False)
        time.sleep(0.35)
        adb.swipe(*coords, duration=350)
        time.sleep(0.8)
        points = _resolve_pattern_points(adb, value)
        _draw_pattern_motionevent(adb, points)
        return f"✔ '{name}' সফলভাবে 1-Click Pattern Unlock চালানো হয়েছে!"

    if process == "Custom Shell Commands":
        if not value.strip():
            raise ADBError(f"'{name}' প্রোফাইলে কোনো Custom Shell Command দেওয়া নেই!")
        outputs = []
        for raw_line in value.replace(";", "\n").splitlines():
            cmd = raw_line.strip()
            if cmd:
                out = adb.shell(cmd, check=False)
                if out:
                    outputs.append(out)
                time.sleep(0.3)
        return f"✔ '{name}' Custom Unlock Commands সম্পন্ন:\n" + ("\n".join(outputs) or "Done")

    raise ADBError(f"অজানা Unlock Process: {process}")
