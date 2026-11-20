#!/usr/bin/env python3
"""
Dedicated Desktop GUI for ADB Phone Control
Uses Python's built-in tkinter/ttk with a modern dark theme and non-blocking worker threads.
"""

import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
from typing import Callable, List, Optional

import customtkinter as ctk
import ctk_widgets as ttk
from ctk_widgets import SidebarNotebook

from main import ADB, ADBError
from action_recorder import PhysicalActionRecorder
from macros import MacroStore, execute_macro
from device_profiles import (
    DeviceProfileStore,
    LOCK_TYPES,
    UNLOCK_PROCESSES,
    _resolve_swipe_coords,
    smart_unlock_device,
)

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
except ImportError:
    ServiceBrowser = ServiceStateChange = Zeroconf = None


# ─────────────────────────── Color Palette ───────────────────────────
BG_DARK = "#0f172a"       # Main background (Slate 900)
BG_PANEL = "#1e293b"      # Card / panel background (Slate 800)
BG_INPUT = "#0b1120"      # Console & input background
FG_TEXT = "#f8fafc"       # Primary text (Slate 50)
FG_MUTED = "#94a3b8"      # Secondary text (Slate 400)
ACCENT = "#10b981"        # Emerald green accent
ACCENT_HOVER = "#059669"  # Darker emerald
ACCENT_BLUE = "#3b82f6"   # Blue accent
DANGER = "#ef4444"        # Red accent
WARNING = "#f59e0b"       # Amber warning
BORDER = "#334155"        # Border slate


