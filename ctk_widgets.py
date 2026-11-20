"""
Modern CustomTkinter widget layer & dark Slate/Emerald theme helpers.
Provides drop-in CTk-backed replacements for standard ttk widgets while
keeping Treeview, Scrollbar, Progressbar, and Notebook cleanly styled.
"""

from tkinter import ttk as _ttk
import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

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


def _px_width(chars: int | None, char_px: int = 8, min_px: int = 44) -> int | None:
    if chars is None:
        return None
    if chars <= 60:
        return max(min_px, int(chars * char_px))
    return int(chars)


class Frame(ctk.CTkFrame):
    def __init__(self, master=None, style: str = "", padding=None, **kwargs):
        fg = kwargs.pop("fg_color", None)
        if fg is None:
            # ttk.Notebook / plain tk containers cannot resolve CTk "transparent" cleanly
            if isinstance(master, _ttk.Notebook):
                fg = BG_DARK
            else:
                fg = "transparent"
        corner = kwargs.pop("corner_radius", 0)
        super().__init__(master, fg_color=fg, corner_radius=corner, **kwargs)


class Label(ctk.CTkLabel):
    def __init__(
        self,
        master=None,
        text: str = "",
        textvariable=None,
        style: str = "",
        foreground: str | None = None,
        font=None,
        justify: str = "left",
        width: int | None = None,
        **kwargs,
    ):
        if style == "Header.TLabel":
            text_color = foreground or ACCENT
            font = font or ("Segoe UI", 17, "bold")
        elif style == "SubHeader.TLabel":
            text_color = foreground or ACCENT
            font = font or ("Segoe UI", 12, "bold")
        elif style == "Muted.TLabel":
            text_color = foreground or FG_MUTED
            font = font or ("Segoe UI", 10)
            kwargs.setdefault("wraplength", 610)
        else:
            text_color = foreground or FG_TEXT
            font = font or ("Segoe UI", 11)

        kw = dict(
            master=master,
            text=text,
            textvariable=textvariable,
            text_color=text_color,
            fg_color="transparent",
            font=font,
            justify=justify,
            anchor=kwargs.pop("anchor", "w"),
        )
        if width is not None:
            kw["width"] = _px_width(width, char_px=8, min_px=24)
        kw.update(kwargs)
        super().__init__(**kw)


class Button(ctk.CTkButton):
    def __init__(self, master=None, text: str = "", style: str = "", command=None, width: int | None = None, **kwargs):
        if style == "Accent.TButton":
            fg, hov, tc = ACCENT, ACCENT_HOVER, "#052e16"
        elif style == "Blue.TButton":
            fg, hov, tc = ACCENT_BLUE, "#2563eb", "#ffffff"
        elif style == "Danger.TButton":
            fg, hov, tc = DANGER, "#dc2626", "#ffffff"
        else:
            fg, hov, tc = BORDER, "#475569", FG_TEXT

        # Default width=28 lets CTkButton auto-size tightly to its text instead of CTk's 140px default
        btn_width = _px_width(width, char_px=10, min_px=34) if width is not None else 28
        kw = dict(
            master=master,
            text=text,
            command=command,
            width=btn_width,
            fg_color=kwargs.pop("fg_color", fg),
            hover_color=kwargs.pop("hover_color", hov),
            text_color=kwargs.pop("text_color", tc),
            text_color_disabled=kwargs.pop("text_color_disabled", FG_MUTED),
            corner_radius=kwargs.pop("corner_radius", 8),
            height=kwargs.pop("height", 30),
            font=kwargs.pop("font", ("Segoe UI", 11, "bold")),
        )
        kw.update(kwargs)
        super().__init__(**kw)


class Entry(ctk.CTkEntry):
    def __init__(self, master=None, textvariable=None, width: int | None = None, show: str = "", **kwargs):
        ent_width = _px_width(width, char_px=8, min_px=46) if width is not None else 80
        kw = dict(
            master=master,
            textvariable=textvariable,
            width=ent_width,
            fg_color=BG_INPUT,
            border_color=BORDER,
            border_width=1,
            text_color=FG_TEXT,
            corner_radius=8,
            height=30,
            font=("Segoe UI", 11),
        )
        if show:
            kw["show"] = show
        kw.update(kwargs)
        super().__init__(**kw)


