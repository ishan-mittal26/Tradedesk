"""
TradeDesk startup orchestrator — Phases 0 and 1 (Sprint 1).

Phase 0: Config load, dependency check, folder + DB creation, holiday check.
Phase 1: Tkinter + ttkbootstrap splash window with per-phase progress messages.

Phases 2–6 are stubs that will be filled in subsequent sprints.
"""

import importlib
import json
import logging
import logging.handlers
import os
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pytz
import ttkbootstrap as ttk
from ttkbootstrap.constants import SUCCESS, WARNING, DANGER, INFO

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

CONFIG_PATH    = Path("config/config.json")
HOLIDAYS_PATH  = Path("config/holidays.json")
INSTRUMENTS_PATH = Path("config/instruments.json")

REQUIRED_DIRS = [
    Path("config"),
    Path("core"),
    Path("feeds"),
    Path("data"),
    Path("logs"),
    Path("logs/daily_reports"),
    Path("logs/weekly_reports"),
    Path("tests"),
]

REQUIRED_PACKAGES = [
    ("pandas",           "pandas"),
    ("pandas_ta",        "pandas_ta"),
    ("upstox_client",    "upstox-python-sdk"),
    ("ttkbootstrap",     "ttkbootstrap"),
    ("pytz",             "pytz"),
    ("dotenv",           "python-dotenv"),
    ("colorama",         "colorama"),
    ("mplfinance",       "mplfinance"),
    ("anthropic",        "anthropic"),
]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class StartupResult:
    def __init__(self) -> None:
        self.success: bool = False
        self.is_holiday: bool = False
        self.holiday_name: str = ""
        self.config: dict = {}
        self.instruments: dict = {}
        self.missing_packages: list[str] = []
        self.errors: list[str] = []

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        logger.error(msg)


# ---------------------------------------------------------------------------
# Phase 0 — headless bootstrap
# ---------------------------------------------------------------------------

def run_phase_0() -> StartupResult:
    result = StartupResult()

    _setup_logging()
    logger.info("=== TradeDesk startup — Phase 0 ===")

    # 0a. Create required directories
    for d in REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Directories verified.")

    # 0b. Load .env if present (user manages this file)
    _load_dotenv_if_present()

    # 0c. Load config.json
    config = _load_json(CONFIG_PATH)
    if config is None:
        result.add_error(f"Cannot read {CONFIG_PATH}. Ensure it exists.")
        return result
    result.config = config
    logger.info("config.json loaded.")

    # 0d. Load instruments.json
    instruments = _load_json(INSTRUMENTS_PATH)
    if instruments is None:
        result.add_error(f"Cannot read {INSTRUMENTS_PATH}.")
        return result
    result.instruments = instruments
    logger.info("instruments.json loaded.")

    # 0e. Dependency check
    result.missing_packages = _check_dependencies()
    if result.missing_packages:
        msg = "Missing packages: " + ", ".join(result.missing_packages)
        result.add_error(msg)
        logger.error(f"Run: pip install -r requirements.txt")
        return result
    logger.info("All dependencies present.")

    # 0f. Initialise database (creates tables + seeds lot_size_history)
    try:
        from core.local_db import init_db
        db_path = Path(config.get("paths", {}).get("db", "data/market_data.db"))
        init_db(db_path)
    except Exception as exc:
        result.add_error(f"DB init failed: {exc}")
        return result

    # 0g. Holiday check
    holiday_name = _check_holiday(HOLIDAYS_PATH)
    if holiday_name:
        result.is_holiday = True
        result.holiday_name = holiday_name
        logger.info(f"Today is a market holiday: {holiday_name}")

    result.success = True
    logger.info("Phase 0 complete.")
    return result


# ---------------------------------------------------------------------------
# Phase 1 — splash window
# ---------------------------------------------------------------------------

