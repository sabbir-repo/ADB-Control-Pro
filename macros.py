import json
import os
import re
import shlex
import threading
import time
from typing import Dict, List, Optional

from action_recorder import find_element_by_query
from device_profiles import smart_unlock_device
from main import ADB, ADBError


DEFAULT_MACROS: Dict[str, List[dict]] = {
    "Wake & Open Chrome": [
        {"action": "wake", "arg": ""},
        {"action": "wait", "arg": "0.8"},
        {"action": "launch_app", "arg": "com.android.chrome"},
    ],
    "Wake, Unlock & Home": [
        {"action": "wake", "arg": ""},
        {"action": "wait", "arg": "0.6"},
        {"action": "unlock", "arg": ""},
        {"action": "wait", "arg": "0.5"},
        {"action": "home", "arg": ""},
    ],
}


class MacroStore:
    """Persistent JSON storage for 1-Click ADB automation macros."""

    def __init__(self, path: str = "macros.json"):
        self.path = path
        self.macros: Dict[str, List[dict]] = {}
        self.load()

    def load(self) -> Dict[str, List[dict]]:
        if not os.path.exists(self.path):
            self.macros = {k: list(v) for k, v in DEFAULT_MACROS.items()}
            self.save()
            return self.macros
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.macros = data
            else:
                self.macros = dict(DEFAULT_MACROS)
        except Exception:
            self.macros = dict(DEFAULT_MACROS)
        return self.macros

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.macros, f, ensure_ascii=False, indent=2)

    def names(self) -> List[str]:
        return list(self.macros.keys())

    def get(self, name: str) -> List[dict]:
        return list(self.macros.get(name, []))

    def set_macro(self, name: str, steps: List[dict]) -> None:
        clean = name.strip()
        if not clean:
            raise ValueError("ম্যাক্রোর নাম খালি রাখা যাবে না।")
        self.macros[clean] = list(steps)
        self.save()

    def delete_macro(self, name: str) -> None:
        if name in self.macros:
            del self.macros[name]
            self.save()


def execute_macro(
    adb: ADB,
    steps: List[dict],
    profile: Optional[dict] = None,
    repeat: int = 1,
    stop_event: Optional[threading.Event] = None,
) -> str:
    """Executes a sequence of ADB macro steps (optionally repeated) and returns a summary."""
    if not steps:
        raise ADBError("এই ম্যাক্রোতে কোনো স্টেপ নেই।")

    repeat = max(1, min(int(repeat or 1), 500))
    logs: List[str] = []
    stopped = False

    for loop_i in range(1, repeat + 1):
        if stop_event and stop_event.is_set():
            stopped = True
            break
        if repeat > 1:
            logs.append(f"── Loop {loop_i}/{repeat} ──")
        for idx, step in enumerate(steps, 1):
            if stop_event and stop_event.is_set():
                stopped = True
                break
            act = (step.get("action") or "").strip().lower()
            arg = str(step.get("arg") or "").strip()
            note = str(step.get("note") or "")

            if act == "wake":
                adb.keyevent("KEYCODE_WAKEUP")
                logs.append(f"{idx}. Wake")
            elif act == "unlock":
                if profile and profile.get("lock_type", "none") != "none":
                    smart_unlock_device(adb, profile)
                    logs.append(f"{idx}. Smart Unlock ({profile.get('lock_type')})")
                else:
                    adb.swipe(500, 1600, 500, 400, 400)
                    logs.append(f"{idx}. Swipe Unlock")
            elif act == "home":
                adb.keyevent("KEYCODE_HOME")
                logs.append(f"{idx}. Home")
            elif act == "back":
                adb.keyevent("KEYCODE_BACK")
                logs.append(f"{idx}. Back")
            elif act == "launch_app":
                if not arg:
                    raise ADBError(f"Step {idx} (launch_app)-এ প্যাকেজের নাম খালি।")
                adb.launch_app(arg)
                logs.append(f"{idx}. Launch {arg}")
            elif act == "open_activity":
                if not arg:
                    raise ADBError(f"Step {idx} (open_activity)-এ Activity/Page খালি।")
                res = adb.open_activity(arg)
                short = arg.split("/")[-1].split(".")[-1]
                logs.append(f"{idx}. Open Page {short}" + (" (fallback app)" if "মূল অ্যাপ" in res else ""))
            elif act == "open_url":
                if not arg:
                    raise ADBError(f"Step {idx} (open_url)-এ URL খালি।")
                adb.open_url(arg)
                logs.append(f"{idx}. Open URL {arg}")
            elif act == "wait":
                sec = max(0.05, min(float(arg or 1.0), 60.0))
                if stop_event:
                    if stop_event.wait(sec):
                        stopped = True
                        break
                else:
                    time.sleep(sec)
                logs.append(f"{idx}. Wait {sec}s")
            elif act == "tap":
                parts = [int(p.strip()) for p in arg.split(",")]
                if len(parts) != 2:
                    raise ADBError(f"Step {idx} (tap)-এ সঠিক X,Y দিন (যেমন: 540,1200)।")
                adb.tap(parts[0], parts[1])
                logs.append(f"{idx}. Tap ({parts[0]}, {parts[1]})")
            elif act == "tap_text":
                found = find_element_by_query(adb, arg)
                if found:
                    cx, cy, label = found
                    adb.tap(cx, cy)
                    logs.append(f"{idx}. Smart Tap \"{label}\" ({cx},{cy})")
                else:
                    # Fallback to saved coordinate in note if present: (at x,y)
                    fb = re.search(r"\(at\s+(\d+)\s*,\s*(\d+)\)", note)
                    if fb:
                        fx, fy = int(fb.group(1)), int(fb.group(2))
                        adb.tap(fx, fy)
                        logs.append(f"{idx}. Fallback Tap ({fx},{fy}) for \"{arg}\"")
                    else:
                        raise ADBError(f"Step {idx} (tap_text): স্ক্রিনে '{arg}' খুঁজে পাওয়া যায়নি।")
            elif act == "swipe":
                parts = [int(p.strip()) for p in arg.split(",")]
                if len(parts) not in (4, 5):
                    raise ADBError(f"Step {idx} (swipe)-এ x1,y1,x2,y2[,ms] দিন।")
                dur = parts[4] if len(parts) == 5 else 300
                adb.swipe(parts[0], parts[1], parts[2], parts[3], duration=dur)
                logs.append(f"{idx}. Swipe {parts[:4]} ({dur}ms)")
            elif act == "text":
                adb.input_text(arg, delay_after_first=1.0)
                logs.append(f"{idx}. Text '{arg}'")
            elif act == "keyevent":
                adb.keyevent(arg or "KEYCODE_ENTER")
                logs.append(f"{idx}. Key {arg}")
            else:
                logs.append(f"{idx}. Skipped unknown action '{act}'")
        if stopped:
            break

    header = "⏹️ Macro থামানো হয়েছে:" if stopped else "🤖 Macro সম্পন্ন:"
    return header + "\n" + "\n".join(logs)