class ADBPhoneControlUI:
    """Senior-grade Desktop GUI wrapper around ADB & scrcpy."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ADB Control — Pro Desktop UI (CustomTkinter)")
        self.root.geometry("1380x840")
        self.root.minsize(1160, 720)
        self.root.configure(fg_color=BG_DARK)

        self.adb = ADB(timeout=30)
        self.busy = False
        self.perf_monitoring = False
        self.logcat_proc: Optional[subprocess.Popen] = None
        self.auto_sync_active = False
        self.action_recorder: Optional[PhysicalActionRecorder] = None
        self.macro_stop_event: Optional[threading.Event] = None
        self.macro_store = MacroStore()

        # Tkinter Variables
        self.device_var = tk.StringVar(value="")
        self.ip_port_var = tk.StringVar(value="192.168.1.100:5555")
        self.status_var = tk.StringVar(value="Ready — কোনো ডিভাইস সিলেক্ট করা হয়নি")
        self.shell_cmd_var = tk.StringVar(value="getprop ro.product.model")
        self.log_lines_var = tk.StringVar(value="150")
        self.log_mode_var = tk.StringVar(value="All")
        self.log_keyword_var = tk.StringVar(value="")
        self.pkg_name_var = tk.StringVar(value="")
        self.pkg_filter_var = tk.StringVar(value="")
        self.remote_path_var = tk.StringVar(value="/sdcard/Download")
        self.local_path_var = tk.StringVar(value=os.getcwd())
        self.record_sec_var = tk.StringVar(value="10")
        self.scrcpy_preset_var = tk.StringVar(value="1")
        self.scrcpy_extra_var = tk.StringVar(value="-m 1024 --max-fps 60")
        self.tcpip_port_var = tk.StringVar(value="5555")
        self.timeout_var = tk.StringVar(value="30")
        self.input_text_var = tk.StringVar(value="")
        self.tap_x_var = tk.StringVar(value="500")
        self.tap_y_var = tk.StringVar(value="1000")

        # Live File Transfer Progress Variables
        self.transfer_cancel_event: Optional[threading.Event] = None
        self.transfer_label_var = tk.StringVar(value="কোনো ফাইল ট্রান্সফার চলছে না")
        self.transfer_stats_var = tk.StringVar(value="0.0%  •  0 B / 0 B  •  Speed: 0 B/s  •  ETA: --")
        self.transfer_pct_var = tk.DoubleVar(value=0.0)

        # Device Profile & Smart Unlock Variables
        self.profile_store = DeviceProfileStore()
        self.prof_fp_var = tk.StringVar(value="")
        self.prof_name_var = tk.StringVar(value="")
        self.prof_lock_type_var = tk.StringVar(value=LOCK_TYPES[1])
        self.prof_process_var = tk.StringVar(value=UNLOCK_PROCESSES[1])
        self.prof_value_var = tk.StringVar(value="")
        self.prof_coords_var = tk.StringVar(value="auto")
        self.prof_notes_var = tk.StringVar(value="")
        self.prof_show_val = tk.BooleanVar(value=False)

        # Quick Controls, URL & Permission Variables
        self.url_var = tk.StringVar(value="https://")
        self.brightness_var = tk.IntVar(value=128)
        self.timeout_preset_var = tk.StringVar(value="1 Minute")
        self.perm_var = tk.StringVar(value="android.permission.CAMERA")

        self._setup_styles()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Initial device scan
        self.root.after(200, self.refresh_devices)

    # ─────────────────────────── Styling ───────────────────────────
    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=BG_DARK, foreground=FG_TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG_DARK)
        style.configure("Card.TFrame", background=BG_PANEL, relief="flat")
        style.configure("TLabel", background=BG_DARK, foreground=FG_TEXT, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=BG_PANEL, foreground=FG_TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG_PANEL, foreground=FG_MUTED, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=BG_DARK, foreground=ACCENT, font=("Segoe UI", 15, "bold"))
        style.configure("SubHeader.TLabel", background=BG_PANEL, foreground=ACCENT, font=("Segoe UI", 11, "bold"))

        # Notebook (Tabs)
        style.configure(
            "TNotebook",
            background=BG_DARK,
            borderwidth=0,
            tabmargins=[2, 5, 2, 0],
        )
        style.configure(
            "TNotebook.Tab",
            background=BG_PANEL,
            foreground=FG_MUTED,
            padding=[8, 6],
            font=("Segoe UI", 9, "bold"),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT), ("active", BORDER)],
            foreground=[("selected", "#052e16"), ("active", FG_TEXT)],
        )

        # Buttons
        style.configure(
            "TButton",
            background=BORDER,
            foreground=FG_TEXT,
            padding=[10, 6],
            font=("Segoe UI", 9, "bold"),
            borderwidth=0,
        )
        style.map(
            "TButton",
            background=[("active", "#475569"), ("disabled", "#1e293b")],
            foreground=[("disabled", "#64748b")],
        )

        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#052e16",
            padding=[12, 6],
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_HOVER)],
            foreground=[("active", "#ffffff")],
        )

        style.configure(
            "Blue.TButton",
            background=ACCENT_BLUE,
            foreground="#ffffff",
            padding=[10, 6],
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Blue.TButton", background=[("active", "#2563eb")])

        style.configure(
            "Danger.TButton",
            background=DANGER,
            foreground="#ffffff",
            padding=[10, 6],
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Danger.TButton", background=[("active", "#dc2626")])

        # Entry & Combobox
        style.configure(
            "TEntry",
            fieldbackground=BG_INPUT,
            foreground=FG_TEXT,
            insertcolor=FG_TEXT,
            borderwidth=1,
            padding=5,
        )
        style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            background=BORDER,
            foreground=FG_TEXT,
            arrowcolor=ACCENT,
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_INPUT)],
            foreground=[("readonly", FG_TEXT)],
        )

        style.configure(
            "TRadiobutton",
            background=BG_PANEL,
            foreground=FG_TEXT,
            font=("Segoe UI", 9),
        )
        style.map("TRadiobutton", background=[("active", BG_PANEL)])

        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=BG_INPUT,
            background=ACCENT,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=16,
        )

        style.configure(
            "Treeview",
            background=BG_INPUT,
            foreground=FG_TEXT,
            fieldbackground=BG_INPUT,
            borderwidth=0,
            rowheight=24,
        )
        style.configure(
            "Treeview.Heading",
            background=BORDER,
            foreground=FG_TEXT,
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#052e16")],
        )

    # ─────────────────────────── Main Layout ───────────────────────────
    def _build_layout(self):
        # 1. Top Toolbar (Device Selection & Quick Actions)
        top_bar = ctk.CTkFrame(
            self.root,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        top_bar.pack(fill="x", padx=14, pady=(12, 8))

        title_box = ttk.Frame(top_bar)
        title_box.pack(side="left", padx=14, pady=10)
        ttk.Label(title_box, text="⚡ ADB CONTROL", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="Python + ADB + scrcpy Desktop Control Center",
            foreground=FG_MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        # Device selector controls on the right of top bar
        dev_bar = ttk.Frame(top_bar)
        dev_bar.pack(side="right", padx=14, pady=10)

        ttk.Label(dev_bar, text="Active Device:").grid(row=0, column=0, padx=(0, 6))
        self.device_combo = ttk.Combobox(
            dev_bar,
            textvariable=self.device_var,
            state="readonly",
            width=26,
        )
        self.device_combo.grid(row=0, column=1, padx=4)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        ttk.Button(dev_bar, text="🔄 Refresh", command=self.refresh_devices).grid(row=0, column=2, padx=4)
        ttk.Button(dev_bar, text="ℹ️ Info", style="Blue.TButton", command=self.action_check_connection).grid(row=0, column=3, padx=4)
        ttk.Button(dev_bar, text="🔓 1-Click Unlock", style="Accent.TButton", command=self.action_one_click_unlock).grid(row=0, column=4, padx=4)
        ttk.Button(dev_bar, text="🖥️ Mirror (scrcpy)", style="Accent.TButton", command=self.action_start_scrcpy).grid(row=0, column=5, padx=4)

        # 2. Main Split Area: Left = Sidebar + Scrollable Tabs, Right = Interactive Console Output
        paned = tk.PanedWindow(
            self.root,
            orient="horizontal",
            bg=BG_DARK,
            sashwidth=5,
            bd=0,
        )
        paned.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        left_container = ctk.CTkFrame(paned, fg_color="transparent")
        right_container = ctk.CTkFrame(paned, fg_color="transparent")
        paned.add(left_container, minsize=760, width=860)
        paned.add(right_container, minsize=380)

        # Build Modern Left Sidebar Navigation
        self.notebook = SidebarNotebook(left_container, sidebar_width=168)
        self.notebook.pack(fill="both", expand=True)

        self.tab_device = self.notebook.add_scrollable_tab("📱 Device")
        self.tab_apps = self.notebook.add_scrollable_tab("📦 Apps")
        self.tab_files = self.notebook.add_scrollable_tab("📁 Files")
        self.tab_monitor = self.notebook.add_scrollable_tab("📊 Monitor")
        self.tab_scrcpy = self.notebook.add_scrollable_tab("🖥️ Screen Share")
        self.tab_wifi = self.notebook.add_scrollable_tab("📶 WiFi & Live")
        self.tab_controls = self.notebook.add_scrollable_tab("⚙️ Controls")
        self.tab_profiles = self.notebook.add_scrollable_tab("🔑 Profiles")
        self.tab_comms = self.notebook.add_plain_tab("💬 Comms")
        self.tab_macro = self.notebook.add_scrollable_tab("🤖 Macro")

        self._build_tab_device()
        self._build_tab_apps()
        self._build_tab_files()
        self._build_tab_monitor()
        self._build_tab_scrcpy()
        self._build_tab_wifi()
        self._build_tab_controls()
        self._build_tab_profiles()
        self._build_tab_comms()
        self._build_tab_macro()

        # Build Output Console on Right
        self._build_console_pane(right_container)

        # 3. Bottom Status Bar
        status_bar = ctk.CTkFrame(self.root, fg_color=BG_PANEL, height=32, corner_radius=0)
        status_bar.pack(fill="x", side="bottom")
        self.status_dot = tk.Label(status_bar, text="●", fg=ACCENT, bg=BG_PANEL, font=("Segoe UI", 11))
        self.status_dot.pack(side="left", padx=(14, 4), pady=4)
        ttk.Label(
            status_bar,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
        ).pack(side="left", pady=4)

    def _card(self, parent, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        card.pack(fill="x", pady=6, padx=2)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)
        ttk.Label(inner, text=title, style="SubHeader.TLabel").pack(anchor="w", pady=(0, 8))
        return inner

    # ─────────────────────────── Tab 1: Device Management ───────────────────────────
    def _build_tab_device(self):
        # Connection & Server Controls
        c1 = self._card(self.tab_device, "1. Device & ADB Server Controls")
        btn_row1 = ttk.Frame(c1, style="Card.TFrame")
        btn_row1.pack(fill="x", pady=2)
        ttk.Button(btn_row1, text="📋 List Devices", command=self.action_list_devices).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(btn_row1, text="✅ Check Connection", command=self.action_check_connection).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(btn_row1, text="⏳ Wait for Device", command=self.action_wait_device).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(btn_row1, text="🔁 Restart ADB", command=self.action_restart_adb).pack(side="left", padx=3, expand=True, fill="x")

        # Quick WiFi Connect / Disconnect
        conn_row = ttk.Frame(c1, style="Card.TFrame")
        conn_row.pack(fill="x", pady=(8, 2))
        ttk.Label(conn_row, text="IP:PORT:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        ttk.Entry(conn_row, textvariable=self.ip_port_var, width=22).pack(side="left", padx=4)
        ttk.Button(conn_row, text="🔗 Connect WiFi", style="Accent.TButton", command=self.action_connect_wifi).pack(side="left", padx=4)
        ttk.Button(conn_row, text="🔌 Disconnect", command=self.action_disconnect_device).pack(side="left", padx=4)
        ttk.Button(conn_row, text="⚠️ Reboot", style="Danger.TButton", command=self.action_reboot_device).pack(side="right", padx=4)

        # Shell Command Execution
        c2 = self._card(self.tab_device, "2. Execute Custom ADB Shell Command")
        sh_row = ttk.Frame(c2, style="Card.TFrame")
        sh_row.pack(fill="x")
        entry_sh = ttk.Entry(sh_row, textvariable=self.shell_cmd_var)
        entry_sh.pack(side="left", fill="x", expand=True, padx=(0, 6))
        entry_sh.bind("<Return>", lambda _: self.action_exec_shell())
        ttk.Button(sh_row, text="▶ Run Shell", style="Accent.TButton", command=self.action_exec_shell).pack(side="right")

        # Device Logs (Logcat Snapshot)
        c3 = self._card(self.tab_device, "3. Device Logs (Logcat Filter & Color View)")
        log_row = ttk.Frame(c3, style="Card.TFrame")
        log_row.pack(fill="x")
        ttk.Label(log_row, text="Lines:", style="Card.TLabel").pack(side="left")
        ttk.Entry(log_row, textvariable=self.log_lines_var, width=7).pack(side="left", padx=4)
        ttk.Label(log_row, text="Mode:", style="Card.TLabel").pack(side="left", padx=(6, 2))
        ttk.Combobox(
            log_row,
            textvariable=self.log_mode_var,
            values=["All", "Errors Only (*:E)", "Keyword Search"],
            state="readonly",
            width=16,
        ).pack(side="left", padx=4)
        ttk.Label(log_row, text="Keyword:", style="Card.TLabel").pack(side="left", padx=(6, 2))
        ttk.Entry(log_row, textvariable=self.log_keyword_var, width=14).pack(side="left", padx=4)
        ttk.Button(log_row, text="📜 Fetch Logs", style="Blue.TButton", command=self.action_get_logs).pack(side="right", padx=2)

        # Remote Input & Keyevents (Bonus from ADB class methods!)
        c4 = self._card(self.tab_device, "4. Remote Keys & Touch Input")
        key_row = ttk.Frame(c4, style="Card.TFrame")
        key_row.pack(fill="x", pady=2)
        for label, key in [
            ("🏠 Home", "KEYCODE_HOME"),
            ("🔙 Back", "KEYCODE_BACK"),
            ("🗂️ Recents", "KEYCODE_APP_SWITCH"),
            ("🔉 Vol -", "KEYCODE_VOLUME_DOWN"),
            ("🔊 Vol +", "KEYCODE_VOLUME_UP"),
            ("☀️ Wake", "KEYCODE_WAKEUP"),
            ("🔒 Power", "KEYCODE_POWER"),
        ]:
            ttk.Button(
                key_row,
                text=label,
                command=lambda k=key: self.action_keyevent(k),
            ).pack(side="left", padx=2, expand=True, fill="x")

        inp_row = ttk.Frame(c4, style="Card.TFrame")
        inp_row.pack(fill="x", pady=(8, 4))
        ttk.Label(inp_row, text="Type Text:", style="Card.TLabel").pack(side="left")
        ttk.Entry(inp_row, textvariable=self.input_text_var, width=18).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(inp_row, text="⌨️ Send Text", command=self.action_send_text).pack(side="left", padx=3)
        ttk.Button(inp_row, text="🔓 Wake & Swipe Up", style="Blue.TButton", command=self.action_wake_and_unlock).pack(side="left", padx=3)

        tap_row = ttk.Frame(c4, style="Card.TFrame")
        tap_row.pack(fill="x", pady=(2, 4))
        ttk.Label(tap_row, text="Tap X/Y:", style="Card.TLabel").pack(side="left")
        ttk.Entry(tap_row, textvariable=self.tap_x_var, width=6).pack(side="left", padx=4)
        ttk.Entry(tap_row, textvariable=self.tap_y_var, width=6).pack(side="left", padx=2)
        ttk.Button(tap_row, text="👆 Tap", command=self.action_tap).pack(side="left", padx=4)
        ttk.Button(tap_row, text="📋 PC → Phone", style="Blue.TButton", command=self.action_push_pc_clipboard).pack(side="right", padx=(4, 0))
        ttk.Button(tap_row, text="📋 Phone → PC", style="Blue.TButton", command=self.action_pull_phone_clipboard).pack(side="right")

        url_row = ttk.Frame(c4, style="Card.TFrame")
        url_row.pack(fill="x", pady=(2, 2))
        ttk.Label(url_row, text="Open URL:", style="Card.TLabel").pack(side="left")
        url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        url_entry.pack(side="left", padx=6, fill="x", expand=True)
        url_entry.bind("<Return>", lambda _: self.action_open_url())
        ttk.Button(url_row, text="🌐 Open on Phone", style="Accent.TButton", command=self.action_open_url).pack(side="right", padx=3)

    # ─────────────────────────── Tab 2: App Management ───────────────────────────
    def _build_tab_apps(self):
        c1 = self._card(self.tab_apps, "1. Install & Batch Install APKs")
        row1 = ttk.Frame(c1, style="Card.TFrame")
        row1.pack(fill="x")
        ttk.Button(row1, text="📲 Install APK File...", style="Accent.TButton", command=self.action_install_apk).pack(side="left", padx=4, expand=True, fill="x")
        ttk.Button(row1, text="📦 Batch Install Folder...", style="Blue.TButton", command=self.action_batch_install).pack(side="left", padx=4, expand=True, fill="x")

        c2 = self._card(self.tab_apps, "2. Package Inspector & 1-Click Picker")
        row2 = ttk.Frame(c2, style="Card.TFrame")
        row2.pack(fill="x", pady=(0, 6))
        ttk.Label(row2, text="Filter:", style="Card.TLabel").pack(side="left")
        filter_entry = ttk.Entry(row2, textvariable=self.pkg_filter_var, width=22)
        filter_entry.pack(side="left", padx=6, fill="x", expand=True)
        filter_entry.bind("<Return>", lambda _: self.action_list_packages())
        ttk.Button(row2, text="📃 List Packages", command=self.action_list_packages).pack(side="left", padx=4)
        ttk.Button(row2, text="🎯 Current Activity", command=self.action_current_activity).pack(side="left", padx=4)

        self.apps_tree = ttk.Treeview(c2, columns=("name", "package"), show="headings", height=6)
        self.apps_tree.heading("name", text="App Name (ক্লিক করে সিলেক্ট করুন)")
        self.apps_tree.heading("package", text="Package Name")
        self.apps_tree.column("name", width=180)
        self.apps_tree.column("package", width=290)
        self.apps_tree.pack(fill="both", expand=True)
        self.apps_tree.bind("<<TreeviewSelect>>", self._on_app_tree_select)

        c3 = self._card(self.tab_apps, "3. Single Package Operations")
        pkg_row = ttk.Frame(c3, style="Card.TFrame")
        pkg_row.pack(fill="x", pady=(0, 8))
        ttk.Label(pkg_row, text="Package Name:", style="Card.TLabel").pack(side="left")
        ttk.Entry(pkg_row, textvariable=self.pkg_name_var).pack(side="left", padx=6, fill="x", expand=True)

        act_grid = ttk.Frame(c3, style="Card.TFrame")
        act_grid.pack(fill="x")
        ttk.Button(act_grid, text="▶️ Start App", style="Accent.TButton", command=self.action_start_app).grid(row=0, column=0, padx=3, pady=3, sticky="ew")
        ttk.Button(act_grid, text="🛑 Force Stop", command=self.action_force_stop).grid(row=0, column=1, padx=3, pady=3, sticky="ew")
        ttk.Button(act_grid, text="ℹ️ Package Info", command=self.action_package_info).grid(row=0, column=2, padx=3, pady=3, sticky="ew")
        ttk.Button(act_grid, text="💾 Backup APK", style="Blue.TButton", command=self.action_backup_apk).grid(row=1, column=0, padx=3, pady=3, sticky="ew")
        ttk.Button(act_grid, text="🧹 Clear Data", command=self.action_clear_data).grid(row=1, column=1, padx=3, pady=3, sticky="ew")
        ttk.Button(act_grid, text="🗑️ Uninstall App", style="Danger.TButton", command=self.action_uninstall_app).grid(row=1, column=2, padx=3, pady=3, sticky="ew")
        for col in range(3):
            act_grid.columnconfigure(col, weight=1)

        perm_row = ttk.Frame(c3, style="Card.TFrame")
        perm_row.pack(fill="x", pady=(8, 2))
        ttk.Label(perm_row, text="Permission:", style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            perm_row,
            textvariable=self.perm_var,
            values=[
                "android.permission.CAMERA",
                "android.permission.RECORD_AUDIO",
                "android.permission.ACCESS_FINE_LOCATION",
                "android.permission.READ_EXTERNAL_STORAGE",
                "android.permission.WRITE_EXTERNAL_STORAGE",
                "android.permission.READ_MEDIA_IMAGES",
                "android.permission.POST_NOTIFICATIONS",
                "android.permission.READ_CONTACTS",
                "android.permission.CALL_PHONE",
            ],
            width=30,
        ).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(perm_row, text="✅ Grant", style="Accent.TButton", command=self.action_grant_perm).pack(side="left", padx=2)
        ttk.Button(perm_row, text="🚫 Revoke", style="Danger.TButton", command=self.action_revoke_perm).pack(side="left", padx=2)
        ttk.Button(perm_row, text="📋 Perms", command=self.action_list_perms).pack(side="left", padx=2)

    # ─────────────────────────── Tab 3: File Operations ───────────────────────────
    def _build_tab_files(self):
        c1 = self._card(self.tab_files, "1. Screenshots & Screen Recording (ADB)")
        row1 = ttk.Frame(c1, style="Card.TFrame")
        row1.pack(fill="x")
        ttk.Button(row1, text="📸 Take Screenshot", style="Accent.TButton", command=self.action_screenshot).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row1, text="🎞️ Burst Screenshots", command=self.action_multiple_screenshots).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Label(row1, text="Rec Sec:", style="Card.TLabel").pack(side="left", padx=(8, 2))
        ttk.Entry(row1, textvariable=self.record_sec_var, width=5).pack(side="left", padx=2)
        ttk.Button(row1, text="🎥 Record Screen", style="Blue.TButton", command=self.action_record_screen).pack(side="left", padx=3)

        c3 = self._card(self.tab_files, "2. File Transfer, Folder Backup & Auto-Sync Watch")
        tr_btns = ttk.Frame(c3, style="Card.TFrame")
        tr_btns.pack(fill="x")
        ttk.Button(tr_btns, text="⬆️ Upload File", style="Accent.TButton", command=self.action_upload_file).pack(side="left", padx=2, expand=True, fill="x")
        ttk.Button(tr_btns, text="⬇️ Download File", style="Blue.TButton", command=self.action_download_file).pack(side="left", padx=2, expand=True, fill="x")
        ttk.Button(tr_btns, text="🔄 Sync Folder", command=self.action_sync_folder).pack(side="left", padx=2, expand=True, fill="x")
        self.btn_auto_sync = ttk.Button(tr_btns, text="👁️ Auto-Sync Watch", command=self.toggle_auto_sync_watch)
        self.btn_auto_sync.pack(side="left", padx=2, expand=True, fill="x")

        # Live Transfer Progress Card
        c4 = self._card(self.tab_files, "3. Live Transfer Progress (Unlimited File Size)")
        top_prog = ttk.Frame(c4, style="Card.TFrame")
        top_prog.pack(fill="x", pady=(0, 4))
        ttk.Label(top_prog, textvariable=self.transfer_label_var, style="Card.TLabel").pack(side="left")
        self.btn_cancel_transfer = ttk.Button(
            top_prog,
            text="⏹️ Cancel Transfer",
            style="Danger.TButton",
            command=self.action_cancel_transfer,
            state="disabled",
        )
        self.btn_cancel_transfer.pack(side="right")

        self.transfer_bar = ttk.Progressbar(
            c4,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            variable=self.transfer_pct_var,
            style="Horizontal.TProgressbar",
        )
        self.transfer_bar.pack(fill="x", pady=6)
        ttk.Label(
            c4,
            textvariable=self.transfer_stats_var,
            style="Muted.TLabel",
            font=("Consolas", 10, "bold"),
        ).pack(anchor="w")

        # 4. Interactive Phone File Explorer
        c5 = self._card(self.tab_files, "4. 📂 Interactive Phone File Explorer")

        # Quick folder shortcut pills
        short_row = ttk.Frame(c5, style="Card.TFrame")
        short_row.pack(fill="x", pady=(0, 6))
        for lbl, pth in [
            ("🏠 /sdcard", "/sdcard"),
            ("📷 DCIM", "/sdcard/DCIM/Camera"),
            ("🖼️ Screenshots", "/sdcard/Pictures/Screenshots"),
            ("⬇️ Download", "/sdcard/Download"),
            ("📄 Docs", "/sdcard/Documents"),
        ]:
            ttk.Button(
                short_row,
                text=lbl,
                command=lambda p=pth: self.explorer_navigate(p),
            ).pack(side="left", padx=2, expand=True, fill="x")

        # Path bar & live filter
        nav_row = ttk.Frame(c5, style="Card.TFrame")
        nav_row.pack(fill="x", pady=(0, 6))
        ttk.Button(nav_row, text="⬆️ Up", command=self.explorer_go_up).pack(side="left", padx=(0, 4))
        self.explorer_path_var = tk.StringVar(value="/sdcard")
        exp_path_entry = ttk.Entry(nav_row, textvariable=self.explorer_path_var)
        exp_path_entry.pack(side="left", padx=4, fill="x", expand=True)
        exp_path_entry.bind("<Return>", lambda _: self.explorer_navigate(self.explorer_path_var.get()))
        ttk.Button(nav_row, text="🔄 Open", style="Blue.TButton", command=lambda: self.explorer_navigate(self.explorer_path_var.get())).pack(side="left", padx=3)

        self.explorer_filter_var = tk.StringVar()
        self.explorer_filter_var.trace_add("write", lambda *_: self._explorer_render())
        ttk.Label(nav_row, text="🔍", style="Card.TLabel").pack(side="left", padx=(6, 2))
        ttk.Entry(nav_row, textvariable=self.explorer_filter_var, width=12).pack(side="left", padx=2)

        # File & folder list
        exp_wrap = ttk.Frame(c5, style="Card.TFrame")
        exp_wrap.pack(fill="both", expand=True, pady=(0, 6))
        self.explorer_tree = ttk.Treeview(
            exp_wrap,
            columns=("name", "size", "modified", "type", "path"),
            displaycolumns=("name", "size", "modified"),
            show="headings",
            height=11,
        )
        self.explorer_tree.heading("name", text="Name (ডাবল-ক্লিক: ফোল্ডার ওপেন / ফাইল ডাউনলোড)")
        self.explorer_tree.heading("size", text="Size")
        self.explorer_tree.heading("modified", text="Modified")
        self.explorer_tree.column("name", width=310, stretch=True)
        self.explorer_tree.column("size", width=85, stretch=False)
        self.explorer_tree.column("modified", width=130, stretch=False)
        exp_sc = ttk.Scrollbar(exp_wrap, orient="vertical", command=self.explorer_tree.yview)
        self.explorer_tree.configure(yscrollcommand=exp_sc.set)
        self.explorer_tree.pack(side="left", fill="both", expand=True)
        exp_sc.pack(side="right", fill="y")
        self.explorer_tree.bind("<<TreeviewSelect>>", self._explorer_on_select)
        self.explorer_tree.bind("<Double-1>", self._explorer_on_double_click)

        # Action toolbar
        act_row = ttk.Frame(c5, style="Card.TFrame")
        act_row.pack(fill="x")
        ttk.Button(act_row, text="⬇️ Download", style="Accent.TButton", command=self.explorer_download_selected).pack(side="left", padx=2)
        ttk.Button(act_row, text="⬆️ Upload Here", style="Blue.TButton", command=self.explorer_upload_here).pack(side="left", padx=2)
        ttk.Button(act_row, text="➕ New Folder", command=self.explorer_new_folder).pack(side="left", padx=2)
        ttk.Button(act_row, text="✏️ Rename", command=self.explorer_rename_selected).pack(side="left", padx=2)
        ttk.Button(act_row, text="🗑️ Delete", style="Danger.TButton", command=self.explorer_delete_selected).pack(side="left", padx=2)
        self.explorer_count_var = tk.StringVar(value="0 items")
        ttk.Label(act_row, textvariable=self.explorer_count_var, style="Muted.TLabel").pack(side="right", padx=6)

        self._explorer_entries: List[dict] = []

    # ─────────────────────────── Tab 4: System Monitoring ───────────────────────────
    def _build_tab_monitor(self):
        c1 = self._card(self.tab_monitor, "1. Hardware & OS Diagnostics")
        grid = ttk.Frame(c1, style="Card.TFrame")
        grid.pack(fill="x")
        items = [
            ("🔋 Battery Info", self.action_battery_info),
            ("🧠 Memory (RAM)", self.action_memory_info),
            ("⚡ CPU Top Processes", self.action_cpu_info),
            ("💾 Storage (df -h)", self.action_storage_info),
            ("⚙️ Running Processes", self.action_processes),
            ("🌐 Network & IP Routes", self.action_network_info),
            ("📋 System Properties", self.action_system_properties),
            ("📊 Apps Storage Size", self.action_apps_with_size),
            ("🧩 System Services", self.action_service_status),
        ]
        for idx, (label, cmd) in enumerate(items):
            r, c = divmod(idx, 3)
            ttk.Button(grid, text=label, command=cmd).grid(row=r, column=c, padx=4, pady=4, sticky="ew")
        for col in range(3):
            grid.columnconfigure(col, weight=1)

        c2 = self._card(self.tab_monitor, "2. Live Performance Monitor (Auto-Refresh)")
        ttk.Label(
            c2,
            text="প্রতি ৩ সেকেন্ড পরপর ফোনের ব্যাটারি, র‍্যাম ও সিপিইউ স্ট্যাটাস লাইভ আপডেট করবে।",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        row_live = ttk.Frame(c2, style="Card.TFrame")
        row_live.pack(fill="x")
        self.btn_perf_toggle = ttk.Button(
            row_live,
            text="▶️ Start Live Performance Monitor",
            style="Accent.TButton",
            command=self.toggle_performance_monitor,
        )
        self.btn_perf_toggle.pack(side="left", padx=4)

    # ─────────────────────────── Tab 5: Screen Sharing (scrcpy) ───────────────────────────
    def _build_tab_scrcpy(self):
        c1 = self._card(self.tab_scrcpy, "1. Quick Screen Mirroring & Background Camera (scrcpy)")
        row1 = ttk.Frame(c1, style="Card.TFrame")
        row1.pack(fill="x")
        ttk.Button(row1, text="▶️ Start Screen Sharing", style="Accent.TButton", command=self.action_start_scrcpy).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row1, text="📴 Screen-Off Mode (-S -w)", style="Blue.TButton", command=self.action_screen_off_scrcpy).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row1, text="📱📱 Multi-Device", command=self.action_multi_device_scrcpy).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row1, text="🛑 Stop All", style="Danger.TButton", command=self.action_stop_scrcpy).pack(side="left", padx=3, expand=True, fill="x")

        cam_row = ttk.Frame(c1, style="Card.TFrame")
        cam_row.pack(fill="x", pady=(6, 0))
        ttk.Button(
            cam_row,
            text="📷 Back Camera Stream (Background)",
            style="Accent.TButton",
            command=lambda: self.action_camera_stream("back"),
        ).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(
            cam_row,
            text="🤳 Front Camera Stream (Background)",
            style="Blue.TButton",
            command=lambda: self.action_camera_stream("front"),
        ).pack(side="left", padx=3, expand=True, fill="x")

        c2 = self._card(self.tab_scrcpy, "2. Custom Preset Modes")
        modes = [
            ("1", "WiFi Smooth Mode (ল্যাগ ছাড়া ফাস্ট - 1024p, 4Mbps, 60fps)"),
            ("2", "Turn Phone Screen Off (ফোনের ডিসপ্লে বন্ধ থাকবে, পিসিতে চলবে)"),
            ("3", "Stay Awake + Always on Top (স্ক্রিন সবসময় উপরে ও চালু থাকবে)"),
            ("4", "Read-Only Mode (শুধু দেখা যাবে, মাউস/কিবোর্ড কাজ করবে না)"),
            ("5", "Custom Arguments (নিচের বক্সে নিজের ইচ্ছেমতো অপশন দিন)"),
        ]
        for val, text in modes:
            ttk.Radiobutton(
                c2,
                text=text,
                variable=self.scrcpy_preset_var,
                value=val,
            ).pack(anchor="w", pady=2)

        cust_row = ttk.Frame(c2, style="Card.TFrame")
        cust_row.pack(fill="x", pady=(6, 2))
        ttk.Label(cust_row, text="Custom Args:", style="Card.TLabel").pack(side="left")
        ttk.Entry(cust_row, textvariable=self.scrcpy_extra_var).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(cust_row, text="🚀 Launch Custom scrcpy", style="Accent.TButton", command=self.action_custom_scrcpy).pack(side="right")

        c3 = self._card(self.tab_scrcpy, "3. Screen-Off Recovery & Recording Tools")
        ttk.Label(
            c3,
            text=(
                "💡 টিপস: ফোনের ফিজিক্যাল Power বাটন চাপলে ফোন লক হয়ে যায়। তাই scrcpy উইন্ডোর ভেতরে:\n"
                "   • ফোনের স্ক্রিন বন্ধ করতে কিবোর্ডে চাপুন:  Alt + O   (ফোন লক হবে না, ডিসপ্লে অফ থাকবে)\n"
                "   • ফোনের স্ক্রিন চালু করতে কিবোর্ডে চাপুন:  Alt + Shift + O"
            ),
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        row3a = ttk.Frame(c3, style="Card.TFrame")
        row3a.pack(fill="x", pady=(0, 6))
        ttk.Button(row3a, text="🔓 Wake & Unlock", command=self.action_wake_and_unlock).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row3a, text="🔴 Record via scrcpy", style="Blue.TButton", command=self.action_scrcpy_record).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row3a, text="⌨️ HID Keyboard/Mouse", style="Accent.TButton", command=self.action_launch_hid_mode).pack(side="left", padx=3, expand=True, fill="x")

        row3b = ttk.Frame(c3, style="Card.TFrame")
        row3b.pack(fill="x")
        ttk.Button(row3b, text="🔍 Active Sessions", command=self.action_show_scrcpy_sessions).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(row3b, text="✔️ Check scrcpy", command=self.action_check_scrcpy).pack(side="left", padx=3, expand=True, fill="x")

        c4 = self._card(self.tab_scrcpy, "4. Virtual Display Mode (ফোনের স্ক্রিন ছাড়াই আলাদা উইন্ডোতে অ্যাপ)")
        vd_row = ttk.Frame(c4, style="Card.TFrame")
        vd_row.pack(fill="x", pady=(2, 2))
        ttk.Label(vd_row, text="App:", style="Card.TLabel").pack(side="left")
        self.vd_pkg_var = tk.StringVar(value="com.whatsapp")
        self.vd_pkg_combo = ttk.Combobox(
            vd_row,
            textvariable=self.vd_pkg_var,
            values=[
                "com.whatsapp",
                "com.android.chrome",
                "com.google.android.youtube",
                "com.facebook.katana",
                "org.telegram.messenger",
            ],
            width=18,
        )
        self.vd_pkg_combo.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(vd_row, text="Size:", style="Card.TLabel").pack(side="left", padx=(6, 2))
        self.vd_size_var = tk.StringVar(value="Auto")
        ttk.Combobox(
            vd_row,
            textvariable=self.vd_size_var,
            values=["Auto", "1280x720/240", "1920x1080/320", "1080x1920/320"],
            state="readonly",
            width=16,
        ).pack(side="left", padx=4)
        ttk.Button(vd_row, text="🚀 Launch Virtual App", style="Accent.TButton", command=self.action_launch_virtual_display).pack(side="right", padx=(4, 0))
        ttk.Label(
            c4,
            text="💡 নোট: কিছু ফোনের (যেমন Tecno HiOS) ROM-এ কালো স্ক্রিন দেখালে Developer Options থেকে 'Enable freeform windows' ও 'Force desktop mode' চালু করে ফোন রিস্টার্ট করুন।",
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

    # ─────────────────────────── Tab 6: WiFi & Live Tools ───────────────────────────
    def _build_tab_wifi(self):
        c1 = self._card(self.tab_wifi, "1. Wireless Debugging & QR Code Pairing")
        ttk.Label(
            c1,
            text="Android 11+ ফোনে কোনো ক্যাবল ছাড়াই QR কোড স্ক্যান করে ওয়াইফাই ডিবাগিং পেয়ার ও কানেক্ট করুন।",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        row_qr = ttk.Frame(c1, style="Card.TFrame")
        row_qr.pack(fill="x")
        ttk.Button(
            row_qr,
            text="📷 Pair with WiFi QR Code (Graphical Popup)",
            style="Accent.TButton",
            command=self.open_qr_pairing_wizard,
        ).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(
            row_qr,
            text="⚡ Quick USB → WiFi Auto-Setup",
            style="Blue.TButton",
            command=self.action_quick_wifi_setup,
        ).pack(side="left", padx=3, expand=True, fill="x")

        c2 = self._card(self.tab_wifi, "2. TCP/IP Mode & WiFi Diagnostics")
        row_tcp = ttk.Frame(c2, style="Card.TFrame")
        row_tcp.pack(fill="x")
        ttk.Label(row_tcp, text="TCP Port:", style="Card.TLabel").pack(side="left")
        ttk.Entry(row_tcp, textvariable=self.tcpip_port_var, width=8).pack(side="left", padx=4)
        ttk.Button(row_tcp, text="🔁 Enable TCP/IP", command=self.action_tcpip_mode).pack(side="left", padx=3)
        ttk.Button(row_tcp, text="📡 Transfer All to TCP/IP", command=self.action_transfer_all_tcpip).pack(side="left", padx=3)
        ttk.Button(row_tcp, text="📶 Detailed WiFi Status", style="Blue.TButton", command=self.action_wifi_status).pack(side="left", padx=3)

        c3 = self._card(self.tab_wifi, "3. Live Logcat Streamer & Tool Settings")
        row_stream = ttk.Frame(c3, style="Card.TFrame")
        row_stream.pack(fill="x", pady=(0, 8))
        self.btn_logcat_stream = ttk.Button(
            row_stream,
            text="▶️ Start Live Logcat Stream",
            style="Accent.TButton",
            command=self.toggle_live_logcat,
        )
        self.btn_logcat_stream.pack(side="left", padx=3, expand=True, fill="x")

        row_set = ttk.Frame(c3, style="Card.TFrame")
        row_set.pack(fill="x")
        ttk.Label(row_set, text="Command Timeout (sec):", style="Card.TLabel").pack(side="left")
        ttk.Entry(row_set, textvariable=self.timeout_var, width=7).pack(side="left", padx=6)
        ttk.Button(row_set, text="💾 Save Timeout", command=self.action_save_settings).pack(side="left", padx=4)

    # ─────────────────────────── Tab 7: Quick System Toggles & Display ───────────────────────────
    def _build_tab_controls(self):
        c1 = self._card(self.tab_controls, "1. Quick System Toggles (Mobile Data, Bluetooth, Location, Flashlight)")
        grid = ttk.Frame(c1, style="Card.TFrame")
        grid.pack(fill="x")

        controls = [
            ("📶 Mobile Data ON", lambda: self.run_async("Mobile Data ON", lambda: self.adb.set_mobile_data(True)), "Accent.TButton"),
            ("📴 Mobile Data OFF", lambda: self.run_async("Mobile Data OFF", lambda: self.adb.set_mobile_data(False)), "Danger.TButton"),
            ("🔵 Bluetooth ON", lambda: self.run_async("Bluetooth ON", lambda: self.adb.set_bluetooth(True)), "Blue.TButton"),
            ("⚫ Bluetooth OFF", lambda: self.run_async("Bluetooth OFF", lambda: self.adb.set_bluetooth(False)), "TButton"),
            ("📍 Location ON", lambda: self.run_async("Location ON", lambda: self.adb.set_location(True)), "Accent.TButton"),
            ("🚫 Location OFF", lambda: self.run_async("Location OFF", lambda: self.adb.set_location(False)), "TButton"),
            ("🔦 Flashlight ON", lambda: self.run_async("Flashlight ON", lambda: self.adb.set_flashlight(True)), "Blue.TButton"),
            ("🌑 Flashlight OFF", lambda: self.run_async("Flashlight OFF", lambda: self.adb.set_flashlight(False)), "TButton"),
        ]
        for idx, (label, cmd, st) in enumerate(controls):
            r, c = divmod(idx, 2)
            ttk.Button(grid, text=label, style=st, command=cmd).grid(row=r, column=c, padx=5, pady=4, sticky="ew")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        c2 = self._card(self.tab_controls, "2. Screen Brightness Slider (0 - 255)")
        br_row = ttk.Frame(c2, style="Card.TFrame")
        br_row.pack(fill="x")
        ttk.Label(br_row, text="Brightness:", style="Card.TLabel").pack(side="left")
        self.brightness_scale = ctk.CTkSlider(
            br_row,
            from_=0,
            to=255,
            number_of_steps=255,
            variable=self.brightness_var,
            fg_color=BG_INPUT,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
        )
        self.brightness_scale.pack(side="left", padx=10, fill="x", expand=True)
        ttk.Button(br_row, text="☀️ Apply Brightness", style="Accent.TButton", command=self.action_set_brightness).pack(side="right")

        c3 = self._card(self.tab_controls, "3. Screen Auto-Lock Timeout")
        to_row = ttk.Frame(c3, style="Card.TFrame")
        to_row.pack(fill="x")
        ttk.Label(to_row, text="Screen Timeout:", style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            to_row,
            textvariable=self.timeout_preset_var,
            values=["15 Seconds", "30 Seconds", "1 Minute", "2 Minutes", "5 Minutes", "10 Minutes", "30 Minutes"],
            state="readonly",
            width=18,
        ).pack(side="left", padx=8)
        ttk.Button(to_row, text="⏱️ Apply Timeout", style="Blue.TButton", command=self.action_set_timeout).pack(side="left", padx=4)

    # ─────────────────────────── Tab 8: Device Profiles & 1-Click Unlock ───────────────────────────
    def _build_tab_profiles(self):
        c1 = self._card(self.tab_profiles, "1. Device Fingerprint & Profile Editor")

        fp_row = ttk.Frame(c1, style="Card.TFrame")
        fp_row.pack(fill="x", pady=3)
        ttk.Label(fp_row, text="Fingerprint:", style="Card.TLabel", width=15).pack(side="left")
        ttk.Entry(fp_row, textvariable=self.prof_fp_var).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(
            fp_row,
            text="🔍 Detect Connected Device",
            style="Blue.TButton",
            command=self.action_detect_fingerprint,
        ).pack(side="right", padx=2)

        name_row = ttk.Frame(c1, style="Card.TFrame")
        name_row.pack(fill="x", pady=3)
        ttk.Label(name_row, text="Device Name:", style="Card.TLabel", width=15).pack(side="left")
        ttk.Entry(name_row, textvariable=self.prof_name_var).pack(side="left", padx=4, fill="x", expand=True)

        type_row = ttk.Frame(c1, style="Card.TFrame")
        type_row.pack(fill="x", pady=3)
        ttk.Label(type_row, text="Lock Type:", style="Card.TLabel", width=15).pack(side="left")
        ttk.Combobox(
            type_row,
            textvariable=self.prof_lock_type_var,
            values=LOCK_TYPES,
            state="readonly",
            width=22,
        ).pack(side="left", padx=4)
        ttk.Label(type_row, text="Unlock Process:", style="Card.TLabel").pack(side="left", padx=(10, 4))
        ttk.Combobox(
            type_row,
            textvariable=self.prof_process_var,
            values=UNLOCK_PROCESSES,
            state="readonly",
        ).pack(side="left", padx=4, fill="x", expand=True)

        val_row = ttk.Frame(c1, style="Card.TFrame")
        val_row.pack(fill="x", pady=3)
        ttk.Label(val_row, text="Unlock Value:", style="Card.TLabel", width=15).pack(side="left")
        self.prof_val_entry = ttk.Entry(val_row, textvariable=self.prof_value_var, show="*")
        self.prof_val_entry.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(
            val_row,
            text="👁️ Show/Hide",
            command=self._toggle_prof_value_visibility,
        ).pack(side="right", padx=2)

        coord_row = ttk.Frame(c1, style="Card.TFrame")
        coord_row.pack(fill="x", pady=3)
        ttk.Label(coord_row, text="Swipe Coords:", style="Card.TLabel", width=15).pack(side="left")
        ttk.Entry(coord_row, textvariable=self.prof_coords_var, width=22).pack(side="left", padx=4)
        ttk.Label(coord_row, text="Notes:", style="Card.TLabel").pack(side="left", padx=(10, 4))
        ttk.Entry(coord_row, textvariable=self.prof_notes_var).pack(side="left", padx=4, fill="x", expand=True)

        btn_row = ttk.Frame(c1, style="Card.TFrame")
        btn_row.pack(fill="x", pady=(8, 2))
        ttk.Button(btn_row, text="💾 Save Profile", style="Accent.TButton", command=self.action_save_profile).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(btn_row, text="🔓 1-Click Unlock", style="Blue.TButton", command=self.action_one_click_unlock).pack(side="left", padx=3, expand=True, fill="x")
        ttk.Button(btn_row, text="🗑️ Delete Profile", style="Danger.TButton", command=self.action_delete_profile).pack(side="left", padx=3, expand=True, fill="x")

        c2 = self._card(self.tab_profiles, "2. ইউনিভার্সাল ডিভাইস সিঙ্ক ও আনলক টিপস")
        ttk.Label(
            c2,
            text=(
                "• ওপরের 'Active Device' ড্রপডাউন থেকে যে ডিভাইসটি সিলেক্ট করবেন, তার Fingerprint ও সেভড প্রোফাইল\n"
                "  স্বয়ংক্রিয়ভাবে এখানে লোড হয়ে যাবে—আলাদাভাবে প্রোফাইল সিলেক্ট করার প্রয়োজন নেই।\n"
                "• Swipe Coords 'auto' রাখলে ফোনের আসল রেজোলিউশন (wm size) অনুযায়ী স্বয়ংক্রিয়ভাবে সোয়াইপ হবে।\n"
                "• শুধু স্ক্রিন Wake Up হয়ে আটকে গেলে ফোনের Developer Options-এ\n"
                "  'USB debugging (Security settings)' চালু আছে কি না নিশ্চিত করুন।"
            ),
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(
            c2,
            text="📂 Manage All Saved Profiles (অফলাইন/পুরনো প্রোফাইল দেখুন বা মুছুন)",
            command=self.open_saved_profiles_manager,
        ).pack(anchor="w")

    # ─────────────────────────── Right Pane: Console Output ───────────────────────────
    def _build_console_pane(self, parent):
        box = ctk.CTkFrame(
            parent,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        box.pack(fill="both", expand=True)

        hdr = ttk.Frame(box)
        hdr.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(hdr, text="🖥️ Output Console", style="SubHeader.TLabel").pack(side="left")

        ttk.Button(hdr, text="🗑️ Clear", width=7, command=self.clear_console).pack(side="right", padx=2)
        ttk.Button(hdr, text="💾 Save", width=7, command=self.save_console).pack(side="right", padx=2)
        ttk.Button(hdr, text="📋 Copy", width=7, command=self.copy_console).pack(side="right", padx=2)

        self.console_text = ScrolledText(
            box,
            bg=BG_INPUT,
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=("Consolas", 10),
            relief="flat",
            wrap="word",
            padx=10,
            pady=10,
        )
        self.console_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Configure color tags
        self.console_text.tag_configure("title", foreground=ACCENT, font=("Consolas", 11, "bold"))
        self.console_text.tag_configure("info", foreground="#38bdf8")
        self.console_text.tag_configure("warn", foreground=WARNING)
        self.console_text.tag_configure("error", foreground=DANGER, font=("Consolas", 10, "bold"))
        self.console_text.tag_configure("muted", foreground=FG_MUTED)

        self.write_console(
            "ADB Control Desktop UI চালু হয়েছে!\n"
            "• বাম পাশের ট্যাবগুলো থেকে যেকোনো ফিচারে ক্লিক করুন।\n"
            "• সব ADB কমান্ড ব্যাকগ্রাউন্ড থ্রেডে রান হয়, তাই UI কখনো হ্যাং করবে না।",
            title="Welcome",
        )

    # ─────────────────────────── Console & Async Helpers ───────────────────────────
    def write_console(self, content: str, title: str = "Output", clear: bool = False):
        if clear:
            self.console_text.delete("1.0", "end")

        timestamp = time.strftime("%H:%M:%S")
        self.console_text.insert("end", f"\n━━━ [{timestamp}] {title} ━━━\n", "title")

        for line in (content or "Done").splitlines():
            if " E " in line or line.lower().startswith("error"):
                self.console_text.insert("end", line + "\n", "error")
            elif " W " in line or "warning" in line.lower():
                self.console_text.insert("end", line + "\n", "warn")
            elif line.startswith("═══") or line.startswith("---"):
                self.console_text.insert("end", line + "\n", "info")
            else:
                self.console_text.insert("end", line + "\n")

        self.console_text.see("end")

    def clear_console(self):
        self.console_text.delete("1.0", "end")

    def copy_console(self):
        text = self.console_text.get("1.0", "end").strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("আউটপুট ক্লিপবোর্ডে কপি করা হয়েছে।", ACCENT)

    def save_console(self):
        path = filedialog.asksaveasfilename(
            title="Save Output",
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("Log Files", "*.log"), ("All Files", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.console_text.get("1.0", "end"))
            self.set_status(f"ফাইল সেভ করা হয়েছে: {path}", ACCENT)

    def set_status(self, message: str, color: str = ACCENT):
        self.status_var.set(message)
        self.status_dot.configure(fg=color)

    def run_async(self, title: str, func: Callable[[], str], require_device: bool = True):
        """Runs a blocking ADB operation in a background thread safely."""
        if require_device and not self._ensure_device_selected():
            return

        self.set_status(f"Running: {title}...", WARNING)

        def worker():
            try:
                result = func()
                self.root.after(0, lambda: self._on_task_done(title, result, None))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._on_task_done(title, "", e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_task_done(self, title: str, result: str, error: Optional[Exception]):
        if error:
            self.write_console(f"Error: {error}", title=f"❌ {title} Failed")
            self.set_status(f"Error in {title}: {error}", DANGER)
        else:
            self.write_console(result or "Done", title=f"✔ {title}")
            dev_label = self.adb.serial or "auto"
            self.set_status(f"Ready (Active Device: {dev_label})", ACCENT)

    # ─────────────────────────── Device Selection ───────────────────────────
    def refresh_devices(self):
        def worker():
            try:
                devices = ADB.list_devices()
                self.root.after(0, lambda: self._update_device_list(devices))
            except Exception as e:
                self.root.after(0, lambda err=e: self.set_status(f"ADB Error: {err}", DANGER))

        threading.Thread(target=worker, daemon=True).start()

    def _update_device_list(self, devices: List[dict]):
        ready = [d for d in devices if d["state"] == "device"]
        serials = [d["serial"] for d in ready]
        self.device_combo["values"] = serials

        if self.adb.serial and self.adb.serial in serials:
            self.device_var.set(self.adb.serial)
            self._auto_sync_active_profile()
        elif serials:
            self.adb.serial = serials[0]
            self.device_var.set(serials[0])
            self.set_status(f"Connected: {self.adb.serial} ({len(serials)} device ready)", ACCENT)
            self._auto_sync_active_profile()
        else:
            self.adb.serial = None
            self.device_var.set("")
            self.set_status("কোনো কানেক্টেড ডিভাইস পাওয়া যায়নি", WARNING)

    def _on_device_selected(self, _event=None):
        chosen = self.device_var.get().strip()
        if chosen:
            self.adb.serial = chosen
            self.set_status(f"Active Device: {chosen}", ACCENT)
            self._auto_sync_active_profile()

    def _ensure_device_selected(self) -> bool:
        chosen = self.device_var.get().strip()
        if chosen:
            self.adb.serial = chosen
            return True
        try:
            devices = [d for d in ADB.list_devices() if d["state"] == "device"]
        except ADBError as e:
            messagebox.showerror("ADB Error", str(e))
            return False

        if not devices:
            messagebox.showwarning("No Device", "কোনো কানেক্টেড ডিভাইস পাওয়া যায়নি!\nUSB বা WiFi দিয়ে ফোন কানেক্ট করে Refresh চাপুন।")
            return False

        self.adb.serial = devices[0]["serial"]
        self.device_var.set(self.adb.serial)
        self.device_combo["values"] = [d["serial"] for d in devices]
        return True

    # ─────────────────────────── Tab 1 Actions: Device ───────────────────────────
    def action_list_devices(self):
        def task():
            devices = ADB.list_devices()
            self.root.after(0, lambda: self._update_device_list(devices))
            if not devices:
                return "কোনো ডিভাইস কানেক্টেড নেই।"
            lines = [f"{'SERIAL':<24} {'STATE':<12} DETAILS", "─" * 65]
            for d in devices:
                lines.append(f"{d['serial']:<24} {d['state']:<12} {d['details']}")
            return "\n".join(lines)

        self.run_async("List Devices", task, require_device=False)

    def action_connect_wifi(self):
        target = self.ip_port_var.get().strip()
        if not target:
            messagebox.showwarning("Input Required", "অনুগ্রহ করে IP:PORT দিন (যেমন: 192.168.1.100:5555)")
            return

        def task():
            out = self.adb.connect(target)
            self.root.after(200, self.refresh_devices)
            return out

        self.run_async(f"Connect {target}", task, require_device=False)

    def action_disconnect_device(self):
        target = self.ip_port_var.get().strip() or None

        def task():
            self.adb.serial = None
            out = self.adb.disconnect(target)
            self.root.after(200, self.refresh_devices)
            return out

        self.run_async("Disconnect Device", task, require_device=False)

    def action_wait_device(self):
        self.run_async("Wait for Device", self.adb.wait, require_device=False)

    def action_restart_adb(self):
        def task():
            out1 = subprocess.run(
                ["adb", "kill-server"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
            )
            out2 = subprocess.run(
                ["adb", "start-server"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
            )
            self.adb.serial = None
            self.root.after(300, self.refresh_devices)
            return (out1.stdout + out1.stderr + "\n" + out2.stdout + out2.stderr).strip() or "ADB Server Restarted."

        self.run_async("Restart ADB Server", task, require_device=False)

    def action_check_connection(self):
        def task():
            info = self.adb.info()
            return "\n".join(f"{k:<15}: {v}" for k, v in info.items())

        self.run_async("Device Info", task, require_device=True)

    def action_exec_shell(self):
        cmd = self.shell_cmd_var.get().strip()
        if not cmd:
            return
        self.run_async(f"Shell: {cmd}", lambda: self.adb.shell(cmd, check=False), require_device=True)

    def action_get_logs(self):
        try:
            lines = max(1, int(self.log_lines_var.get().strip()))
        except ValueError:
            lines = 150
        mode = self.log_mode_var.get()
        keyword = self.log_keyword_var.get().strip()

        def task():
            if "Errors" in mode:
                raw = self.adb.run(["logcat", "-d", "-t", str(lines), "*:E"], check=False)
            elif "Keyword" in mode and keyword:
                raw = self.adb.run(["logcat", "-d", "-t", str(lines)], check=False)
                raw = "\n".join(l for l in raw.splitlines() if keyword.lower() in l.lower())
            else:
                raw = self.adb.logcat(lines)
            return raw or "কোনো লগ পাওয়া যায়নি।"

        self.run_async("Logcat Snapshot", task, require_device=True)

    def action_reboot_device(self):
        if not messagebox.askyesno("Confirm Reboot", "আপনি কি নিশ্চিতভাবে ডিভাইসটি Reboot করতে চান?"):
            return
        self.run_async("Reboot Device", self.adb.reboot, require_device=True)

    def action_keyevent(self, keycode: str):
        self.run_async(f"Keyevent {keycode}", lambda: self.adb.keyevent(keycode), require_device=True)

    def action_send_text(self):
        txt = self.input_text_var.get()
        if not txt:
            return
        if not txt.isascii():
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self.root.update_idletasks()
            self.run_async("Send Unicode Text", lambda: self.adb.send_unicode_text(txt, auto_paste=True), require_device=True)
            return
        self.run_async("Send Text", lambda: self.adb.input_text(txt, delay_after_first=1.0), require_device=True)

    def action_tap(self):
        try:
            x = int(self.tap_x_var.get().strip())
            y = int(self.tap_y_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid Coordinates", "X এবং Y অবশ্যই পূর্ণসংখ্যা হতে হবে।")
            return
        self.run_async(f"Tap ({x}, {y})", lambda: self.adb.tap(x, y), require_device=True)

    def action_wake_and_unlock(self):
        def task():
            self.adb.shell("svc power stayon true", check=False)
            self.adb.keyevent("KEYCODE_WAKEUP")
            time.sleep(1.0)
            coords = _resolve_swipe_coords(self.adb, "auto")
            self.adb.swipe(*coords, duration=400)
            return (
                "ফোন Wake ও Swipe-Up করা হয়েছে!\n"
                "💡 পিসি থেকে লক স্ক্রিনে মাউস/টাচ কাজ না করলে ফোনের Developer Options থেকে\n"
                "   'USB debugging (Security settings)' অপশনটি চালু (Enable) করুন।"
            )

        self.run_async("Wake & Unlock", task, require_device=True)

    # ─────────────────────────── Tab 2 Actions: Apps ───────────────────────────
    def action_install_apk(self):
        apk_path = filedialog.askopenfilename(
            title="Select APK to Install",
            filetypes=[("Android Package", "*.apk"), ("All Files", "*.*")],
        )
        if not apk_path:
            return
        self.run_async(f"Install {os.path.basename(apk_path)}", lambda: self.adb.install_apk(apk_path))

    def action_batch_install(self):
        folder = filedialog.askdirectory(title="Select Folder Containing APKs")
        if not folder:
            return
        apks = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".apk")]
        if not apks:
            messagebox.showinfo("No APKs", "নির্বাচিত ফোল্ডারে কোনো .apk ফাইল পাওয়া যায়নি।")
            return

        def task():
            results = [f"মোট {len(apks)}টি APK পাওয়া গেছে:\n"]
            for path in apks:
                name = os.path.basename(path)
                try:
                    out = self.adb.install_apk(path)
                    results.append(f"✔ {name}: {out}")
                except Exception as e:
                    results.append(f"❌ {name}: {e}")
            return "\n".join(results)

        self.run_async("Batch APK Install", task)

    def _on_app_tree_select(self, _event=None):
        sel = self.apps_tree.selection()
        if not sel:
            return
        vals = self.apps_tree.item(sel[0], "values")
        if len(vals) >= 2:
            self.pkg_name_var.set(vals[1])
            self.set_status(f"Selected App: {vals[0]} ({vals[1]})", ACCENT)

    def _populate_apps_tree(self, apps: List[dict]):
        for item in self.apps_tree.get_children():
            self.apps_tree.delete(item)
        for a in apps:
            self.apps_tree.insert("", "end", values=(a["name"], a["package"]))

    def action_list_packages(self):
        flt = self.pkg_filter_var.get().strip().lower()

        def task():
            apps = self.adb.list_launcher_apps()
            if flt:
                apps = [
                    a for a in apps
                    if flt in a["name"].lower() or flt in a["package"].lower()
                ]
            self.root.after(0, lambda data=apps: self._populate_apps_tree(data))
            if not apps:
                return "অ্যাপ মেনুতে কোনো ম্যাচিং অ্যাপ পাওয়া যায়নি।"

            lines = [
                f"মোট অ্যাপ (Launcher Icons): {len(apps)}",
                f"{'APP NAME':<28} {'PACKAGE NAME'}",
                "─" * 70,
            ]
            for a in apps:
                lines.append(f"{a['name']:<28} {a['package']}")
            return "\n".join(lines)

        self.run_async("List Launcher Apps", task)

    def action_current_activity(self):
        self.run_async("Current Activity", self.adb.current_activity)

    def _require_package_name(self) -> Optional[str]:
        pkg = self.pkg_name_var.get().strip()
        if not pkg:
            messagebox.showwarning("Package Required", "অনুগ্রহ করে Package Name লিখুন (যেমন: com.whatsapp)")
            return None
        return pkg

    def action_start_app(self):
        pkg = self._require_package_name()
        if pkg:
            self.run_async(f"Start {pkg}", lambda: self.adb.start_app(pkg))

    def action_force_stop(self):
        pkg = self._require_package_name()
        if pkg:
            self.run_async(f"Force Stop {pkg}", lambda: self.adb.force_stop(pkg))

    def action_package_info(self):
        pkg = self._require_package_name()
        if pkg:
            self.run_async(f"Package Info: {pkg}", lambda: self.adb.package_info(pkg))

    def action_clear_data(self):
        pkg = self._require_package_name()
        if pkg and messagebox.askyesno("Confirm Clear Data", f"আপনি কি {pkg}-এর সব ডাটা মুছে ফেলতে চান?"):
            self.run_async(f"Clear Data: {pkg}", lambda: self.adb.clear_data(pkg))

    def action_uninstall_app(self):
        pkg = self._require_package_name()
        if pkg and messagebox.askyesno("Confirm Uninstall", f"আপনি কি {pkg} আনইনস্টল করতে চান?"):
            self.run_async(f"Uninstall {pkg}", lambda: self.adb.uninstall_app(pkg))

    def action_backup_apk(self):
        pkg = self._require_package_name()
        if not pkg:
            return
        save_path = filedialog.asksaveasfilename(
            title="Save Extracted APK As",
            initialfile=f"{pkg}.apk",
            defaultextension=".apk",
            filetypes=[("APK File", "*.apk")],
        )
        if not save_path:
            return

        if not self._ensure_device_selected():
            return

        path_out = self.adb.shell(f"pm path {shlex.quote(pkg)}", check=False)
        lines = [l.replace("package:", "").strip() for l in path_out.splitlines() if l.startswith("package:")]
        if not lines:
            messagebox.showerror("APK Not Found", f"'{pkg}' প্যাকেজের APK পাথ পাওয়া যায়নি।")
            return
        remote_apk = lines[0]
        self._start_transfer_with_progress(
            title=f"Backup APK ({pkg})",
            is_push=False,
            src=remote_apk,
            dst=save_path,
        )

    # ─────────────────────────── Tab 3 Actions: Files & Live Progress ───────────────────────────
    def _update_transfer_ui(self, label: str, done_b: int, total_b: int, pct: float, speed_bps: float):
        done_str = ADB.format_bytes(done_b)
        tot_str = ADB.format_bytes(total_b) if total_b > 0 else "Calculating..."
        spd_str = ADB.format_bytes(speed_bps) + "/s"

        if speed_bps > 0 and total_b > done_b:
            rem_sec = int((total_b - done_b) / speed_bps)
            eta_str = f"{rem_sec // 60}m {rem_sec % 60}s" if rem_sec >= 60 else f"{rem_sec}s"
        elif pct >= 100.0:
            eta_str = "Done"
        else:
            eta_str = "--"

        stats = f"{pct:5.1f}%  •  {done_str} / {tot_str}  •  Speed: {spd_str}  •  ETA: {eta_str}"
        self.transfer_label_var.set(label)
        self.transfer_pct_var.set(pct)
        self.transfer_stats_var.set(stats)
        self.set_status(f"{label} — {stats}", ACCENT if pct >= 100.0 else WARNING)

    def action_cancel_transfer(self):
        if self.transfer_cancel_event:
            self.transfer_cancel_event.set()
            self.set_status("ফাইল ট্রান্সফার বাতিল করা হচ্ছে...", DANGER)

    def _start_transfer_with_progress(self, title: str, is_push: bool, src: str, dst: str):
        if not self._ensure_device_selected():
            return

        if self.transfer_cancel_event is not None:
            messagebox.showwarning("Transfer Busy", "ইতিমধ্যে একটি ফাইল ট্রান্সফার চলছে! শেষ হওয়া পর্যন্ত অপেক্ষা করুন অথবা Cancel চাপুন।")
            return

        cancel_ev = threading.Event()
        self.transfer_cancel_event = cancel_ev
        self.btn_cancel_transfer.configure(state="normal")

        short_name = os.path.basename(src.rstrip("/\\")) or src
        direction = f"⬆️ Uploading: {short_name}" if is_push else f"⬇️ Downloading: {short_name}"
        self.transfer_pct_var.set(0.0)
        self.transfer_label_var.set(direction)
        self.transfer_stats_var.set("Starting transfer...  •  0.0%")
        self.set_status(f"{direction} শুরু হচ্ছে...", WARNING)

        def on_prog(done_b: int, total_b: int, pct: float, speed_bps: float):
            self.root.after(0, lambda: self._update_transfer_ui(direction, done_b, total_b, pct, speed_bps))

        def worker():
            try:
                if is_push:
                    out = self.adb.push(src, dst, progress_callback=on_prog, cancel_event=cancel_ev)
                else:
                    out = self.adb.pull(src, dst, progress_callback=on_prog, cancel_event=cancel_ev)
                self.root.after(0, lambda: self._on_transfer_finished(title, out, None))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._on_transfer_finished(title, "", e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_transfer_finished(self, title: str, result: str, error: Optional[Exception]):
        self.transfer_cancel_event = None
        self.btn_cancel_transfer.configure(state="disabled")
        if error:
            self.transfer_label_var.set(f"❌ {title} ব্যর্থ বা বাতিল হয়েছে")
            self.write_console(f"Error: {error}", title=f"❌ {title} Failed")
            self.set_status(f"Error in {title}: {error}", DANGER)
        else:
            self.transfer_pct_var.set(100.0)
            self.transfer_label_var.set(f"✔ {title} সম্পন্ন হয়েছে (100%)")
            self.write_console(result or "Transfer Completed (100%)", title=f"✔ {title}")
            self.set_status(f"✔ {title} সফলভাবে সম্পন্ন হয়েছে (100%)", ACCENT)

    def action_screenshot(self):
        save_path = filedialog.asksaveasfilename(
            title="Save Screenshot As",
            initialfile=f"screenshot_{int(time.time())}.png",
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
        )
        if not save_path:
            return

        def task():
            res = self.adb.screenshot(save_path)
            clip_msg = self._copy_image_to_clipboard(save_path)
            self.root.after(0, lambda p=save_path: ScreenshotPreviewDialog(self.root, p))
            return f"{res}\n{clip_msg}"

        self.run_async("Take Screenshot", task)

    @staticmethod
    def _copy_image_to_clipboard(image_path: str) -> str:
        if os.name != "nt":
            return "ℹ️ Clipboard image copy শুধু Windows-এ সমর্থিত।"
        abs_path = os.path.abspath(image_path).replace("'", "''")
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            f"$img = [System.Drawing.Image]::FromFile('{abs_path}'); "
            "[System.Windows.Forms.Clipboard]::SetImage($img); "
            "$img.Dispose()"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                return "📋 স্ক্রিনশট পিসির Clipboard-এও কপি করা হয়েছে (Ctrl+V দিয়ে পেস্ট করতে পারবেন)!"
            return f"⚠️ Clipboard কপি ব্যর্থ: {(proc.stderr or '').strip()}"
        except Exception as e:
            return f"⚠️ Clipboard কপি ব্যর্থ: {e}"

    def action_multiple_screenshots(self):
        count = simpledialog.askinteger("Burst Screenshots", "কয়টি স্ক্রিনশট নিতে চান? (1-20):", initialvalue=3, minvalue=1, maxvalue=20)
        if not count:
            return
        interval = simpledialog.askinteger("Interval", "প্রতি স্ক্রিনশটের মাঝে বিরতি (সেকেন্ড):", initialvalue=2, minvalue=1, maxvalue=60)
        if not interval:
            return
        folder = filedialog.askdirectory(title="Select Folder to Save Screenshots")
        if not folder:
            return

        def task():
            saved = []
            for i in range(1, count + 1):
                fname = os.path.join(folder, f"shot_{int(time.time())}_{i}.png")
                self.adb.screenshot(fname)
                saved.append(fname)
                if i < count:
                    time.sleep(interval)
            return "সফলভাবে সেভ হয়েছে:\n" + "\n".join(saved)

        self.run_async(f"Multiple Screenshots ({count}x)", task)

    def action_record_screen(self):
        try:
            seconds = max(1, min(180, int(self.record_sec_var.get().strip())))
        except ValueError:
            seconds = 10
        save_path = filedialog.asksaveasfilename(
            title="Save Screen Recording As",
            initialfile=f"record_{int(time.time())}.mp4",
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4")],
        )
        if save_path:
            self.run_async(f"Record Screen ({seconds}s)", lambda: self.adb.screenrecord(save_path, seconds))

    def action_list_files(self):
        remote = self.remote_path_var.get().strip() or "/sdcard"
        self.run_async(f"List {remote}", lambda: self.adb.ls(remote))

    def action_create_folder(self):
        remote = self.remote_path_var.get().strip()
        if remote:
            self.run_async(f"mkdir {remote}", lambda: self.adb.mkdir(remote))

    def action_delete_path(self):
        remote = self.remote_path_var.get().strip()
        if not remote:
            return
        if messagebox.askyesno("Confirm Delete", f"আপনি কি নিশ্চিতভাবে ফোনের '{remote}' মুছে ফেলতে চান?"):
            self.run_async(f"Delete {remote}", lambda: self.adb.rm(remote))

    def action_upload_file(self):
        local = filedialog.askopenfilename(title="Select File from PC to Upload")
        if not local:
            return
        remote = self.remote_path_var.get().strip() or "/sdcard/Download"
        self._start_transfer_with_progress("Upload File", is_push=True, src=local, dst=remote)

    def action_download_file(self):
        remote = self.remote_path_var.get().strip()
        if not remote:
            messagebox.showwarning("Remote Path Required", "ডাউনলোড করার জন্য Remote Path বক্সে ফাইলের পাথ দিন।")
            return
        local_dir = filedialog.askdirectory(title="Select Destination Folder on PC")
        if not local_dir:
            return
        self._start_transfer_with_progress("Download File", is_push=False, src=remote, dst=local_dir)

    def action_sync_folder(self):
        remote = self.remote_path_var.get().strip() or "/sdcard/Download"
        local_dir = filedialog.askdirectory(title="Select Local PC Folder for Sync/Backup")
        if not local_dir:
            return
        direction = messagebox.askyesnocancel(
            "Sync Direction",
            f"ফোল্ডার সিঙ্ক মোড বেছে নিন:\n\n"
            f"• YES = Phone ({remote}) → PC ({local_dir}) [Backup]\n"
            f"• NO  = PC ({local_dir}) → Phone ({remote}) [Upload]",
        )
        if direction is None:
            return
        if direction:
            self._start_transfer_with_progress("Backup Folder (Phone → PC)", is_push=False, src=remote, dst=local_dir)
        else:
            self._start_transfer_with_progress("Sync Folder (PC → Phone)", is_push=True, src=local_dir, dst=remote)

    # ─────────────────────────── Tab 4 Actions: System Monitoring ───────────────────────────
    def action_battery_info(self):
        self.run_async("Battery Info", self.adb.battery)

    def action_memory_info(self):
        self.run_async("Memory Info", self.adb.memory)

    def action_cpu_info(self):
        self.run_async("CPU Info", self.adb.cpu)

    def action_storage_info(self):
        self.run_async("Storage Info", self.adb.storage)

    def action_processes(self):
        self.run_async("Running Processes", self.adb.processes)

    def action_network_info(self):
        self.run_async("Network Info", self.adb.netstat)

    def action_system_properties(self):
        def task():
            info = self.adb.info()
            return "\n".join(f"{k:<15}: {v}" for k, v in info.items())

        self.run_async("System Properties", task)

    def action_apps_with_size(self):
        def task():
            out = self.adb.shell("du -sh /data/media/0/Android/data/* 2>/dev/null | sort -hr | head -n 30", check=False)
            if not out.strip():
                out = self.adb.shell("df -h && echo '\n--- Top Storage Directories ---' && du -sh /sdcard/* 2>/dev/null", check=False)
            return out or "Storage details unavailable without root permissions."

        self.run_async("Apps & Directory Sizes", task)

    def action_service_status(self):
        self.run_async("Service Status", self.adb.service_status)

    def toggle_performance_monitor(self):
        if self.perf_monitoring:
            self.perf_monitoring = False
            self.btn_perf_toggle.configure(text="▶️ Start Live Performance Monitor")
            self.set_status("Live Performance Monitor বন্ধ করা হয়েছে।", ACCENT)
            return

        if not self._ensure_device_selected():
            return

        self.perf_monitoring = True
        self.btn_perf_toggle.configure(text="⏹️ Stop Live Performance Monitor")
        self._schedule_perf_tick()

    def _schedule_perf_tick(self):
        if not self.perf_monitoring:
            return

        def worker():
            try:
                bat = self.adb.shell("dumpsys battery | grep -E 'level|temperature|status'", check=False)
                mem = self.adb.shell("dumpsys meminfo | head -n 10", check=False)
                cpu = self.adb.shell("top -n 1 | head -n 12", check=False)
                snap = (
                    "═══ Battery ═══\n" + bat.strip() +
                    "\n\n═══ Memory ═══\n" + mem.strip() +
                    "\n\n═══ CPU Top ═══\n" + cpu.strip()
                )
                self.root.after(0, lambda: self.write_console(snap, title="📈 Live Performance Snapshot", clear=True))
            except Exception as e:
                self.root.after(0, lambda err=e: self.write_console(f"Monitor error: {err}", title="Monitor Error"))
            finally:
                if self.perf_monitoring:
                    self.root.after(3000, self._schedule_perf_tick)

        threading.Thread(target=worker, daemon=True).start()

    # ─────────────────────────── Tab 5 Actions: Screen Sharing ───────────────────────────
    def _find_scrcpy_exe(self) -> str:
        for candidate in ["scrcpy.exe", os.path.join("scrcpy", "scrcpy.exe")]:
            if os.path.exists(candidate):
                return candidate
        return "scrcpy"

    def _launch_scrcpy_bg(self, args: List[str]):
        exe = self._find_scrcpy_exe()
        try:
            subprocess.Popen(
                [exe] + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as e:
            raise ADBError(
                "scrcpy পাওয়া যায়নি!\n"
                "টার্মিনালে নিচের কমান্ডটি চালিয়ে ইনস্টল করুন:\n"
                "  winget install Genymobile.scrcpy"
            ) from e

    def action_start_scrcpy(self):
        if not self._ensure_device_selected():
            return
        try:
            self._launch_scrcpy_bg(["-s", self.adb.serial])
            self.write_console(f"ডিভাইস ({self.adb.serial})-এর স্ক্রিন শেয়ার চালু হয়েছে!", title="scrcpy Started")
        except ADBError as e:
            messagebox.showerror("scrcpy Error", str(e))

    def action_screen_off_scrcpy(self):
        """Wakes phone, enables stay-awake, restarts scrcpy in -S -w mode cleanly."""
        if not self._ensure_device_selected():
            return

        def task():
            # ১. আগে ফোন ওয়েকআপ ও stay-on নিশ্চিত করা
            self.adb.shell("svc power stayon true", check=False)
            self.adb.keyevent("KEYCODE_WAKEUP")
            # ২. আগের আটকে থাকা scrcpy থাকলে রিস্টার্ট করা যাতে -S আবার কাজ করে
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", "scrcpy.exe", "/F"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
            time.sleep(0.4)
            self._launch_scrcpy_bg(["-s", self.adb.serial, "-S", "-w"])
            return (
                f"ডিভাইস ({self.adb.serial}) Screen-Off মোডে (-S -w) চালু হয়েছে!\n\n"
                "⚠️ জরুরি নির্দেশনা:\n"
                "• ফোনের ফিজিক্যাল Power বাটন চাপবেন না (চাপলে ফোন লক হয়ে যায় এবং স্ক্রিন-অফ মোড ভেঙে যায়)।\n"
                "• যেকোনো সময় ফোনের স্ক্রিন আবার বন্ধ করতে scrcpy উইন্ডোতে চাপুন:  Alt + O\n"
                "• ফোনের স্ক্রিন আবার চালু করতে scrcpy উইন্ডোতে চাপুন:  Alt + Shift + O"
            )

        self.run_async("Screen-Off Mode (-S -w)", task, require_device=True)

    def action_camera_stream(self, facing: str):
        if not self._ensure_device_selected():
            return
        # ফোনের ক্যামেরা সেন্সর সাধারণত ল্যান্ডস্কেপে বসানো থাকে, তাই পোর্ট্রেট সোজা করতে orientation সেট করা হলো
        orientation = "90" if facing == "back" else "270"
        args = [
            "-s", self.adb.serial,
            "--video-source=camera",
            f"--camera-facing={facing}",
            "--camera-size=1280x720",
            f"--orientation={orientation}",
            "--no-audio",
            "--window-title", f"Phone {facing.title()} Camera ({self.adb.serial})",
        ]
        try:
            self._launch_scrcpy_bg(args)
            self.write_console(
                f"ডিভাইস ({self.adb.serial})-এর {facing.upper()} ক্যামেরা ব্যাকগ্রাউন্ড স্ট্রিম চালু হয়েছে!\n"
                "• ফোনে কোনো ক্যামেরা অ্যাপ খুলবে না—সরাসরি পিসিতে লাইভ ফিড দেখা যাবে।\n"
                "• প্রয়োজনে উইন্ডোর ভেতরে  Alt + ←  অথবা  Alt + →  চেপে যেকোনো দিকে ঘুরাতে (Rotate) পারবেন।\n"
                "• বন্ধ করতে ক্যামেরা উইন্ডো কেটে দিন অথবা '🛑 Stop All' চাপুন।",
                title=f"📷 {facing.title()} Camera Stream",
            )
        except ADBError as e:
            messagebox.showerror("scrcpy Camera Error", str(e))

    def action_custom_scrcpy(self):
        if not self._ensure_device_selected():
            return
        mode = self.scrcpy_preset_var.get()
        if mode == "2":
            self.action_screen_off_scrcpy()
            return

        preset_args = {
            "1": ["-m", "1024", "-b", "4M", "--max-fps", "60"],
            "3": ["-w", "--always-on-top"],
            "4": ["-n"],
        }
        if mode in preset_args:
            extra_args = preset_args[mode]
        else:
            extra_args = shlex.split(self.scrcpy_extra_var.get().strip())

        try:
            self._launch_scrcpy_bg(["-s", self.adb.serial] + extra_args)
            self.write_console(f"scrcpy চালু হয়েছে: {' '.join(extra_args)}", title="Custom scrcpy")
        except ADBError as e:
            messagebox.showerror("scrcpy Error", str(e))

    def action_stop_scrcpy(self):
        def task():
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", "scrcpy.exe", "/F"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "scrcpy"], capture_output=True)
            return "সব সক্রিয় scrcpy সেশন বন্ধ করা হয়েছে।"

        self.run_async("Stop scrcpy", task, require_device=False)

    def action_show_scrcpy_sessions(self):
        def task():
            if os.name == "nt":
                proc = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq scrcpy.exe"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
                out = proc.stdout.strip()
                if "scrcpy.exe" not in out.lower():
                    return "কোনো সক্রিয় scrcpy সেশন চলছে না।"
                return out
            proc = subprocess.run(["pgrep", "-af", "scrcpy"], capture_output=True, text=True)
            return proc.stdout.strip() or "কোনো সক্রিয় scrcpy সেশন চলছে না।"

        self.run_async("Active scrcpy Sessions", task, require_device=False)

    def action_multi_device_scrcpy(self):
        def task():
            devices = [d for d in ADB.list_devices() if d["state"] == "device"]
            if not devices:
                raise ADBError("কোনো কানেক্টেড ডিভাইস পাওয়া যায়নি।")
            for d in devices:
                self._launch_scrcpy_bg(["-s", d["serial"], "--window-title", f"Phone: {d['serial']}"])
            return f"মোট {len(devices)}টি ডিভাইসের জন্য আলাদা আলাদা স্ক্রিন শেয়ার চালু হয়েছে!"

        self.run_async("Multi-Device scrcpy", task, require_device=False)

    def action_check_scrcpy(self):
        def task():
            exe = self._find_scrcpy_exe()
            try:
                proc = subprocess.run(
                    [exe, "--version"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
                )
                return (proc.stdout or proc.stderr).strip()
            except FileNotFoundError:
                return "❌ scrcpy ইনস্টল করা নেই।\nইনস্টল করতে লিখুন: winget install Genymobile.scrcpy"

        self.run_async("Check scrcpy", task, require_device=False)

    def action_scrcpy_record(self):
        if not self._ensure_device_selected():
            return
        save_path = filedialog.asksaveasfilename(
            title="Save scrcpy Recording As",
            initialfile=f"scrcpy_rec_{int(time.time())}.mp4",
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4")],
        )
        if not save_path:
            return
        try:
            self._launch_scrcpy_bg(["-s", self.adb.serial, "--record", save_path])
            self.write_console(
                f"স্ক্রিন শেয়ার ও রেকর্ডিং শুরু হয়েছে!\nফাইল সেভ হবে: {save_path}\n"
                "রেকর্ডিং শেষ করতে scrcpy উইন্ডোটি বন্ধ করুন।",
                title="scrcpy Recording",
            )
        except ADBError as e:
            messagebox.showerror("scrcpy Error", str(e))

    # ─────────────────────────── Tab 6 Actions: WiFi & Live ───────────────────────────
    def action_tcpip_mode(self):
        try:
            port = int(self.tcpip_port_var.get().strip())
        except ValueError:
            port = 5555
        self.run_async(f"TCP/IP Mode ({port})", lambda: self.adb.tcpip(port))

    def action_transfer_all_tcpip(self):
        try:
            port = int(self.tcpip_port_var.get().strip())
        except ValueError:
            port = 5555

        def task():
            devices = [d for d in ADB.list_devices() if d["state"] == "device" and ":" not in d["serial"]]
            if not devices:
                return "কোনো USB ডিভাইস পাওয়া যায়নি।"
            logs = []
            for d in devices:
                sub_adb = ADB(serial=d["serial"])
                out = sub_adb.tcpip(port)
                logs.append(f"{d['serial']} → {out}")
            return "\n".join(logs)

        self.run_async("Transfer All Devices to TCP/IP", task, require_device=False)

    def action_quick_wifi_setup(self):
        try:
            port = int(self.tcpip_port_var.get().strip())
        except ValueError:
            port = 5555

        def task():
            out1 = self.adb.tcpip(port)
            time.sleep(1.5)
            ip = self.adb.shell(
                "ip -f inet addr show wlan0 | grep inet | awk '{print $2}' | cut -d/ -f1",
                check=False,
            ).strip()
            if ip:
                self.ip_port_var.set(f"{ip}:{port}")
                out2 = self.adb.connect(f"{ip}:{port}")
                self.root.after(300, self.refresh_devices)
            else:
                out2 = "WiFi IP পাওয়া যায়নি। ফোনে WiFi কানেক্ট করা আছে কি না যাচাই করুন।"
            return f"{out1}\nDetected IP: {ip or 'N/A'}\n{out2}"

        self.run_async("Quick WiFi Setup", task)

    def action_wifi_status(self):
        def task():
            status_out = self.adb.shell("cmd wifi status", check=False)
            basic_dump = self.adb.shell("dumpsys wifi | head -n 30", check=False)
            toggles = self.adb.shell("dumpsys wifi | grep 'CMD_WIFI_TOGGLED' | tail -n 5", check=False)

            result = ["═══ Connected WiFi Status ═══"]
            if status_out:
                for line in status_out.splitlines():
                    line_s = line.strip()
                    if any(k in line_s for k in ["Wifi is", "SSID:", "DhcpResults:", "successful", "Additional"]):
                        result.append(line_s)
            else:
                result.append("তথ্য পাওয়া যায়নি")

            result.append("\n═══ System Settings ═══")
            important_keys = ("WifiState", "AirplaneModeOn", "ScanAlwaysAvailable", "Current wifi mode")
            for line in basic_dump.splitlines():
                stripped = line.strip()
                if stripped.startswith(important_keys):
                    result.append(stripped)

            result.append("\n═══ শেষ ৫টি WiFi Toggle ইতিহাস ═══")
            result.append(toggles.strip() or "কোনো toggle ইতিহাস নেই")
            return "\n".join(result)

        self.run_async("WiFi Status", task)

    def toggle_live_logcat(self):
        if self.logcat_proc is not None:
            try:
                self.logcat_proc.terminate()
            except Exception:
                pass
            self.logcat_proc = None
            self.btn_logcat_stream.configure(text="▶️ Start Live Logcat Stream")
            self.set_status("Live Logcat Stream বন্ধ করা হয়েছে।", ACCENT)
            return

        if not self._ensure_device_selected():
            return

        cmd = ["adb", "-s", self.adb.serial, "logcat", "-v", "time"]
        try:
            self.logcat_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            messagebox.showerror("ADB Missing", "adb পাওয়া যায়নি!")
            return

        self.btn_logcat_stream.configure(text="⏹️ Stop Live Logcat Stream")
        self.write_console("Live Logcat স্ট্রিমিং শুরু হয়েছে...", title="🔴 Live Logcat")
        self.set_status("Streaming Live Logcat...", WARNING)

        def reader():
            proc = self.logcat_proc
            if not proc or not proc.stdout:
                return
            for line in proc.stdout:
                if self.logcat_proc is not proc:
                    break
                clean = line.rstrip("\r\n")
                if clean:
                    self.root.after(0, lambda l=clean: self._append_stream_line(l))

        threading.Thread(target=reader, daemon=True).start()

    def _append_stream_line(self, line: str):
        if " E/" in line or " E " in line:
            self.console_text.insert("end", line + "\n", "error")
        elif " W/" in line or " W " in line:
            self.console_text.insert("end", line + "\n", "warn")
        else:
            self.console_text.insert("end", line + "\n")
        self.console_text.see("end")

    def action_save_settings(self):
        try:
            val = max(5, int(self.timeout_var.get().strip()))
            self.adb.timeout = val
            self.write_console(f"ADB Command Timeout আপডেট করা হয়েছে: {val} সেকেন্ড।", title="Settings Saved")
        except ValueError:
            messagebox.showwarning("Invalid Timeout", "অনুগ্রহ করে সঠিক সংখ্যা লিখুন।")

    # ─────────────────────────── Tab 7 Actions: Device Profiles & 1-Click Unlock ───────────────────────────
    def _toggle_prof_value_visibility(self):
        show = not self.prof_show_val.get()
        self.prof_show_val.set(show)
        self.prof_val_entry.configure(show="" if show else "*")

    def _auto_sync_active_profile(self):
        """Automatically loads the saved profile for the universally selected Active Device."""
        serial = self.adb.serial
        if not serial:
            return

        def worker():
            try:
                fp = self.adb.getprop("ro.build.fingerprint").strip()
                if not fp:
                    return
                existing = self.profile_store.get(fp)
                if existing:
                    self.root.after(0, lambda p=existing: self._load_profile_into_form(p))
                    self.root.after(
                        0,
                        lambda n=existing.get("name", ""), lt=existing.get("lock_type", ""): self.set_status(
                            f"Active: {serial}  •  Profile Loaded: {n} ({lt})", ACCENT
                        ),
                    )
                else:
                    brand = self.adb.getprop("ro.product.manufacturer").strip()
                    model = self.adb.getprop("ro.product.model").strip()

                    def fill_new():
                        self.prof_fp_var.set(fp)
                        self.prof_name_var.set(f"{brand} {model}".strip())
                        self.prof_value_var.set("")
                        self.prof_coords_var.set("auto")
                        self.prof_notes_var.set("")

                    self.root.after(0, fill_new)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _load_profile_into_form(self, p: dict):
        self.prof_fp_var.set(p.get("fingerprint", ""))
        self.prof_name_var.set(p.get("name", ""))
        self.prof_lock_type_var.set(p.get("lock_type", LOCK_TYPES[1]))
        self.prof_process_var.set(p.get("unlock_process", UNLOCK_PROCESSES[1]))
        self.prof_value_var.set(p.get("unlock_value", ""))
        self.prof_coords_var.set(p.get("swipe_coords", "auto"))
        self.prof_notes_var.set(p.get("notes", ""))

    def action_detect_fingerprint(self):
        def task():
            fp = self.adb.getprop("ro.build.fingerprint").strip()
            model = self.adb.getprop("ro.product.model").strip()
            brand = self.adb.getprop("ro.product.manufacturer").strip()
            if not fp:
                raise ADBError("ডিভাইসের Fingerprint পাওয়া যায়নি।")

            existing = self.profile_store.get(fp)

            def apply_ui():
                if existing:
                    self._load_profile_into_form(existing)
                    self.write_console(
                        f"সংরক্ষিত প্রোফাইল পাওয়া গেছে!\nName: {existing.get('name')}\nFingerprint: {fp}",
                        title="Profile Loaded",
                    )
                else:
                    self.prof_fp_var.set(fp)
                    self.prof_name_var.set(f"{brand} {model}".strip())
                    self.prof_coords_var.set("auto")
                    self.write_console(
                        f"নতুন ডিভাইস শনাক্ত হয়েছে:\nName: {brand} {model}\nFingerprint: {fp}\n\n"
                        "এখন Lock Type, Unlock Process ও Unlock Value দিয়ে 'Save Profile' চাপুন।",
                        title="Fingerprint Detected",
                    )

            self.root.after(0, apply_ui)
            return f"Fingerprint: {fp}"

        self.run_async("Detect Fingerprint", task, require_device=True)

    def action_save_profile(self):
        fp = self.prof_fp_var.get().strip()
        if not fp:
            messagebox.showwarning("Fingerprint Required", "আগে 'Detect Connected Device' চাপুন অথবা Fingerprint লিখুন।")
            return
        entry = self.profile_store.upsert(
            fp,
            {
                "name": self.prof_name_var.get(),
                "lock_type": self.prof_lock_type_var.get(),
                "unlock_process": self.prof_process_var.get(),
                "unlock_value": self.prof_value_var.get(),
                "swipe_coords": self.prof_coords_var.get(),
                "notes": self.prof_notes_var.get(),
            },
        )
        self.write_console(
            f"প্রোফাইল সেভ হয়েছে!\nDevice: {entry['name']}\nFingerprint: {entry['fingerprint']}\n"
            f"Lock Type: {entry['lock_type']}\nProcess: {entry['unlock_process']}",
            title="💾 Profile Saved",
        )
        self.set_status(f"Profile Saved: {entry['name']}", ACCENT)

    def action_delete_profile(self):
        fp = self.prof_fp_var.get().strip()
        if not fp:
            return
        if not messagebox.askyesno("Confirm Delete", f"আপনি কি এই ডিভাইসের প্রোফাইল মুছে ফেলতে চান?\n\n{fp}"):
            return
        if self.profile_store.delete(fp):
            self.prof_value_var.set("")
            self.prof_notes_var.set("")
            self.write_console(f"প্রোফাইল মুছে ফেলা হয়েছে: {fp}", title="Profile Deleted")
        else:
            messagebox.showinfo("Not Found", "এই Fingerprint-এর কোনো সেভ করা প্রোফাইল পাওয়া যায়নি।")

    def action_one_click_unlock(self):
        def task():
            fp = self.adb.getprop("ro.build.fingerprint").strip()
            if not fp:
                raise ADBError("কানেক্টেড ডিভাইসের Fingerprint পড়া যায়নি।")
            profile = self.profile_store.get(fp)
            if not profile:
                raise ADBError(
                    f"এই ডিভাইসের কোনো সেভ করা প্রোফাইল পাওয়া যায়নি!\n"
                    f"Fingerprint: {fp}\n\n"
                    "দয়া করে '🔑 Profiles' ট্যাবে গিয়ে 'Detect Connected Device' চাপুন এবং লকের তথ্য সেভ করুন।"
                )
            return smart_unlock_device(self.adb, profile)

        self.run_async("1-Click Smart Unlock", task, require_device=True)

    # ─────────────────────────── Auto-Sync Watch & Saved Profiles Manager ───────────────────────────
    def toggle_auto_sync_watch(self):
        if self.auto_sync_active:
            self.auto_sync_active = False
            self.btn_auto_sync.configure(text="👁️ Auto-Sync Watch")
            self.write_console("Auto-Sync Watch বন্ধ করা হয়েছে।", title="Auto-Sync Stopped")
            self.set_status("Auto-Sync Watch বন্ধ করা হয়েছে।", ACCENT)
            return

        if not self._ensure_device_selected():
            return

        local_dir = filedialog.askdirectory(title="Select Local PC Folder to Auto-Sync (PC → Phone)")
        if not local_dir:
            return
        remote_dir = self.remote_path_var.get().strip() or "/sdcard/Download"

        self.auto_sync_active = True
        self.btn_auto_sync.configure(text="⏹️ Stop Watch")
        self.write_console(
            f"Auto-Sync Watch চালু হয়েছে!\nPC Folder: {local_dir}\nPhone Folder: {remote_dir}\n"
            "এই ফোল্ডারে কোনো নতুন বা পরিবর্তিত ফাইল রাখলেই স্বয়ংক্রিয়ভাবে ফোনে চলে যাবে।",
            title="👁️ Auto-Sync Watch Started",
        )
        self.set_status(f"Watching: {os.path.basename(local_dir)} → {remote_dir}", WARNING)
        threading.Thread(target=self._auto_sync_worker, args=(local_dir, remote_dir), daemon=True).start()

    def _auto_sync_worker(self, local_dir: str, remote_dir: str):
        snapshot = {}
        try:
            for name in os.listdir(local_dir):
                full = os.path.join(local_dir, name)
                if os.path.isfile(full):
                    st = os.stat(full)
                    snapshot[full] = (st.st_mtime, st.st_size)
        except OSError:
            pass

        while self.auto_sync_active:
            time.sleep(2.5)
            if not self.auto_sync_active:
                break
            try:
                current_files = os.listdir(local_dir)
            except OSError:
                continue

            for name in current_files:
                if not self.auto_sync_active:
                    break
                full = os.path.join(local_dir, name)
                if not os.path.isfile(full):
                    continue
                try:
                    st = os.stat(full)
                    sig = (st.st_mtime, st.st_size)
                except OSError:
                    continue

                if snapshot.get(full) != sig:
                    snapshot[full] = sig
                    try:
                        out = self.adb.push(full, remote_dir)
                        self.root.after(
                            0,
                            lambda fn=name, msg=out: self.write_console(
                                f"Auto-Synced: {fn} → {remote_dir}\n{msg}",
                                title="🔄 Auto-Sync",
                            ),
                        )
                    except Exception as e:
                        self.root.after(
                            0,
                            lambda fn=name, err=e: self.write_console(
                                f"Auto-Sync Failed ({fn}): {err}",
                                title="❌ Auto-Sync Error",
                            ),
                        )

    def action_open_url(self):
        url = self.url_var.get().strip()
        if not url or url in ("http://", "https://"):
            messagebox.showwarning("URL Required", "অনুগ্রহ করে একটি সঠিক URL লিখুন।")
            return
        self.run_async(f"Open URL: {url}", lambda: self.adb.open_url(url), require_device=True)

    def action_pull_phone_clipboard(self):
        def task():
            text = self.adb.get_phone_clipboard()

            def copy_to_pc():
                self.root.clipboard_clear()
                self.root.clipboard_append(text)

            self.root.after(0, copy_to_pc)
            return f"✔ ফোনের ক্লিপবোর্ড ডাটা পিসির Clipboard-এ কপি করা হয়েছে:\n\n{text}"

        self.run_async("Phone Clipboard → PC", task, require_device=True)

    def action_grant_perm(self):
        pkg = self._require_package_name()
        perm = self.perm_var.get().strip()
        if pkg and perm:
            self.run_async(f"Grant {perm}", lambda: self.adb.grant_permission(pkg, perm))

    def action_revoke_perm(self):
        pkg = self._require_package_name()
        perm = self.perm_var.get().strip()
        if pkg and perm:
            self.run_async(f"Revoke {perm}", lambda: self.adb.revoke_permission(pkg, perm))

    def action_list_perms(self):
        pkg = self._require_package_name()
        if pkg:
            self.run_async(f"Permissions: {pkg}", lambda: self.adb.list_app_permissions(pkg))

    def action_set_brightness(self):
        val = self.brightness_var.get()
        self.run_async(f"Brightness ({val}/255)", lambda: self.adb.set_brightness(val))

    def action_set_timeout(self):
        mapping = {
            "15 Seconds": 15000,
            "30 Seconds": 30000,
            "1 Minute": 60000,
            "2 Minutes": 120000,
            "5 Minutes": 300000,
            "10 Minutes": 600000,
            "30 Minutes": 1800000,
        }
        ms = mapping.get(self.timeout_preset_var.get(), 60000)
        self.run_async(f"Screen Timeout ({self.timeout_preset_var.get()})", lambda: self.adb.set_screen_timeout(ms))

    def open_saved_profiles_manager(self):
        SavedProfilesDialog(self.root, self.profile_store, on_select=self._load_profile_into_form)

    # ─────────────────────────── Graphical QR Pairing Wizard ───────────────────────────
    def open_qr_pairing_wizard(self):
        if qrcode is None or Zeroconf is None:
            messagebox.showwarning(
                "Optional Dependencies Needed",
                "QR Pairing ব্যবহারের জন্য qrcode এবং zeroconf প্যাকেজ প্রয়োজন:\n\n"
                "pip install qrcode zeroconf",
            )
            return

        WiFiQRPairDialog(self.root, self.adb, on_connected=self.refresh_devices, log_callback=self.write_console)

    # ─────────────────────────── Tab 9: Comms (Notifications, SMS/OTP & Dialer) ───────────────────────────
    def _build_tab_comms(self):
        sub_nb = ctk.CTkTabview(
            self.tab_comms,
            fg_color="transparent",
            segmented_button_fg_color=BG_PANEL,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=BG_PANEL,
            segmented_button_unselected_hover_color=BORDER,
            text_color=FG_TEXT,
            corner_radius=10,
        )
        sub_nb.pack(fill="both", expand=True)

        tab_notif = sub_nb.add("🔔 Notifications")
        tab_sms_outer = sub_nb.add("📩 SMS / OTP & Dialer")
        tab_sms_call = ctk.CTkScrollableFrame(
            tab_sms_outer,
            fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT,
        )
        tab_sms_call.pack(fill="both", expand=True)

        # --- Sub-tab 1: Live Notification Viewer ---
        n_top = ttk.Frame(tab_notif, style="Card.TFrame")
        n_top.pack(fill="x", pady=(4, 8))
        ttk.Button(n_top, text="🔄 Refresh Notifications", style="Accent.TButton", command=self.action_refresh_notifications).pack(side="left", padx=3)
        ttk.Button(n_top, text="📂 Open Shade", command=lambda: self.run_async("Open Shade", lambda: self.adb.shell("cmd statusbar expand-notifications", check=False), require_device=True)).pack(side="left", padx=3)
        ttk.Button(n_top, text="🔼 Collapse Shade", command=lambda: self.run_async("Collapse Shade", lambda: self.adb.shell("cmd statusbar collapse", check=False), require_device=True)).pack(side="left", padx=3)
        self.notif_count_var = tk.StringVar(value="0 notifications")
        ttk.Label(n_top, textvariable=self.notif_count_var, style="Muted.TLabel").pack(side="right", padx=6)

        n_frame = ttk.Frame(tab_notif, style="Card.TFrame")
        n_frame.pack(fill="both", expand=True)
        self.notif_tree = ttk.Treeview(n_frame, columns=("app", "title", "text"), show="headings", height=16)
        self.notif_tree.heading("app", text="App Package")
        self.notif_tree.heading("title", text="Title")
        self.notif_tree.heading("text", text="Message")
        self.notif_tree.column("app", width=180, stretch=False)
        self.notif_tree.column("title", width=170, stretch=False)
        self.notif_tree.column("text", width=260, stretch=True)
        n_scroll = ttk.Scrollbar(n_frame, orient="vertical", command=self.notif_tree.yview)
        self.notif_tree.configure(yscrollcommand=n_scroll.set)
        self.notif_tree.pack(side="left", fill="both", expand=True)
        n_scroll.pack(side="right", fill="y")
        self.notif_tree.bind("<Double-1>", self._on_notif_double_click)

        # --- Sub-tab 2: SMS / OTP & Quick Dialer ---
        dial_card = self._card(tab_sms_call, "📞 Quick Dialer & Call Control")
        dial_row = ttk.Frame(dial_card, style="Card.TFrame")
        dial_row.pack(fill="x")
        ttk.Label(dial_row, text="Number:", style="Card.TLabel").pack(side="left")
        self.dial_number_var = tk.StringVar()
        dial_entry = ttk.Entry(dial_row, textvariable=self.dial_number_var, width=22)
        dial_entry.pack(side="left", padx=6, fill="x", expand=True)
        dial_entry.bind("<Return>", lambda _: self.action_dial_number())
        ttk.Button(dial_row, text="📞 Dial", style="Accent.TButton", command=self.action_dial_number).pack(side="left", padx=3)
        ttk.Button(dial_row, text="📲 Answer", style="Blue.TButton", command=lambda: self.action_call_control("answer")).pack(side="left", padx=3)
        ttk.Button(dial_row, text="📵 End Call", style="Danger.TButton", command=lambda: self.action_call_control("end")).pack(side="left", padx=3)

        sms_card = self._card(tab_sms_call, "📩 Recent SMS & Auto-OTP")
        sms_bar = ttk.Frame(sms_card, style="Card.TFrame")
        sms_bar.pack(fill="x", pady=(0, 6))
        ttk.Button(sms_bar, text="🔄 Refresh SMS", style="Accent.TButton", command=self.action_refresh_sms).pack(side="left", padx=3)
        ttk.Button(sms_bar, text="🔑 Copy Selected OTP", style="Blue.TButton", command=self.action_copy_selected_otp).pack(side="left", padx=3)

        sms_wrap = ttk.Frame(sms_card, style="Card.TFrame")
        sms_wrap.pack(fill="both", expand=True)
        self.sms_tree = ttk.Treeview(sms_wrap, columns=("sender", "otp", "body", "time"), show="headings", height=6)
        self.sms_tree.heading("sender", text="Sender")
        self.sms_tree.heading("otp", text="OTP")
        self.sms_tree.heading("body", text="Message")
        self.sms_tree.heading("time", text="Time")
        self.sms_tree.column("sender", width=120, stretch=False)
        self.sms_tree.column("otp", width=75, stretch=False)
        self.sms_tree.column("body", width=260, stretch=True)
        self.sms_tree.column("time", width=110, stretch=False)
        sms_sc = ttk.Scrollbar(sms_wrap, orient="vertical", command=self.sms_tree.yview)
        self.sms_tree.configure(yscrollcommand=sms_sc.set)
        self.sms_tree.pack(side="left", fill="both", expand=True)
        sms_sc.pack(side="right", fill="y")
        self.sms_tree.bind("<Double-1>", lambda _: self.action_copy_selected_otp())

        call_card = self._card(tab_sms_call, "☎️ Recent Call Logs")
        call_bar = ttk.Frame(call_card, style="Card.TFrame")
        call_bar.pack(fill="x", pady=(0, 6))
        ttk.Button(call_bar, text="🔄 Refresh Call Logs", style="Blue.TButton", command=self.action_refresh_calls).pack(side="left", padx=3)
        ttk.Button(call_bar, text="📞 Dial Selected", command=self.action_dial_selected_call).pack(side="left", padx=3)

        call_wrap = ttk.Frame(call_card, style="Card.TFrame")
        call_wrap.pack(fill="both", expand=True)
        self.call_tree = ttk.Treeview(call_wrap, columns=("number", "name", "type", "dur", "time"), show="headings", height=5)
        self.call_tree.heading("number", text="Number")
        self.call_tree.heading("name", text="Name")
        self.call_tree.heading("type", text="Type")
        self.call_tree.heading("dur", text="Duration")
        self.call_tree.heading("time", text="Time")
        self.call_tree.column("number", width=130, stretch=False)
        self.call_tree.column("name", width=130, stretch=True)
        self.call_tree.column("type", width=85, stretch=False)
        self.call_tree.column("dur", width=70, stretch=False)
        self.call_tree.column("time", width=110, stretch=False)
        call_sc = ttk.Scrollbar(call_wrap, orient="vertical", command=self.call_tree.yview)
        self.call_tree.configure(yscrollcommand=call_sc.set)
        self.call_tree.pack(side="left", fill="both", expand=True)
        call_sc.pack(side="right", fill="y")

    def action_push_pc_clipboard(self):
        try:
            txt = self.root.clipboard_get()
        except Exception:
            txt = ""
        if not txt:
            messagebox.showinfo("Clipboard Empty", "পিসির ক্লিপবোর্ডে কোনো টেক্সট নেই।")
            return
        self.run_async("PC → Phone Clipboard", lambda: self.adb.set_phone_clipboard(txt), require_device=True)

    def action_launch_virtual_display(self):
        if not self._ensure_device_selected():
            return
        pkg = self.vd_pkg_var.get().strip()
        size = self.vd_size_var.get().strip()
        args = ["-s", self.adb.serial]
        if size and size != "Auto":
            args.append(f"--new-display={size}")
        else:
            args.append("--new-display")
        if pkg:
            args.append(f"--start-app=+{pkg}")

        def task():
            subprocess.Popen(
                ["scrcpy"] + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return f"🖥️ Virtual Display চালু হয়েছে: {' '.join(args)}"

        self.run_async("Virtual Display", task, require_device=True)

    def _on_notif_double_click(self, _event=None):
        sel = self.notif_tree.selection()
        if not sel:
            return
        vals = self.notif_tree.item(sel[0], "values")
        if vals:
            full = f"[{vals[0]}] {vals[1]}: {vals[2]}"
            self.root.clipboard_clear()
            self.root.clipboard_append(str(vals[2] or vals[1]))
            self.write_console("Notification Copied", full)

    def action_refresh_notifications(self):
        def task():
            items = self.adb.list_notifications(limit=40)

            def update_ui():
                self.notif_tree.delete(*self.notif_tree.get_children())
                for it in items:
                    self.notif_tree.insert("", "end", values=(it["package"], it["title"], it["text"]))
                self.notif_count_var.set(f"{len(items)} notifications")

            self.root.after(0, update_ui)
            return f"🔔 {len(items)}টি নোটিফিকেশন লোড হয়েছে।"

        self.run_async("Refresh Notifications", task, require_device=True)

    def action_refresh_sms(self):
        def task():
            items = self.adb.list_sms(limit=25)

            def update_ui():
                self.sms_tree.delete(*self.sms_tree.get_children())
                for it in items:
                    self.sms_tree.insert("", "end", values=(it["address"], it["otp"] or "-", it["body"], it["date"]))

            self.root.after(0, update_ui)
            return f"📩 {len(items)}টি SMS লোড হয়েছে।"

        self.run_async("Refresh SMS", task, require_device=True)

    def action_copy_selected_otp(self):
        sel = self.sms_tree.selection()
        if not sel:
            messagebox.showinfo("Select SMS", "প্রথমে লিস্ট থেকে একটি SMS সিলেক্ট করুন।")
            return
        vals = self.sms_tree.item(sel[0], "values")
        otp = str(vals[1]) if len(vals) > 1 else ""
        body = str(vals[2]) if len(vals) > 2 else ""
        to_copy = otp if (otp and otp != "-") else body
        if to_copy:
            self.root.clipboard_clear()
            self.root.clipboard_append(to_copy)
            self.write_console("Clipboard", f"✔ কপি করা হয়েছে: {to_copy}")

    def action_refresh_calls(self):
        def task():
            items = self.adb.list_call_logs(limit=25)

            def update_ui():
                self.call_tree.delete(*self.call_tree.get_children())
                for it in items:
                    self.call_tree.insert("", "end", values=(it["number"], it["name"], it["type"], it["duration"], it["date"]))

            self.root.after(0, update_ui)
            return f"☎️ {len(items)}টি কল লগ লোড হয়েছে।"

        self.run_async("Refresh Call Logs", task, require_device=True)

    def action_dial_number(self):
        num = self.dial_number_var.get().strip()
        self.run_async("Dial Number", lambda: self.adb.dial_number(num), require_device=True)

    def action_dial_selected_call(self):
        sel = self.call_tree.selection()
        if not sel:
            return
        vals = self.call_tree.item(sel[0], "values")
        if vals and vals[0]:
            self.dial_number_var.set(str(vals[0]))
            self.action_dial_number()

    def action_call_control(self, action: str):
        self.run_async(f"Call {action}", lambda: self.adb.call_action(action), require_device=True)

    # ─────────────────────────── Interactive Phone File Explorer, HID & Macro ───────────────────────────
    @staticmethod
    def _explorer_icon(name: str, entry_type: str) -> str:
        if entry_type == "dir":
            return "📁"
        ext = os.path.splitext(name.lower())[1]
        if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"):
            return "🖼️"
        if ext in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".3gp"):
            return "🎬"
        if ext in (".mp3", ".wav", ".aac", ".ogg", ".m4a", ".flac"):
            return "🎵"
        if ext == ".apk":
            return "📦"
        if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
            return "🗜️"
        if ext in (".pdf", ".doc", ".docx", ".txt", ".json", ".csv", ".py", ".xml"):
            return "📄"
        return "📃"

    def explorer_navigate(self, remote_dir: str):
        target = (remote_dir or "/sdcard").strip()
        if target != "/":
            target = target.rstrip("/")
        self.explorer_path_var.set(target)
        self.remote_path_var.set(target)

        def task():
            entries = self.adb.list_directory_entries(target)

            def update_ui():
                self._explorer_entries = entries
                self._explorer_render()

            self.root.after(0, update_ui)
            dirs_n = sum(1 for e in entries if e["type"] == "dir")
            files_n = len(entries) - dirs_n
            return f"📂 {target} — {dirs_n} folders, {files_n} files"

        self.run_async("Open Folder", task, require_device=True)

    def explorer_go_up(self):
        cur = (self.explorer_path_var.get() or "/sdcard").strip().rstrip("/")
        if not cur or cur == "/":
            parent = "/"
        else:
            parent = cur.rsplit("/", 1)[0] or "/"
        self.explorer_navigate(parent)

    def _explorer_render(self):
        query = (self.explorer_filter_var.get() or "").strip().lower()
        self.explorer_tree.delete(*self.explorer_tree.get_children())
        shown = 0
        for it in self._explorer_entries:
            if query and query not in it["name"].lower():
                continue
            icon = self._explorer_icon(it["name"], it["type"])
            self.explorer_tree.insert(
                "",
                "end",
                values=(f"{icon} {it['name']}", it["size"], it["modified"], it["type"], it["path"]),
            )
            shown += 1
        self.explorer_count_var.set(f"{shown} / {len(self._explorer_entries)} items")

    def _explorer_selected_item(self) -> Optional[tuple[str, str, str]]:
        sel = self.explorer_tree.selection()
        if not sel:
            return None
        vals = self.explorer_tree.item(sel[0], "values")
        if not vals or len(vals) < 5:
            return None
        display_name, entry_type, full_path = str(vals[0]), str(vals[3]), str(vals[4])
        raw_name = display_name.split(" ", 1)[1] if " " in display_name else display_name
        return raw_name, entry_type, full_path

    def _explorer_on_select(self, _event=None):
        item = self._explorer_selected_item()
        if item:
            self.remote_path_var.set(item[2])

    def _explorer_on_double_click(self, _event=None):
        item = self._explorer_selected_item()
        if not item:
            return
        _name, entry_type, full_path = item
        if entry_type == "dir":
            self.explorer_filter_var.set("")
            self.explorer_navigate(full_path)
        else:
            self.explorer_download_selected()

    def explorer_download_selected(self):
        item = self._explorer_selected_item()
        if not item:
            messagebox.showinfo("Select Item", "ডাউনলোড করার জন্য প্রথমে একটি ফাইল বা ফোল্ডার সিলেক্ট করুন।")
            return
        name, entry_type, full_path = item
        if not self._ensure_device_selected():
            return
        save_dir = filedialog.askdirectory(title="পিসিতে কোথায় সেভ করবেন?")
        if not save_dir:
            return
        dst = save_dir if entry_type == "dir" else os.path.join(save_dir, name)
        self._start_transfer_with_progress(f"Download {name}", is_push=False, src=full_path, dst=dst)

    def explorer_upload_here(self):
        if not self._ensure_device_selected():
            return
        local_file = filedialog.askopenfilename(title="ফোনে আপলোড করার ফাইল বেছে নিন")
        if not local_file:
            return
        cur_dir = (self.explorer_path_var.get() or "/sdcard").strip().rstrip("/") or "/"
        fname = os.path.basename(local_file)
        dst = f"/{fname}" if cur_dir == "/" else f"{cur_dir}/{fname}"
        self._start_transfer_with_progress(f"Upload {fname}", is_push=True, src=local_file, dst=dst)

    def explorer_new_folder(self):
        if not self._ensure_device_selected():
            return
        cur_dir = (self.explorer_path_var.get() or "/sdcard").strip().rstrip("/") or "/"
        folder_name = simpledialog.askstring("New Folder", f"{cur_dir}-এর ভেতর নতুন ফোল্ডারের নাম লিখুন:", parent=self.root)
        if not folder_name or not folder_name.strip():
            return
        new_path = f"/{folder_name.strip()}" if cur_dir == "/" else f"{cur_dir}/{folder_name.strip()}"

        def task():
            self.adb.mkdir(new_path)
            entries = self.adb.list_directory_entries(cur_dir)
            self.root.after(0, lambda: (setattr(self, "_explorer_entries", entries), self._explorer_render()))
            return f"➕ নতুন ফোল্ডার তৈরি হয়েছে: {new_path}"

        self.run_async("Create Folder", task, require_device=True)

    def explorer_rename_selected(self):
        item = self._explorer_selected_item()
        if not item:
            messagebox.showinfo("Select Item", "রিনেম করার জন্য প্রথমে একটি আইটেম সিলেক্ট করুন।")
            return
        old_name, _etype, old_path = item
        new_name = simpledialog.askstring("Rename", "নতুন নাম লিখুন:", initialvalue=old_name, parent=self.root)
        if not new_name or not new_name.strip() or new_name.strip() == old_name:
            return
        cur_dir = (self.explorer_path_var.get() or "/sdcard").strip().rstrip("/") or "/"
        new_path = f"/{new_name.strip()}" if cur_dir == "/" else f"{cur_dir}/{new_name.strip()}"

        def task():
            self.adb.rename_path(old_path, new_path)
            entries = self.adb.list_directory_entries(cur_dir)
            self.root.after(0, lambda: (setattr(self, "_explorer_entries", entries), self._explorer_render()))
            return f"✏️ রিনেম সম্পন্ন: {old_name} → {new_name.strip()}"

        self.run_async("Rename Item", task, require_device=True)

    def explorer_delete_selected(self):
        item = self._explorer_selected_item()
        if not item:
            messagebox.showinfo("Select Item", "ডিলিট করার জন্য প্রথমে একটি আইটেম সিলেক্ট করুন।")
            return
        name, entry_type, full_path = item
        kind = "ফোল্ডার" if entry_type == "dir" else "ফাইল"
        if not messagebox.askyesno("Confirm Delete", f"আপনি কি নিশ্চিতভাবে এই {kind}টি ডিলিট করতে চান?\n\n{full_path}"):
            return
        cur_dir = (self.explorer_path_var.get() or "/sdcard").strip().rstrip("/") or "/"

        def task():
            self.adb.rm(full_path)
            entries = self.adb.list_directory_entries(cur_dir)
            self.root.after(0, lambda: (setattr(self, "_explorer_entries", entries), self._explorer_render()))
            return f"🗑️ ডিলিট সম্পন্ন: {full_path}"

        self.run_async("Delete Item", task, require_device=True)

    def action_launch_hid_mode(self):
        if not self._ensure_device_selected():
            return
        args = ["-s", self.adb.serial, "--no-video", "--no-audio", "--keyboard=uhid", "--mouse=uhid"]

        def task():
            subprocess.Popen(
                ["scrcpy"] + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return (
                "⌨️ HID Keyboard/Mouse Mode চালু হয়েছে (--no-video --keyboard=uhid --mouse=uhid)!\n"
                "ছোট scrcpy উইন্ডোতে ক্লিক করে সরাসরি পিসির কিবোর্ড/মাউস দিয়ে ফোনে টাইপ ও কন্ট্রোল করুন।"
            )

        self.run_async("HID Mode", task, require_device=True)

    # ─────────────────────────── Tab 10: Macro & Task Automation ───────────────────────────
    def _build_tab_macro(self):
        # 1. Physical Action Recorder Card
        c_rec = self._card(self.tab_macro, "1. 🔴 Physical Action Recorder (ফোনে করা কাজ লাইভ রেকর্ড)")
        rec_row1 = ttk.Frame(c_rec, style="Card.TFrame")
        rec_row1.pack(fill="x", pady=(0, 6))
        ttk.Label(rec_row1, text="Mode:", style="Card.TLabel").pack(side="left")
        self.rec_mode_var = tk.StringVar(value="⚡ Fast Gesture")
        ttk.Combobox(
            rec_row1,
            textvariable=self.rec_mode_var,
            values=[
                "⚡ Fast Gesture",
                "🧠 Smart Element (UI Text)",
                "🎯 Direct Page / Activity (No Touch)",
            ],
            state="readonly",
            width=28,
        ).pack(side="left", padx=6)

        self.rec_waits_var = tk.BooleanVar(value=True)
        self.rec_apps_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(rec_row1, text="Auto Wait", variable=self.rec_waits_var).pack(side="left", padx=6)
        ttk.Checkbutton(rec_row1, text="Track Apps", variable=self.rec_apps_var).pack(side="left", padx=6)

        rec_row2 = ttk.Frame(c_rec, style="Card.TFrame")
        rec_row2.pack(fill="x", pady=(0, 4))
        self.btn_rec_toggle = ttk.Button(
            rec_row2,
            text="🔴 Start Recording",
            style="Danger.TButton",
            command=self.toggle_action_recording,
        )
        self.btn_rec_toggle.pack(side="left", padx=(0, 4))
        self.btn_rec_pause = ttk.Button(
            rec_row2,
            text="⏸️ Pause",
            style="Blue.TButton",
            command=self.toggle_pause_recording,
            state="disabled",
        )
        self.btn_rec_pause.pack(side="left", padx=(0, 4))
        ttk.Button(rec_row2, text="🧹 Clear Steps", command=self._macro_clear_steps).pack(side="left", padx=3)
        self.rec_status_var = tk.StringVar(value="Idle — Start Recording চেপে হাতে ফোন চালান (Tap, Swipe, Long Press, App Switch)")
        ttk.Label(rec_row2, textvariable=self.rec_status_var, style="Muted.TLabel").pack(side="left", padx=8, fill="x", expand=True)

        # 2. Saved Macros & Replay / Loop
        c1 = self._card(self.tab_macro, "2. Saved Macros & Replay / Loop")
        sel_row = ttk.Frame(c1, style="Card.TFrame")
        sel_row.pack(fill="x")
        ttk.Label(sel_row, text="Macro:", style="Card.TLabel").pack(side="left")
        names = self.macro_store.names()
        self.macro_var = tk.StringVar(value=names[0] if names else "Recorded Macro")
        self.macro_combo = ttk.Combobox(sel_row, textvariable=self.macro_var, values=names, width=20)
        self.macro_combo.pack(side="left", padx=6, fill="x", expand=True)
        self.macro_combo.bind("<<ComboboxSelected>>", lambda _: self._macro_load_steps())

        ttk.Label(sel_row, text="Loop:", style="Card.TLabel").pack(side="left", padx=(4, 2))
        self.macro_loop_var = tk.StringVar(value="1")
        ttk.Entry(sel_row, textvariable=self.macro_loop_var, width=4).pack(side="left", padx=(0, 4))

        ttk.Button(sel_row, text="▶️ Run", style="Accent.TButton", command=self.action_run_selected_macro).pack(side="left", padx=2)
        ttk.Button(sel_row, text="⏹️ Stop", style="Danger.TButton", command=self.action_stop_macro).pack(side="left", padx=2)
        ttk.Button(sel_row, text="💾 Save", style="Blue.TButton", command=self._macro_save).pack(side="left", padx=2)
        ttk.Button(sel_row, text="🗑️", command=self._macro_delete).pack(side="left", padx=2)

        # 3. Automation Step Sequencer
        c2 = self._card(self.tab_macro, "3. Automation Step Sequencer")
        tree_wrap = ttk.Frame(c2, style="Card.TFrame")
        tree_wrap.pack(fill="both", expand=True, pady=(0, 8))
        self.macro_tree = ttk.Treeview(tree_wrap, columns=("step", "action", "arg", "note"), show="headings", height=11)
        self.macro_tree.heading("step", text="#")
        self.macro_tree.heading("action", text="Action")
        self.macro_tree.heading("arg", text="Argument")
        self.macro_tree.heading("note", text="Details / Element")
        self.macro_tree.column("step", width=36, stretch=False)
        self.macro_tree.column("action", width=95, stretch=False)
        self.macro_tree.column("arg", width=210, stretch=True)
        self.macro_tree.column("note", width=180, stretch=True)
        m_sc = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.macro_tree.yview)
        self.macro_tree.configure(yscrollcommand=m_sc.set)
        self.macro_tree.pack(side="left", fill="both", expand=True)
        m_sc.pack(side="right", fill="y")

        add_row = ttk.Frame(c2, style="Card.TFrame")
        add_row.pack(fill="x", pady=(0, 6))
        self.macro_act_var = tk.StringVar(value="tap")
        ttk.Combobox(
            add_row,
            textvariable=self.macro_act_var,
            values=[
                "open_activity",
                "open_url",
                "launch_app",
                "tap",
                "tap_text",
                "swipe",
                "wait",
                "text",
                "keyevent",
                "wake",
                "unlock",
                "home",
                "back",
            ],
            state="readonly",
            width=14,
        ).pack(side="left", padx=(0, 6))
        self.macro_arg_var = tk.StringVar()
        ttk.Entry(add_row, textvariable=self.macro_arg_var).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(add_row, text="➕ Add", style="Blue.TButton", command=self._macro_add_step).pack(side="left", padx=2)
        ttk.Button(add_row, text="➖ Remove", command=self._macro_remove_step).pack(side="left", padx=2)
        ttk.Button(add_row, text="⬆️", width=3, command=lambda: self._macro_move_step(-1)).pack(side="left", padx=2)
        ttk.Button(add_row, text="⬇️", width=3, command=lambda: self._macro_move_step(1)).pack(side="left", padx=2)

        ttk.Label(
            c2,
            text="💡 উদাহরণ arg: tap → 540,1200 | tap_text → Next | swipe → 500,1500,500,500,300 | wait → 1.5 | launch_app → com.android.chrome",
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w")

        self._macro_steps: List[dict] = []
        self._macro_load_steps()

    def toggle_pause_recording(self):
        if self.action_recorder is None:
            return
        if self.action_recorder.is_paused:
            self.action_recorder.resume()
            self.btn_rec_pause.configure(text="⏸️ Pause")
            self.rec_status_var.set(f"🔴 Recording resumed... ({len(self._macro_steps)} steps captured)")
            self.write_console("Action Recorder", "▶️ রেকর্ডিং আবার চালু (Resumed) হয়েছে।")
        else:
            self.action_recorder.pause()
            self.btn_rec_pause.configure(text="▶️ Resume")
            self.rec_status_var.set(f"⏸️ Recording Paused ({len(self._macro_steps)} steps) — এখন ফোনে কাজ করলে রেকর্ড হবে না।")
            self.write_console("Action Recorder", "⏸️ রেকর্ডিং পজ (Paused) করা হয়েছে।")

    def toggle_action_recording(self):
        if self.action_recorder is not None:
            self.action_recorder.stop()
            self.action_recorder = None
            self.btn_rec_toggle.configure(text="🔴 Start Recording")
            self.btn_rec_pause.configure(text="⏸️ Pause", state="disabled")
            self.rec_status_var.set(f"⏹️ Recording stopped — মোট {len(self._macro_steps)}টি স্টেপ রেডি। চাইলে Save বা Run করুন।")
            self.write_console("Action Recorder", f"⏹️ রেকর্ডিং বন্ধ হয়েছে ({len(self._macro_steps)} steps)।")
            return

        if not self._ensure_device_selected():
            return

        mode_txt = self.rec_mode_var.get()
        direct = "Direct" in mode_txt
        smart = "Smart" in mode_txt
        rec_waits = bool(self.rec_waits_var.get())
        track_apps = bool(self.rec_apps_var.get())

        def on_step(step: dict, log_line: str):
            def apply_ui():
                self._macro_steps.append(step)
                self._macro_render_tree()
                children = self.macro_tree.get_children()
                if children:
                    self.macro_tree.see(children[-1])
                self.rec_status_var.set(f"🔴 Recording... ({len(self._macro_steps)} steps captured)")
                self.write_console("Recorded", log_line)

            self.root.after(0, apply_ui)

        def on_status(msg: str):
            self.root.after(0, lambda m=msg: (self.rec_status_var.set(m), self.write_console("Action Recorder", m)))

        def on_replace_last(step: dict, log_line: str):
            def apply_ui():
                # Replace the preceding launcher tap (and any wait after it) so app isn't opened twice
                while self._macro_steps and self._macro_steps[-1].get("action") == "wait":
                    self._macro_steps.pop()
                if self._macro_steps and self._macro_steps[-1].get("action") in ("tap", "tap_text"):
                    self._macro_steps[-1] = step
                else:
                    self._macro_steps.append(step)
                self._macro_render_tree()
                children = self.macro_tree.get_children()
                if children:
                    self.macro_tree.see(children[-1])
                self.rec_status_var.set(f"🔴 Recording... ({len(self._macro_steps)} steps captured)")
                self.write_console("Recorded", log_line)

            self.root.after(0, apply_ui)

        recorder = PhysicalActionRecorder(
            self.adb,
            on_step=on_step,
            on_status=on_status,
            on_replace_last=on_replace_last,
            smart_mode=smart,
            direct_mode=direct,
            record_waits=rec_waits,
            track_apps=track_apps,
        )
        self.action_recorder = recorder
        self.btn_rec_toggle.configure(text="⏹️ Stop Recording")
        self.btn_rec_pause.configure(text="⏸️ Pause", state="normal")
        self.rec_status_var.set(
            "⏳ Starting Direct Page / Activity Watcher..." if direct else "⏳ Calibrating touchscreen (getevent -p & wm size)..."
        )

        def start_worker():
            try:
                cal = recorder.start()
                if direct:
                    status_msg = "🔴 Recording (Direct Page / Activity) — শুধু App ও Page রেকর্ড হবে (কোনো Touch/Swipe নয়)"
                    console_msg = (
                        "🎯 Direct Page / Activity মোডে রেকর্ডিং শুরু হয়েছে!\n"
                        "এখন হাতে ফোনে যে যে অ্যাপ ও পেজে যাবেন, টাচ/সোয়াইপ ছাড়াই সরাসরি সেগুলো রেকর্ড হবে।"
                    )
                else:
                    mode_lbl = "Smart Element" if smart else "Fast Gesture"
                    status_msg = f"🔴 Recording ({mode_lbl}) | Screen {cal.screen_w}x{cal.screen_h} (max {cal.max_x}x{cal.max_y})"
                    console_msg = (
                        f"🔴 {mode_lbl} মোডে রেকর্ডিং শুরু হয়েছে!\n"
                        f"Screen: {cal.screen_w}x{cal.screen_h} | Touch Max: {cal.max_x}x{cal.max_y} ({cal.touch_device or 'auto'})\n"
                        "এখন হাতে ফোন নিয়ে কাজ করুন — প্রতিটি Tap/Swipe/Long Press/App Switch লাইভ রেকর্ড হবে।"
                    )
                self.root.after(
                    0,
                    lambda s=status_msg, c=console_msg: (
                        self.rec_status_var.set(s),
                        self.write_console("Action Recorder Started", c),
                    ),
                )
            except Exception as exc:
                self.action_recorder = None
                self.root.after(
                    0,
                    lambda e=exc: (
                        self.btn_rec_toggle.configure(text="🔴 Start Recording"),
                        self.btn_rec_pause.configure(text="⏸️ Pause", state="disabled"),
                        self.rec_status_var.set(f"❌ Recorder ব্যর্থ: {e}"),
                        self.write_console("Recorder Error", str(e)),
                    ),
                )

        threading.Thread(target=start_worker, daemon=True).start()

    def _macro_clear_steps(self):
        self._macro_steps = []
        self._macro_render_tree()
        self.rec_status_var.set("🧹 সব স্টেপ ক্লিয়ার করা হয়েছে। নতুন করে রেকর্ড বা স্টেপ যোগ করুন।")

    def _macro_load_steps(self):
        self._macro_steps = self.macro_store.get(self.macro_var.get().strip())
        self._macro_render_tree()

    def _macro_render_tree(self):
        self.macro_tree.delete(*self.macro_tree.get_children())
        for idx, st in enumerate(self._macro_steps, 1):
            self.macro_tree.insert("", "end", values=(idx, st.get("action", ""), st.get("arg", ""), st.get("note", "")))

    def _macro_add_step(self):
        self._macro_steps.append({"action": self.macro_act_var.get(), "arg": self.macro_arg_var.get().strip(), "note": ""})
        self.macro_arg_var.set("")
        self._macro_render_tree()

    def _macro_remove_step(self):
        sel = self.macro_tree.selection()
        if not sel:
            return
        idx = self.macro_tree.index(sel[0])
        if 0 <= idx < len(self._macro_steps):
            self._macro_steps.pop(idx)
            self._macro_render_tree()

    def _macro_move_step(self, direction: int):
        sel = self.macro_tree.selection()
        if not sel:
            return
        idx = self.macro_tree.index(sel[0])
        new_idx = idx + direction
        if 0 <= idx < len(self._macro_steps) and 0 <= new_idx < len(self._macro_steps):
            self._macro_steps[idx], self._macro_steps[new_idx] = self._macro_steps[new_idx], self._macro_steps[idx]
            self._macro_render_tree()
            children = self.macro_tree.get_children()
            if new_idx < len(children):
                self.macro_tree.selection_set(children[new_idx])

    def _macro_save(self):
        name = self.macro_var.get().strip()
        if not name:
            messagebox.showwarning("Macro Name", "ম্যাক্রোর নাম দিন।")
            return
        self.macro_store.set_macro(name, self._macro_steps)
        self.macro_combo["values"] = self.macro_store.names()
        self.write_console("Macro Saved", f"✔ '{name}' ({len(self._macro_steps)} steps) সেভ হয়েছে।")

    def _macro_delete(self):
        name = self.macro_var.get().strip()
        if name in self.macro_store.macros:
            self.macro_store.delete_macro(name)
            names = self.macro_store.names()
            self.macro_combo["values"] = names
            self.macro_var.set(names[0] if names else "")
            self._macro_load_steps()
            self.write_console("Macro Deleted", f"🗑️ '{name}' ডিলিট করা হয়েছে।")

    def action_stop_macro(self):
        if self.macro_stop_event is not None:
            self.macro_stop_event.set()
            self.write_console("Macro", "⏹️ ম্যাক্রো থামানোর সিগন্যাল পাঠানো হয়েছে...")

    def action_run_selected_macro(self):
        if self.action_recorder is not None:
            messagebox.showwarning("Recording Active", "ম্যাক্রো রান করার আগে Recording বন্ধ করুন (Stop Recording)।")
            return
        name = self.macro_var.get().strip()
        if not name:
            messagebox.showinfo("No Macro", "কোনো ম্যাক্রো সিলেক্ট করা নেই।")
            return
        try:
            repeat = max(1, int(self.macro_loop_var.get().strip() or "1"))
        except ValueError:
            repeat = 1
        steps = list(self._macro_steps) if self._macro_steps else self.macro_store.get(name)
        profile = self.profile_store.get_by_fingerprint(self.current_fingerprint) if hasattr(self, "current_fingerprint") and self.current_fingerprint else None
        stop_ev = threading.Event()
        self.macro_stop_event = stop_ev
        self.run_async(
            f"Macro: {name} (x{repeat})",
            lambda: execute_macro(self.adb, steps, profile=profile, repeat=repeat, stop_event=stop_ev),
            require_device=True,
        )

    def _on_close(self):
        self.perf_monitoring = False
        self.auto_sync_active = False
        if self.action_recorder:
            try:
                self.action_recorder.stop()
            except Exception:
                pass
        if self.macro_stop_event:
            self.macro_stop_event.set()
        if self.logcat_proc:
            try:
                self.logcat_proc.terminate()
            except Exception:
                pass
        self.root.destroy()


class SavedProfilesDialog(tk.Toplevel):
    """Modal manager to view or delete all saved offline/online device profiles."""

    def __init__(self, parent: tk.Tk, store: DeviceProfileStore, on_select: Callable[[dict], None]):
        super().__init__(parent)
        self.title("Saved Device Profiles Manager")
        self.geometry("760x400")
        self.configure(bg=BG_PANEL)
        self.store = store
        self.on_select = on_select

        tk.Label(
            self,
            text="📂 সংরক্ষিত সকল ডিভাইস প্রোফাইল",
            bg=BG_PANEL,
            fg=ACCENT,
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=(12, 6))

        cols = ("name", "lock_type", "process", "updated", "fingerprint")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=11)
        self.tree.heading("name", text="Device Name")
        self.tree.heading("lock_type", text="Lock Type")
        self.tree.heading("process", text="Unlock Process")
        self.tree.heading("updated", text="Updated")
        self.tree.heading("fingerprint", text="Fingerprint")
        self.tree.column("name", width=130)
        self.tree.column("lock_type", width=95)
        self.tree.column("process", width=180)
        self.tree.column("updated", width=120)
        self.tree.column("fingerprint", width=210)
        self.tree.pack(fill="both", expand=True, padx=14, pady=6)

        btn_row = ttk.Frame(self, style="Card.TFrame")
        btn_row.pack(fill="x", padx=14, pady=(4, 12))
        ttk.Button(btn_row, text="📝 Load into Editor", style="Accent.TButton", command=self._load_selected).pack(side="left", padx=4)
        ttk.Button(btn_row, text="🗑️ Delete Selected", style="Danger.TButton", command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="right", padx=4)

        self._reload()

    def _reload(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for p in self.store.list_all():
            self.tree.insert(
                "",
                "end",
                values=(
                    p.get("name", ""),
                    p.get("lock_type", ""),
                    p.get("unlock_process", ""),
                    p.get("updated_at", ""),
                    p.get("fingerprint", ""),
                ),
            )

    def _selected_fp(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        return vals[4] if len(vals) >= 5 else None

    def _load_selected(self):
        fp = self._selected_fp()
        if not fp:
            return
        p = self.store.get(fp)
        if p:
            self.on_select(p)
            self.destroy()

    def _delete_selected(self):
        fp = self._selected_fp()
        if not fp:
            return
        if messagebox.askyesno("Confirm Delete", f"প্রোফাইলটি মুছে ফেলতে চান?\n\n{fp}", parent=self):
            self.store.delete(fp)
            self._reload()


class WiFiQRPairDialog(tk.Toplevel):
    """Graphical QR Code Pairing Wizard rendered directly on a Tkinter Canvas (no Pillow needed)."""

    def __init__(self, parent: tk.Tk, adb: ADB, on_connected: Callable[[], None], log_callback: Callable[[str, str], None]):
        super().__init__(parent)
        self.title("Wireless Debugging — QR Pairing Wizard")
        self.geometry("520x620")
        self.configure(bg=BG_PANEL)
        self.resizable(False, False)
        self.adb = adb
        self.on_connected = on_connected
        self.log_callback = log_callback

        self.zc: Optional[Zeroconf] = None
        self.browser_pair = None
        self.browser_conn = None
        self.closed = False

        self.protocol("WM_DELETE_WINDOW", self._close)

        tk.Label(
            self,
            text="📷 ADB Wireless Debugging QR Pairing",
            bg=BG_PANEL,
            fg=ACCENT,
            font=("Segoe UI", 13, "bold"),
        ).pack(pady=(14, 4))

        self.info_label = tk.Label(
            self,
            text=(
                "ফোনে যান: Settings → Developer options → Wireless debugging\n"
                "→ Pair device with QR code এবং নিচের QR কোডটি স্ক্যান করুন:"
            ),
            bg=BG_PANEL,
            fg=FG_TEXT,
            font=("Segoe UI", 9),
            justify="center",
        )
        self.info_label.pack(pady=(0, 10))

        self.canvas = tk.Canvas(self, width=320, height=320, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(pady=6)

        self.status_lbl = tk.Label(
            self,
            text="⏳ Pairing সার্ভিস খোঁজা হচ্ছে... (১২০ সেকেন্ড)",
            bg=BG_PANEL,
            fg=WARNING,
            font=("Segoe UI", 10, "bold"),
        )
        self.status_lbl.pack(pady=10)

        btn_frame = ttk.Frame(self, style="Card.TFrame")
        btn_frame.pack(pady=6)
        ttk.Button(btn_frame, text="📶 Show Host WiFi QR First", command=self._show_host_wifi_qr).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="🔑 Show ADB Pairing QR", style="Accent.TButton", command=self._start_adb_pairing).pack(side="left", padx=6)

        self._start_adb_pairing()

    def _draw_qr_on_canvas(self, data: str):
        self.canvas.delete("all")
        qr = qrcode.QRCode(border=2)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        size = 320
        cell = size / n
        for r, row in enumerate(matrix):
            for c, val in enumerate(row):
                if val:
                    x0, y0 = c * cell, r * cell
                    x1, y1 = (c + 1) * cell, (r + 1) * cell
                    self.canvas.create_rectangle(x0, y0, x1, y1, fill="#000000", outline="#000000")

    def _show_host_wifi_qr(self):
        ssid, wifi_pass = "", ""
        if os.name == "nt":
            try:
                out = subprocess.run(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace"
                ).stdout
                for line in out.splitlines():
                    if "SSID" in line and "BSSID" not in line:
                        ssid = line.split(":", 1)[1].strip()
                        break
                if ssid:
                    prof = subprocess.run(
                        ["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace"
                    ).stdout
                    for line in prof.splitlines():
                        if "Key Content" in line:
                            wifi_pass = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

        if not ssid:
            ssid = simpledialog.askstring("WiFi SSID", "আপনার WiFi-এর নাম (SSID) লিখুন:", parent=self) or ""
        if not wifi_pass and ssid:
            wifi_pass = simpledialog.askstring("WiFi Password", f"'{ssid}' WiFi-এর পাসওয়ার্ড লিখুন:", parent=self) or ""
        if not ssid:
            return

        wifi_qr_data = f"WIFI:T:WPA;S:{ssid};P:{wifi_pass};;"
        self.info_label.configure(
            text=f"ধাপ ১: ফোনের ক্যামেরা দিয়ে স্ক্যান করে '{ssid}' ওয়াইফাইতে কানেক্ট করুন।\nতারপর 'Show ADB Pairing QR' বাটনে ক্লিক করুন।"
        )
        self._draw_qr_on_canvas(wifi_qr_data)
        self.status_lbl.configure(text=f"WiFi QR প্রদর্শিত হচ্ছে (SSID: {ssid})", fg=ACCENT)

    def _start_adb_pairing(self):
        self._cleanup_zeroconf()

        service_name = f"adbtool-{secrets.token_hex(4)}"
        password = secrets.token_urlsafe(12)
        qr_data = f"WIFI:T:ADB;S:{service_name};P:{password};;"

        self.info_label.configure(
            text=(
                "ফোনে যান: Settings → Developer options → Wireless debugging\n"
                "→ Pair device with QR code এবং নিচের QR কোডটি স্ক্যান করুন:"
            )
        )
        self._draw_qr_on_canvas(qr_data)
        self.status_lbl.configure(text="⏳ Pairing সার্ভিস খোঁজা হচ্ছে... (ফোনে স্ক্যান করুন)", fg=WARNING)

        threading.Thread(
            target=self._mDNS_worker,
            args=(service_name, password),
            daemon=True,
        ).start()

    def _mDNS_worker(self, service_name: str, password: str):
        found_pair = threading.Event()
        pair_info = {}
        pairing_type = "_adb-tls-pairing._tcp.local."

        def on_pairing_service(zeroconf, service_type, name, state_change):
            if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
                return
            if not name.lower().startswith(service_name.lower() + "."):
                return
            info = zeroconf.get_service_info(service_type, name, timeout=5000)
            if not info:
                return
            addrs = [a for a in info.parsed_addresses() if ":" not in a]
            if addrs:
                pair_info["ip"] = addrs[0]
                pair_info["port"] = info.port
                found_pair.set()

        self.zc = Zeroconf()
        self.browser_pair = ServiceBrowser(self.zc, pairing_type, handlers=[on_pairing_service])

        if not found_pair.wait(timeout=120) or self.closed:
            if not self.closed:
                self.after(0, lambda: self.status_lbl.configure(text="❌ টাইমআউট: Pairing সার্ভিস পাওয়া যায়নি।", fg=DANGER))
            return

        target = f"{pair_info['ip']}:{pair_info['port']}"
        self.after(0, lambda: self.status_lbl.configure(text=f"🔗 পেয়ার করা হচ্ছে ({target})...", fg=ACCENT))

        try:
            pair_out = ADB(timeout=30).run(["pair", target, password])
        except Exception as e:
            self.after(0, lambda err=e: self.status_lbl.configure(text=f"❌ Pair ব্যর্থ: {err}", fg=DANGER))
            return

        if "successfully" not in pair_out.lower():
            self.after(0, lambda: self.status_lbl.configure(text=f"❌ Pair ব্যর্থ: {pair_out}", fg=DANGER))
            return

        self.after(0, lambda: self.status_lbl.configure(text="⏳ Connection পোর্ট খোঁজা হচ্ছে...", fg=WARNING))

        found_conn = threading.Event()
        conn_info = {}
        connect_type = "_adb-tls-connect._tcp.local."

        def on_connect_service(zeroconf, service_type, name, state_change):
            if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
                return
            info = zeroconf.get_service_info(service_type, name, timeout=5000)
            if not info:
                return
            addrs = [a for a in info.parsed_addresses() if ":" not in a]
            if addrs and addrs[0] == pair_info["ip"]:
                conn_info["ip"] = addrs[0]
                conn_info["port"] = info.port
                found_conn.set()

        self.browser_conn = ServiceBrowser(self.zc, connect_type, handlers=[on_connect_service])

        if found_conn.wait(timeout=30) and not self.closed:
            conn_target = f"{conn_info['ip']}:{conn_info['port']}"
            conn_out = self.adb.connect(conn_target)
            msg = f"Pair: {pair_out}\nConnect: {conn_out}\nDevice: {conn_target}"
            self.after(0, lambda: self._on_pair_success(msg))
        elif not self.closed:
            self.after(
                0,
                lambda: self.status_lbl.configure(
                    text="✔ পেয়ার সফল! এবার IP:PORT দিয়ে ম্যানুয়ালি কানেক্ট করুন।",
                    fg=ACCENT,
                ),
            )

    def _on_pair_success(self, msg: str):
        self.status_lbl.configure(text="✔ সফলভাবে পেয়ার এবং কানেক্ট হয়েছে!", fg=ACCENT)
        self.log_callback(msg, "WiFi QR Pairing Success")
        self.on_connected()
        messagebox.showinfo("Success", f"সফলভাবে কানেক্ট হয়েছে!\n\n{msg}", parent=self)
        self._close()

    def _cleanup_zeroconf(self):
        if self.browser_conn:
            try:
                self.browser_conn.cancel()
            except Exception:
                pass
            self.browser_conn = None
        if self.browser_pair:
            try:
                self.browser_pair.cancel()
            except Exception:
                pass
            self.browser_pair = None
        if self.zc:
            try:
                self.zc.close()
            except Exception:
                pass
            self.zc = None

    def _close(self):
        self.closed = True
        self._cleanup_zeroconf()
        self.destroy()


class ScreenshotPreviewDialog(tk.Toplevel):
    """Instant screenshot preview popup using built-in tkinter PhotoImage (PNG)."""

    def __init__(self, parent: tk.Tk, image_path: str):
        super().__init__(parent)
        self.title(f"Screenshot Preview — {os.path.basename(image_path)}")
        self.configure(bg=BG_PANEL)
        self.resizable(False, False)

        tk.Label(
            self,
            text="📸 স্ক্রিনশট সেভ হয়েছে এবং Clipboard-এ কপি করা হয়েছে!",
            bg=BG_PANEL,
            fg=ACCENT,
            font=("Segoe UI", 10, "bold"),
        ).pack(pady=(10, 6), padx=14)

        try:
            raw_img = tk.PhotoImage(file=image_path)
            max_h = 560
            h = raw_img.height()
            if h > max_h:
                factor = max(2, (h + max_h - 1) // max_h)
                self._img = raw_img.subsample(factor, factor)
            else:
                self._img = raw_img
            tk.Label(self, image=self._img, bg=BG_INPUT, bd=1, relief="solid").pack(padx=14, pady=6)
        except Exception as e:
            tk.Label(self, text=f"Preview unavailable: {e}", bg=BG_PANEL, fg=WARNING).pack(padx=14, pady=14)

        ttk.Button(self, text="Close", style="Accent.TButton", command=self.destroy).pack(pady=(4, 12))


def main():
    root = ctk.CTk()
    ADBPhoneControlUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