def run_phase_1(result: StartupResult, next_phase_fn: Optional[Callable] = None) -> tk.Tk:
    """
    Open the ttkbootstrap splash window.
    next_phase_fn: optional callable invoked after splash via root.after()
                   (used by later sprints to chain Phase 2 in background).
    Returns the root Tk window.
    """
    logger.info("=== TradeDesk startup — Phase 1 ===")

    theme = result.config.get("display", {}).get("theme", "darkly")
    title = result.config.get("display", {}).get("window_title", "TradeDesk")
    min_w = result.config.get("display", {}).get("window_min_width", 1280)
    min_h = result.config.get("display", {}).get("window_min_height", 800)

    root = ttk.Window(themename=theme)
    root.title(title)
    root.minsize(min_w, min_h)
    root.resizable(True, True)
    _centre_window(root, min_w, min_h)

    splash = _SplashScreen(root, result)

    if result.is_holiday:
        splash.set_status(
            f"Market Holiday: {result.holiday_name}  —  Monitor is OFF.",
            level="warning",
        )
        splash.set_progress(100)
        logger.info("Splash: holiday message displayed.")
    else:
        splash.set_status("Initialising…", level="info")
        splash.set_progress(15)

        if next_phase_fn:
            root.after(200, next_phase_fn)

    logger.info("Phase 1 complete — splash window open.")
    return root


# ---------------------------------------------------------------------------
# Phase 2 — data loading (stub; filled in Sprint 5)
# ---------------------------------------------------------------------------

def run_phase_2() -> None:
    """Load historical candles, compute indicators, build S&R zones."""
    logger.info("=== TradeDesk startup — Phase 2 ===")

    # Phase 2 sub-step: SMC zone initialization
    try:
        config = _load_json(CONFIG_PATH) or {}
        smc_cfg = config.get('smc', {})
        if smc_cfg.get('enabled', False):
            try:
                import smart_money_concepts  # noqa
                logger.info('SMC library available. Zones ready for Sprint 9.')
                # update_splash('Initializing SMC zones...')  # uncomment when splash exists
            except ImportError:
                logger.warning('smart-money-concepts not installed. Run: pip install smart-money-concepts>=0.0.6')
        else:
            logger.debug('SMC disabled in config.')
    except Exception as e:
        logger.warning(f'SMC init skipped: {e}')  # Never crash startup for SMC

    logger.info("Phase 2 complete.")


# ---------------------------------------------------------------------------
# Splash screen widget
# ---------------------------------------------------------------------------