class Combobox(ctk.CTkComboBox):
    def __init__(self, master=None, textvariable=None, values=None, state: str = "normal", width: int | None = None, **kwargs):
        self._select_callbacks = []
        vals = [str(v) for v in (values or [])]
        cmb_width = _px_width(width, char_px=8, min_px=84) if width is not None else 120
        kw = dict(
            master=master,
            variable=textvariable,
            values=vals,
            state=state,
            width=cmb_width,
            command=self._on_selected,
            fg_color=BG_INPUT,
            border_color=BORDER,
            border_width=1,
            button_color=BORDER,
            button_hover_color="#475569",
            dropdown_fg_color=BG_PANEL,
            dropdown_hover_color=ACCENT,
            dropdown_text_color=FG_TEXT,
            text_color=FG_TEXT,
            corner_radius=8,
            height=30,
            font=("Segoe UI", 11),
        )
        kw.update(kwargs)
        super().__init__(**kw)

    def _on_selected(self, _choice):
        for cb in self._select_callbacks:
            cb(None)

    def bind(self, sequence=None, command=None, add=None):
        if sequence == "<<ComboboxSelected>>" and command is not None:
            self._select_callbacks.append(command)
            return
        return super().bind(sequence, command, add)

    def __setitem__(self, key, value):
        if key == "values":
            self.configure(values=[str(v) for v in (value or [])])
            return
        self.configure(**{key: value})

    def __getitem__(self, key):
        if key == "values":
            return self.cget("values")
        return self.cget(key)


class Checkbutton(ctk.CTkCheckBox):
    def __init__(self, master=None, text: str = "", variable=None, style: str = "", **kwargs):
        super().__init__(
            master=master,
            text=text,
            variable=variable,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color=BORDER,
            text_color=FG_TEXT,
            corner_radius=6,
            font=("Segoe UI", 11),
            **kwargs,
        )


class Radiobutton(ctk.CTkRadioButton):
    def __init__(self, master=None, text: str = "", variable=None, value=None, style: str = "", **kwargs):
        super().__init__(
            master=master,
            text=text,
            variable=variable,
            value=value,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color=BORDER,
            text_color=FG_TEXT,
            font=("Segoe UI", 11),
            **kwargs,
        )


# Pass-through ttk widgets that have no CTk native equivalent
Treeview = _ttk.Treeview
Scrollbar = _ttk.Scrollbar
Notebook = _ttk.Notebook
Progressbar = _ttk.Progressbar
Style = _ttk.Style


class SidebarNotebook(ctk.CTkFrame):
    """Modern left-sidebar + center content switcher replacing crowded horizontal tabs."""

    def __init__(self, master=None, sidebar_width: int = 160, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.sidebar = ctk.CTkFrame(
            self,
            width=sidebar_width,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        self.sidebar.pack(side="left", fill="y", padx=(0, 10))
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(
            self.sidebar,
            text="NAVIGATION",
            text_color=FG_MUTED,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 6))

        self.nav_scroll = ctk.CTkScrollableFrame(
            self.sidebar,
            fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT,
        )
        self.nav_scroll.pack(fill="both", expand=True, padx=2, pady=(0, 8))

        self.content_area = ctk.CTkFrame(self, fg_color="transparent")
        self.content_area.pack(side="left", fill="both", expand=True)

        self._pages: list[tuple[str, ctk.CTkBaseClass, ctk.CTkBaseClass]] = []
        self._buttons: list[ctk.CTkButton] = []
        self._active_idx: int = -1

    def add_scrollable_tab(self, text: str) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(
            self.content_area,
            fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT,
        )
        return self._register_tab(text, page, page)

    def add_plain_tab(self, text: str) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content_area, fg_color="transparent")
        return self._register_tab(text, page, page)

    def _register_tab(self, text: str, container, content):
        idx = len(self._pages)
        btn = ctk.CTkButton(
            self.nav_scroll,
            text=f" {text}",
            anchor="w",
            height=34,
            corner_radius=8,
            fg_color="transparent",
            hover_color=BORDER,
            text_color=FG_MUTED,
            font=("Segoe UI", 11, "bold"),
            command=lambda i=idx: self.select(i),
        )
        btn.pack(fill="x", padx=4, pady=3)
        self._pages.append((text, container, content))
        self._buttons.append(btn)
        if idx == 0:
            self.select(0)
        return content

    def select(self, index: int):
        if not (0 <= index < len(self._pages)):
            return
        if self._active_idx == index:
            return
        for i, (_t, container, _c) in enumerate(self._pages):
            if i == index:
                container.pack(fill="both", expand=True)
                self._buttons[i].configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#052e16")
            else:
                container.pack_forget()
                self._buttons[i].configure(fg_color="transparent", hover_color=BORDER, text_color=FG_MUTED)
        self._active_idx = index

    def tabs(self) -> list[int]:
        return list(range(len(self._pages)))

    def tab(self, tab_id: int, option: str | None = None):
        if option == "text":
            return self._pages[int(tab_id)][0]
        return {"text": self._pages[int(tab_id)][0]}
