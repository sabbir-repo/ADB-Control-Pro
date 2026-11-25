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


SIM_NUMBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_sim_numbers.json")


class DeviceSimStore:
    """Persists per-device saved SIM phone numbers (keyed by ro.build.fingerprint)."""

    def __init__(self, path: str = SIM_NUMBERS_FILE):
        self.path = path

    def load(self) -> Dict[str, Dict[str, str]]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, data: Dict[str, Dict[str, str]]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_for_device(self, fingerprint: str) -> Dict[str, str]:
        fp = (fingerprint or "").strip()
        if not fp:
            return {}
        entry = self.load().get(fp, {})
        return dict(entry) if isinstance(entry, dict) else {}

    def set_slot_number(self, fingerprint: str, slot: int, number: str) -> Dict[str, str]:
        fp = (fingerprint or "").strip()
        if not fp:
            raise ValueError("Device fingerprint পাওয়া যায়নি।")
        slot_key = str(max(1, int(slot)))
        num_clean = (number or "").strip()
        data = self.load()
        entry = data.get(fp, {})
        if not isinstance(entry, dict):
            entry = {}
        if num_clean:
            entry[slot_key] = num_clean
        else:
            entry.pop(slot_key, None)
        if entry:
            data[fp] = entry
        else:
            data.pop(fp, None)
        self.save(data)
        return dict(entry)


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


def get_lock_screen_state(adb: ADB) -> dict:
    """
    Detects whether the Android device is currently locked and whether the screen is awake
    using a single combined `dumpsys` call (works on Android 8–15, Tecno HiOS, Samsung, Xiaomi, Pixel).
    Returns: {"locked": Optional[bool], "awake": Optional[bool]}
    """
    import re

    raw = adb.shell(
        "dumpsys window | grep -iE 'mShowingLockscreen|mDreamingLockscreen|isStatusBarKeyguard'; "
        "echo '__PWR__'; "
        "dumpsys power | grep -E 'mWakefulness=|Display Power: state='; "
        "echo '__TRUST__'; "
        "dumpsys trust | grep -i 'deviceLocked=' | head -n 5",
        check=False,
        timeout=7,
    )
    if not raw:
        return {"locked": None, "awake": None}

    parts = raw.split("__PWR__")
    win_txt = parts[0] if len(parts) > 0 else ""
    rest = parts[1] if len(parts) > 1 else ""
    rest_parts = rest.split("__TRUST__")
    pwr_txt = rest_parts[0] if len(rest_parts) > 0 else ""
    trust_txt = rest_parts[1] if len(rest_parts) > 1 else ""

    # 1. Screen wakefulness
    awake: Optional[bool] = None
    if "mWakefulness=Awake" in pwr_txt or "state=ON" in pwr_txt:
        awake = True
    elif "mWakefulness=Asleep" in pwr_txt or "mWakefulness=Dozing" in pwr_txt or "state=OFF" in pwr_txt:
        awake = False

    # 2. Keyguard / Lockscreen visibility from WindowManager
    keyguard_flags = re.findall(
        r"(?:mShowingLockscreen|mDreamingLockscreen|isStatusBarKeyguard)=(true|false)",
        win_txt,
        flags=re.IGNORECASE,
    )
    keyguard_showing: Optional[bool] = None
    if keyguard_flags:
        keyguard_showing = any(v.lower() == "true" for v in keyguard_flags)

    # 3. TrustManager deviceLocked flag (prefer `(current)` user line if multi-user)
    device_locked: Optional[bool] = None
    trust_lines = [ln.strip() for ln in trust_txt.splitlines() if "deviceLocked=" in ln]
    target_trust_line = next((ln for ln in trust_lines if "(current)" in ln), trust_lines[0] if trust_lines else "")
    m_dl = re.search(r"deviceLocked=([01]|true|false)", target_trust_line, flags=re.IGNORECASE)
    if m_dl:
        device_locked = m_dl.group(1).lower() in ("1", "true")

    # Combine signals safely: locked if EITHER TrustManager OR Keyguard says locked
    if device_locked is True or keyguard_showing is True:
        locked: Optional[bool] = True
    elif device_locked is False or keyguard_showing is False:
        locked = False
    else:
        locked = None

    return {"locked": locked, "awake": awake}


def smart_unlock_device(adb: ADB, profile: dict) -> str:
    """Executes the saved unlock sequence ONLY if the device is actually locked."""
    process = profile.get("unlock_process", "Wake + Swipe Up")
    value = profile.get("unlock_value", "")
    name = profile.get("name", "Device")

    # ০. আগে চেক করা ডিভাইসটি আদৌ লক আছে নাকি ইতিমধ্যেই আনলক করা!
    state = get_lock_screen_state(adb)
    if state["locked"] is False:
        if state["awake"] is False:
            adb.keyevent("KEYCODE_WAKEUP")
            return f"☀️ '{name}' ইতিমধ্যেই আনলক ছিল — পিন/পাসওয়ার্ড না দিয়ে শুধু স্ক্রিন চালু করা হয়েছে।"
        return f"🔓 '{name}' ইতিমধ্যেই আনলক করা আছে — কোনো অ্যাকশন নেওয়া হয়নি।"

    coords = _resolve_swipe_coords(adb, profile.get("swipe_coords", "auto"))

    # ১. স্ক্রিন অফ থাকলে জাগানো এবং লক স্ক্রিন অ্যানিমেশন পুরোপুরি লোড হওয়ার জন্য সময় দেওয়া
    if state["awake"] is not True:
        adb.keyevent("KEYCODE_WAKEUP")
        time.sleep(0.9)
    else:
        time.sleep(0.2)

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