class _SplashScreen(ttk.Frame):

    _PHASE_MESSAGES: list[str] = [
        "Loading configuration…",
        "Opening database…",
        "Loading candles…",
        "Computing indicators…",
        "Building S&R zones…",
        "Fetching news & global data…",
        "Connecting to Upstox…",
        "Starting monitor…",
    ]

    def __init__(self, parent: tk.Misc, result: StartupResult) -> None:
        super().__init__(parent, padding=40)
        self.pack(fill="both", expand=True)
        self._build(result)

    def _build(self, result: StartupResult) -> None:
        # Title
        ttk.Label(
            self,
            text="TradeDesk",
            font=("Segoe UI", 32, "bold"),
            bootstyle=INFO,
        ).pack(pady=(20, 4))

        ttk.Label(
            self,
            text="AI-Powered Option Buying Terminal",
            font=("Segoe UI", 12),
        ).pack(pady=(0, 2))

        ttk.Label(
            self,
            text="Personal use only  ·  Indian equity derivatives",
            font=("Segoe UI", 9),
            bootstyle="secondary",
        ).pack(pady=(0, 30))

        # Paper trading badge
        if result.config.get("capital", {}).get("paper_trading", True):
            ttk.Label(
                self,
                text="  PAPER TRADING MODE  ",
                font=("Segoe UI", 10, "bold"),
                bootstyle="warning-inverse",
                padding=(8, 4),
            ).pack(pady=(0, 20))

        # Progress bar
        self._progress = ttk.Progressbar(
            self,
            bootstyle="info-striped",
            length=420,
            mode="determinate",
            maximum=100,
        )
        self._progress.pack(pady=(0, 12))

        # Status label
        self._status_var = tk.StringVar(value="Starting…")
        self._status_label = ttk.Label(
            self,
            textvariable=self._status_var,
            font=("Segoe UI", 10),
            bootstyle=INFO,
        )
        self._status_label.pack(pady=(0, 30))

        # Phase checklist
        self._checklist_frame = ttk.Frame(self)
        self._checklist_frame.pack()
        self._phase_labels: list[ttk.Label] = []
        for msg in self._PHASE_MESSAGES:
            lbl = ttk.Label(
                self._checklist_frame,
                text=f"○  {msg}",
                font=("Segoe UI", 9),
                bootstyle="secondary",
            )
            lbl.pack(anchor="w", padx=20)
            self._phase_labels.append(lbl)

        # Errors (hidden until needed)
        self._error_frame = ttk.Frame(self)
        self._error_frame.pack(pady=(20, 0))

        if result.errors:
            for err in result.errors:
                ttk.Label(
                    self._error_frame,
                    text=f"✖  {err}",
                    font=("Segoe UI", 9),
                    bootstyle=DANGER,
                ).pack(anchor="w")

        # Footer
        ttk.Label(
            self,
            text=f"Capital: ₹{result.config.get('capital', {}).get('total', 0):,.0f}   "
                 f"·   {datetime.now(IST).strftime('%d %b %Y')}",
            font=("Segoe UI", 8),
            bootstyle="secondary",
        ).pack(side="bottom", pady=10)

    def set_progress(self, value: int) -> None:
        self._progress["value"] = value
        self.update_idletasks()

    def set_status(self, message: str, level: str = "info") -> None:
        style_map = {
            "info":    INFO,
            "success": SUCCESS,
            "warning": WARNING,
            "danger":  DANGER,
        }
        self._status_var.set(message)
        self._status_label.configure(bootstyle=style_map.get(level, INFO))
        self.update_idletasks()

    def mark_phase_done(self, phase_index: int) -> None:
        """Tick off a phase in the checklist (0-indexed)."""
        if 0 <= phase_index < len(self._phase_labels):
            lbl = self._phase_labels[phase_index]
            current = lbl.cget("text")
            lbl.configure(
                text=current.replace("○", "✓"),
                bootstyle=SUCCESS,
            )
            self.update_idletasks()

    def mark_phase_active(self, phase_index: int) -> None:
        if 0 <= phase_index < len(self._phase_labels):
            lbl = self._phase_labels[phase_index]
            current = lbl.cget("text")
            lbl.configure(
                text=current.replace("○", "▶"),
                bootstyle=INFO,
            )
            self.update_idletasks()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"

    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)


def _load_dotenv_if_present() -> None:
    env_path = Path(".env")
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
            logger.info(".env loaded.")
        except ImportError:
            logger.warning("python-dotenv not installed; .env not loaded.")


def _load_json(path: Path) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"File not found: {path}")
        return None
    except json.JSONDecodeError as exc:
        logger.error(f"Invalid JSON in {path}: {exc}")
        return None


def _check_dependencies() -> list[str]:
    missing: list[str] = []
    for module, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(pip_name)
    return missing


def _check_holiday(holidays_path: Path) -> str:
    """Return holiday name if today is a market holiday, else empty string."""
    data = _load_json(holidays_path)
    if not data:
        return ""
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    year = datetime.now(IST).strftime("%Y")
    holidays = data.get(year, [])
    for h in holidays:
        if h.get("date") == today_str:
            return h.get("name", "Market Holiday")
    return ""


def _centre_window(window: tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    window.geometry(f"{width}x{height}+{x}+{y}")


# ---------------------------------------------------------------------------
# Entry point (used by main.py and tests)
# ---------------------------------------------------------------------------

def start(next_phase_fn: Optional[Callable] = None) -> tuple[Optional[tk.Tk], StartupResult]:
    """
    Run Phase 0 then Phase 1.
    Returns (root_window, result).
    root_window is None if Phase 0 failed before the window could open.
    """
    result = run_phase_0()

    if not result.success:
        print("\n[TradeDesk] Startup failed during Phase 0:")
        for err in result.errors:
            print(f"  ✖  {err}")
        return None, result

    root = run_phase_1(result, next_phase_fn=next_phase_fn)
    return root, result


if __name__ == "__main__":
    root, res = start()
    if root:
        root.mainloop()
