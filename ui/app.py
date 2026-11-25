"""
TradeDesk main application window — Sprint 8 (PySide6 rewrite).

Thread: MAIN THREAD (Qt event loop runs here).

Responsibilities:
  - Create PySide6 QMainWindow with qt-material dark_teal theme.
  - Two-tab QTabWidget: Screen 1 (TA) + Screen 2 placeholder (Sprint 11).
  - Shared queue (maxsize=500) bridges all threads to UI.
  - _poll_queue() drains queue every 100ms via QTimer.
  - Routes messages by type using ROUTING_TABLE (GAP 2 / GAP 27).
  - "alert" messages fan-out to monitor_panel + analysis_panel.
  - Top bar: instrument selector, market clock, WebSocket status.
  - Bottom bar: Settings button, Paper Trading button.
  - Startup phases 2-4 run in background thread; splash → dashboard on complete.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QLabel, QPushButton, QComboBox,
    QStatusBar, QFrame, QSizePolicy, QDialog,
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QColor
from qt_material import apply_stylesheet

from core.local_db import get_connection
from core.startup import run_phase_0

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
CONFIG_PATH = Path("config/config.json")


# ---------------------------------------------------------------------------
# Helper — load config safely
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class TradeDeskApp(QMainWindow):
    """
    Root window.  Created on Main thread; never touched from other threads.
    All state changes from background threads arrive via shared_queue.
    """

    POLL_INTERVAL_MS = 100

    def __init__(self):
        super().__init__()

        self._cfg = _load_config()
        self._active_symbol: str = (
            self._cfg.get("instruments", {}).get("active", ["NIFTY"])[0]
        )

        # Shared queue: Data / Network / Agent → Main thread (GAP 2)
        self.shared_queue: queue.Queue = queue.Queue(maxsize=500)

        # Flags
        self._startup_complete = False
        self._paper_trading_on: bool = self._cfg.get("paper_trading", {}).get(
            "screen1_auto_simulate", True
        )

        # Build UI skeleton
        self._build_ui()

        # Wire routing table (must come after panels are created)
        self._build_routing_table()

        # Start clock QTimer (1000ms)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()  # immediate first tick

        # Kick off background startup (phases 2-4)
        self._run_background_startup()

        # Start queue polling QTimer (100ms)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_queue)
        self._poll_timer.start(self.POLL_INTERVAL_MS)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        title = self._cfg.get("display", {}).get("window_title", "TradeDesk")
        self.setWindowTitle(title)
        self.setMinimumSize(
            self._cfg.get("display", {}).get("window_min_width", 1440),
            self._cfg.get("display", {}).get("window_min_height", 900),
        )

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_top_bar())
        layout.addWidget(self._build_main_notebook(), stretch=1)
        layout.addWidget(self._build_bottom_bar())

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setStyleSheet("QFrame#topBar { background-color: #1e1e2e; }")
        bar.setFixedHeight(40)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        title_lbl = QLabel("TradeDesk")
        title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        layout.addWidget(title_lbl)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Instrument:"))

        instruments = self._cfg.get("instruments", {}).get("active", ["NIFTY"])
        self._symbol_combo = QComboBox()
        self._symbol_combo.addItems(instruments)
        self._symbol_combo.setCurrentText(self._active_symbol)
        self._symbol_combo.setFixedWidth(110)
        self._symbol_combo.currentTextChanged.connect(self._on_symbol_change)
        layout.addWidget(self._symbol_combo)

        layout.addSpacing(8)

        self._ws_status_lbl = QLabel("WS: Connecting…")
        self._ws_status_lbl.setStyleSheet("color: #FF9800;")
        layout.addWidget(self._ws_status_lbl)

        layout.addStretch(1)

        self._market_status_lbl = QLabel("PRE-MARKET")
        layout.addWidget(self._market_status_lbl)

        layout.addSpacing(12)

        self._clock_lbl = QLabel("--:--:-- IST")
        self._clock_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        layout.addWidget(self._clock_lbl)

        return bar

    def _build_main_notebook(self) -> QTabWidget:
        self._notebook = QTabWidget()
        self._notebook.setDocumentMode(True)

        s1_widget = QWidget()
        self._notebook.addTab(s1_widget, "  Screen 1 — TA  ")
        self._build_screen1(s1_widget)

        s2_lbl = QLabel("Screen 2 — SMC Dashboard\n(Sprint 11)")
        s2_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s2_lbl.setFont(QFont("Segoe UI", 16))
        self._notebook.addTab(s2_lbl, "  Screen 2 — SMC  ")

        return self._notebook

    def _build_screen1(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left: vertical splitter — chart (top) + monitor (bottom)
        left_splitter = QSplitter(Qt.Orientation.Vertical)

        chart_frame = QFrame()
        chart_frame.setMinimumHeight(520)
        QVBoxLayout(chart_frame).setContentsMargins(0, 0, 0, 0)

        monitor_frame = QFrame()
        monitor_frame.setMinimumHeight(320)
        QVBoxLayout(monitor_frame).setContentsMargins(0, 0, 0, 0)

        left_splitter.addWidget(chart_frame)
        left_splitter.addWidget(monitor_frame)
        left_splitter.setStretchFactor(0, 6)
        left_splitter.setStretchFactor(1, 4)

        # Right: tab widget
        right_tabs = QTabWidget()
        right_tabs.setDocumentMode(True)

        analysis_frame  = QFrame()
        options_frame   = QFrame()
        journal_frame   = QFrame()
        briefing_frame  = QFrame()
        paper_frame     = QFrame()
        strategy_frame  = QFrame()

        for frame in (analysis_frame, options_frame, journal_frame,
                      briefing_frame, paper_frame, strategy_frame):
            QVBoxLayout(frame).setContentsMargins(0, 0, 0, 0)

        right_tabs.addTab(analysis_frame,  " Analysis ")
        right_tabs.addTab(options_frame,   " Options ")
        right_tabs.addTab(journal_frame,   " Journal ")
        right_tabs.addTab(briefing_frame,  " Briefing ")
        right_tabs.addTab(paper_frame,     " Paper ")
        right_tabs.addTab(strategy_frame,  " Strategy ")

        splitter.addWidget(left_splitter)
        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)

        # Lazy imports to avoid circular dependencies
        from ui.panels.chart_panel            import ChartPanel
        from ui.panels.monitor_panel          import MonitorPanel
        from ui.panels.analysis_panel         import AnalysisPanel
        from ui.panels.options_panel          import OptionsPanel
        from ui.panels.journal_panel          import JournalPanel
        from ui.panels.briefing_panel         import BriefingPanel
        from ui.panels.paper_trading_panel    import PaperTradingPanel
        from ui.panels.strategy_builder_panel import StrategyBuilderPanel

        self.chart_panel = ChartPanel(
            chart_frame, self.shared_queue, self._active_symbol
        )
        chart_frame.layout().addWidget(self.chart_panel)

        self.monitor_panel = MonitorPanel(monitor_frame, self.shared_queue)
        monitor_frame.layout().addWidget(self.monitor_panel)

        self.analysis_panel = AnalysisPanel(
            analysis_frame, self.shared_queue, self._active_symbol,
            on_analyze_click=self._run_agents,
        )
        analysis_frame.layout().addWidget(self.analysis_panel)

        self.options_panel = OptionsPanel(options_frame)
        options_frame.layout().addWidget(self.options_panel)

        self.journal_panel = JournalPanel(journal_frame, self.shared_queue)
        journal_frame.layout().addWidget(self.journal_panel)

        self.briefing_panel = BriefingPanel(briefing_frame)
        briefing_frame.layout().addWidget(self.briefing_panel)

        self.paper_trading_panel = PaperTradingPanel(paper_frame, self.shared_queue)
        paper_frame.layout().addWidget(self.paper_trading_panel)

        self.strategy_builder_panel = StrategyBuilderPanel(strategy_frame)
        strategy_frame.layout().addWidget(self.strategy_builder_panel)

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bar.setStyleSheet("QFrame#bottomBar { background-color: #1e1e2e; }")
        bar.setFixedHeight(36)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(8)

        layout.addWidget(QLabel("TradeDesk v1.0  |  Sprint 8"))
        layout.addStretch(1)

        settings_btn = QPushButton("⚙ Settings")
        settings_btn.setFixedWidth(110)
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)

        self._paper_btn = QPushButton(
            "Paper Trading: ON" if self._paper_trading_on else "Paper Trading: OFF"
        )
        self._paper_btn.setFixedWidth(160)
        self._paper_btn.clicked.connect(self._toggle_paper_trading)
        if self._paper_trading_on:
            self._paper_btn.setStyleSheet("color: #4CAF50;")
        layout.addWidget(self._paper_btn)

        return bar

    # ------------------------------------------------------------------
    # Routing table (GAP 2 / GAP 27)
    # ------------------------------------------------------------------

    def _build_routing_table(self) -> None:
        self.ROUTING_TABLE: dict = {
            "tick":                self.monitor_panel.update_tick,
            "live_price":          self.chart_panel.update_live_price,
            "candle":              self.chart_panel.on_new_candle,
            "alert":               self._on_alert,
            "verdict":             self.analysis_panel.display_verdict,
            "pipeline_update":     self.analysis_panel.update_pipeline_score,
            "ws_status":           self._on_ws_status,
            "expiry_alert":        self.monitor_panel.display_expiry_alert,
            "paper_trade_update":  self._on_paper_trade_update,
            "splash_update":       self._on_splash_update,
            "startup_complete":    self._on_startup_complete,
            "morning_briefing":    self.briefing_panel.update_briefing,
            "options_update":      self.options_panel.update_options_data,
            "instruments_changed": self._on_instruments_changed,
            "smc_verdict":         lambda d: None,
            "smc_update":          lambda d: None,
        }

    # ------------------------------------------------------------------
    # poll_queue — GAP 27
    # ------------------------------------------------------------------

    @Slot()
    def _poll_queue(self) -> None:
        """Drain shared_queue on Main thread every 100ms."""
        try:
            while True:
                msg = self.shared_queue.get_nowait()
                msg_type = msg.get("type")
                handler = self.ROUTING_TABLE.get(msg_type)
                if handler:
                    try:
                        handler(msg["data"])
                    except Exception as exc:
                        log.error(
                            "poll_queue handler error type=%s: %s",
                            msg_type, exc, exc_info=True,
                        )
                else:
                    log.warning("Unknown queue message type: %s", msg_type)
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------

    @Slot()
    def _update_clock(self) -> None:
        now = datetime.now(IST)
        self._clock_lbl.setText(now.strftime("%H:%M:%S IST"))
        hhmm = now.strftime("%H:%M")
        if hhmm < "09:15":
            self._market_status_lbl.setText("PRE-MARKET")
            self._market_status_lbl.setStyleSheet("color: #9e9e9e;")
        elif hhmm <= "15:30":
            self._market_status_lbl.setText("MARKET OPEN")
            self._market_status_lbl.setStyleSheet("color: #4CAF50;")
        else:
            self._market_status_lbl.setText("CLOSED")
            self._market_status_lbl.setStyleSheet("color: #f44336;")

    # ------------------------------------------------------------------
    # Fan-out handlers
    # ------------------------------------------------------------------

    def _on_instruments_changed(self, data: dict) -> None:
        active = data.get("active", [])
        if not active:
            return
        self._symbol_combo.blockSignals(True)
        self._symbol_combo.clear()
        self._symbol_combo.addItems(active)
        if self._active_symbol not in active:
            self._active_symbol = active[0]
        idx = self._symbol_combo.findText(self._active_symbol)
        if idx >= 0:
            self._symbol_combo.setCurrentIndex(idx)
        self._symbol_combo.blockSignals(False)

    def _on_alert(self, data: dict) -> None:
        self.monitor_panel.display_alert_card(data)
        self.analysis_panel.update_alert_score(data.get("alert_score", 0))

    def _on_paper_trade_update(self, data: dict) -> None:
        self.journal_panel.on_paper_trade_update(data)
        self.paper_trading_panel.on_paper_trade_update(data)

    # ------------------------------------------------------------------
    # WS status handler
    # ------------------------------------------------------------------

    def _on_ws_status(self, data: dict) -> None:
        status = data.get("status", "")
        hhmm = datetime.now(IST).strftime("%H:%M")
        in_market_hours = "09:15" <= hhmm <= "15:30"

        if status == "CONNECTED":
            self._ws_status_lbl.setText("WS: Connected ●")
            self._ws_status_lbl.setStyleSheet("color: #4CAF50;")
        elif status == "FAILED":
            if not in_market_hours:
                self._ws_status_lbl.setText("Market Closed")
                self._ws_status_lbl.setStyleSheet("color: #9e9e9e;")
            else:
                self._ws_status_lbl.setText("WS: Failed ✖")
                self._ws_status_lbl.setStyleSheet("color: #f44336;")
        elif status == "RECONNECTING":
            self._ws_status_lbl.setText("WS: Reconnecting…")
            self._ws_status_lbl.setStyleSheet("color: #FF9800;")
        self.monitor_panel.update_ws_status(data)

    # ------------------------------------------------------------------
    # Startup handlers
    # ------------------------------------------------------------------

    def _on_splash_update(self, data: dict) -> None:
        msg = data.get("message", "")
        pct = data.get("pct", 0)
        self.setWindowTitle(f"TradeDesk — Starting {pct}%  {msg}")
        log.debug("Splash: %s (%d%%)", msg, pct)

    def _on_startup_complete(self, data: dict) -> None:
        self._startup_complete = True
        self.setWindowTitle(
            self._cfg.get("display", {}).get("window_title", "TradeDesk")
        )
        log.info("Startup complete — checking market hours before live feeds")
        self._try_start_live_feeds()

    def _try_start_live_feeds(self) -> None:
        now = datetime.now(IST)
        hhmm = now.strftime("%H:%M")

        if "09:15" <= hhmm <= "15:30":
            self._ws_status_lbl.setText("WS: Connecting…")
            self._ws_status_lbl.setStyleSheet("color: #FF9800;")
            log.info("Market open — starting live feeds")
            try:
                self._start_live_feeds()
            except Exception as exc:
                log.error("Failed to start live feeds: %s", exc)
        elif hhmm > "15:30":
            self._ws_status_lbl.setText("Market Closed")
            self._ws_status_lbl.setStyleSheet("color: #9e9e9e;")
            log.info("Market closed for today — WebSocket not started")
        else:
            minutes_to_open = (9 * 60 + 15) - (now.hour * 60 + now.minute)
            self._ws_status_lbl.setText(f"Pre-Market — opens in {minutes_to_open}m")
            self._ws_status_lbl.setStyleSheet("color: #FF9800;")
            log.info("Pre-market — retrying in 30s (opens in %dm)", minutes_to_open)
            QTimer.singleShot(30000, self._try_start_live_feeds)

    def _run_background_startup(self) -> None:
        def _worker():
            try:
                _do_background_startup(self.shared_queue)
            except Exception as exc:
                log.error("Background startup failed: %s", exc, exc_info=True)
                self.shared_queue.put({"type": "startup_complete", "data": {}})

        t = threading.Thread(target=_worker, daemon=True, name="startup-bg")
        t.start()

    def _start_live_feeds(self) -> None:
        import os
        from feeds.upstox_stream import SYMBOL_TO_KEY

        all_active: list[str] = [
            s for s in self._cfg.get("instruments", {}).get("active", [self._active_symbol])
            if s in SYMBOL_TO_KEY
        ]
        if not all_active:
            all_active = [self._active_symbol]

        sq = self.shared_queue
        self._active_sym_ref: list[str] = [self._active_symbol]

        monitor_queue: queue.Queue = queue.Queue(maxsize=500)
        try:
            from monitor.monitor import MonitorEngine
            mon = MonitorEngine(symbols=all_active)
            mon.start(monitor_queue, sq)
            log.info("Monitor thread started for %s", all_active)
        except Exception as exc:
            log.error("Monitor start failed: %s", exc)

        try:
            access_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
            tick_queue: queue.Queue = queue.Queue(maxsize=1000)
            from feeds.upstox_stream import UpstoxStream
            stream = UpstoxStream(
                access_token=access_token,
                symbols=all_active,
                tick_queue=tick_queue,
            )
            stream.start()
            log.info("WebSocket thread started for %s", all_active)
        except Exception as exc:
            log.error("WebSocket start failed: %s", exc)
            return

        sym_ref = self._active_sym_ref

        def _data_loop(tq=tick_queue, mq=monitor_queue):
            from core.candle_builder import CandleBuilder
            from core.local_db import upsert_candles
            cb = CandleBuilder()
            ws_notified = False

            while True:
                try:
                    tick = tq.get(timeout=1.0)
                except queue.Empty:
                    continue
                except Exception:
                    continue

                active_sym = sym_ref[0]

                if not ws_notified:
                    try:
                        sq.put_nowait({"type": "ws_status", "data": {"status": "CONNECTED"}})
                    except queue.Full:
                        log.warning("Data loop: queue full on ws_status CONNECTED")
                    ws_notified = True

                if tick.symbol == active_sym:
                    try:
                        sq.put_nowait({"type": "tick", "data": {
                            "symbol": tick.symbol,
                            "ltp":    tick.ltp,
                            "change": 0.0,
                            "change_pct": 0.0,
                        }})
                    except queue.Full:
                        log.warning("Data loop: queue full on tick")
                    live = cb.get_live_candle(tick.symbol)
                    if live:
                        try:
                            sq.put_nowait({"type": "live_price", "data": {
                                "symbol":      tick.symbol,
                                "ltp":         tick.ltp,
                                "live_candle": live,
                            }})
                        except queue.Full:
                            log.warning("Data loop: queue full on live_price")

                try:
                    closed = cb.on_tick(tick.symbol, tick.timestamp_unix, tick.ltp, tick.volume)
                except Exception as exc:
                    log.warning("CandleBuilder error: %s", exc)
                    closed = []

                if closed:
                    try:
                        upsert_candles(closed)
                    except Exception as exc:
                        log.warning("DB candle upsert error: %s", exc)

                    # Recompute indicators for closed candles (Bug #18 fix)
                    # Without this, monitor reads stale startup-time indicators
                    try:
                        from analysis.indicators import run_for_symbol_timeframe
                        _closed_symbols = set(
                            (c["symbol"], c["timeframe"]) for c in closed
                        )
                        for _sym_c, _tf_c in _closed_symbols:
                            try:
                                run_for_symbol_timeframe(_sym_c, _tf_c, limit=100)
                            except Exception as _e_ind:
                                log.debug(
                                    "Indicator recompute failed %s/%s: %s",
                                    _sym_c, _tf_c, _e_ind,
                                )
                    except Exception as exc:
                        log.debug("Indicator recompute import failed: %s", exc)

                    for candle in closed:
                        try:
                            mq.put_nowait({
                                "type":      "CANDLE_CLOSED",
                                "symbol":    candle["symbol"],
                                "timeframe": candle["timeframe"],
                                "candle":    candle,
                                "timestamp": candle["timestamp"],
                            })
                        except queue.Full:
                            log.warning("Data loop: monitor queue full on candle")
                        if candle["symbol"] == active_sym:
                            try:
                                sq.put_nowait({"type": "candle", "data": {
                                    "symbol":    candle["symbol"],
                                    "timeframe": candle["timeframe"],
                                }})
                            except queue.Full:
                                log.warning(
                                    "Data loop: queue full on candle %s/%s",
                                    candle["symbol"], candle["timeframe"],
                                )

        dt = threading.Thread(target=_data_loop, daemon=True, name="data")
        dt.start()
        log.info("Data thread started")

        def _watch_stream(s=stream):
            s._thread.join()
            if s._stop_event.is_set() and not s.is_connected():
                try:
                    sq.put_nowait({"type": "ws_status", "data": {"status": "FAILED"}})
                except queue.Full:
                    log.warning("Watch stream: queue full on ws_status FAILED")

        threading.Thread(target=_watch_stream, daemon=True, name="ws-watcher").start()

    # ------------------------------------------------------------------
    # Instrument change
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_symbol_change(self, symbol: str) -> None:
        self._active_symbol = symbol
        if hasattr(self, "_active_sym_ref"):
            self._active_sym_ref[0] = symbol
        log.info("Instrument changed to %s", symbol)
        self.chart_panel.set_symbol(symbol)
        self.analysis_panel.set_symbol(symbol)
        self.options_panel.set_symbol(symbol)

    # ------------------------------------------------------------------
    # Analyze button handler
    # ------------------------------------------------------------------

    def _run_agents(self) -> None:
        """Submit agents 1-7 to Agent thread. Called from analysis_panel button."""
        symbol = self._active_symbol

        def _agent_worker():
            try:
                from agents.agent_base import AgentOrchestrator, assemble_agent_input
                from analysis.pipeline import run_pipeline
                from core.local_db import get_connection

                agent_input = assemble_agent_input(symbol)
                orchestrator = AgentOrchestrator()

                conn = get_connection()
                ind_row = conn.execute(
                    "SELECT * FROM indicators WHERE symbol=? AND timeframe='5min' "
                    "ORDER BY timestamp DESC LIMIT 1", (symbol,)
                ).fetchone()

                # Fetch previous 15m indicator for B4 Supertrend flip detection
                _prev_ind_15m_rows = conn.execute(
                    "SELECT supertrend_dir FROM indicators "
                    "WHERE symbol=? AND timeframe='15m' "
                    "ORDER BY timestamp DESC LIMIT 2",
                    (symbol,)
                ).fetchall()
                _prev_supertrend_dir = (
                    int(_prev_ind_15m_rows[1]["supertrend_dir"])
                    if len(_prev_ind_15m_rows) >= 2
                    and _prev_ind_15m_rows[1]["supertrend_dir"] is not None
                    else 0
                )

                options_row = conn.execute(
                    "SELECT * FROM options_chain WHERE symbol=? ORDER BY captured_at DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                candles = conn.execute(
                    "SELECT * FROM candles WHERE symbol=? AND timeframe='5min' "
                    "ORDER BY timestamp DESC LIMIT 20", (symbol,)
                ).fetchall()
                sr_zones = conn.execute(
                    "SELECT * FROM support_resistance WHERE symbol=? AND active=1", (symbol,)
                ).fetchall()

                import pytz as _pytz
                from datetime import datetime as _dt
                now_ist = _dt.now(_pytz.timezone("Asia/Kolkata"))

                from feeds.global_fetcher import GlobalFetcher
                vix = GlobalFetcher.get_cached_vix()

                _eqh_eql = {"EQH": [], "EQL": []}
                try:
                    from analysis.eqh_eql_detector import get_eqh_eql
                    import pandas as _pd
                    _candles_df = _pd.DataFrame([dict(c) for c in candles])
                    if "timestamp" in _candles_df.columns:
                        _candles_df.set_index("timestamp", inplace=True)
                    _eqh_eql = get_eqh_eql(symbol, _candles_df)
                except Exception as _e_eql:
                    log.debug("EQH/EQL fetch failed: %s", _e_eql)

                pre = {
                    "symbol":               symbol,
                    "candle":               dict(candles[0]) if candles else {},
                    "indicators":           dict(ind_row) if ind_row else {},
                    "options_snapshot":     dict(options_row) if options_row else {},
                    "eqh_eql_levels":       _eqh_eql,
                    "vix":                  vix,
                    "market_mode":          agent_input.get("market_mode", "MODERATE_RANGE"),
                    "current_time_ist":     now_ist.strftime("%H:%M"),
                    "day_of_week":          now_ist.strftime("%A"),
                    "ohlcv_last_20":        [dict(c) for c in candles],
                    "sr_zones":             [dict(z) for z in sr_zones],
                    "bb_squeeze_duration":  agent_input.get("bb_squeeze_duration", 0),
                    "prev_supertrend_dir":  _prev_supertrend_dir,
                    "open_positions_count": 0,
                    "daily_pnl_rupees":     0.0,
                    "capital":              100000.0,
                }

                pipeline_result = run_pipeline(pre)

                agent_results = orchestrator.run_agents_1_to_6(agent_input)
                verdict = orchestrator.run_agent_7(agent_results, pipeline_result)

                payload = {
                    "agent_7_json":    verdict,
                    "agent_1_json":    agent_results.get("agent_1", {}),
                    "agent_2_json":    agent_results.get("agent_2", {}),
                    "agent_3_json":    agent_results.get("agent_3", {}),
                    "agent_4_json":    agent_results.get("agent_4", {}),
                    "agent_5_json":    agent_results.get("agent_5", {}),
                    "agent_6_json":    agent_results.get("agent_6", {}),
                    "pipeline_result": pipeline_result,
                    "timestamp":       now_ist.isoformat(),
                }
                self.shared_queue.put({"type": "verdict", "data": payload})

                if self._paper_trading_on:
                    try:
                        from journal.auto_paper_s1 import get_instance as get_paper
                        get_paper().on_new_verdict(verdict, pre)
                    except Exception as exc:
                        log.warning("Auto paper trade failed: %s", exc)

            except Exception as exc:
                log.error("Agent run failed: %s", exc, exc_info=True)

        t = threading.Thread(target=_agent_worker, daemon=True, name="agent-run")
        t.start()

    # ------------------------------------------------------------------
    # Paper trading toggle
    # ------------------------------------------------------------------

    def _toggle_paper_trading(self) -> None:
        self._paper_trading_on = not self._paper_trading_on
        if self._paper_trading_on:
            self._paper_btn.setText("Paper Trading: ON")
            self._paper_btn.setStyleSheet("color: #4CAF50;")
            try:
                from journal.auto_paper_s1 import get_instance
                get_instance().start(self.shared_queue)
            except Exception as exc:
                log.error("Paper trading start: %s", exc)
        else:
            self._paper_btn.setText("Paper Trading: OFF")
            self._paper_btn.setStyleSheet("")
            try:
                from journal.auto_paper_s1 import get_instance
                instance = get_instance()
                if hasattr(instance, "_stop_event"):
                    instance._stop_event.set()
                log.info("Paper trading instance signalled to stop.")
            except Exception as exc:
                log.warning("Paper trading stop signal failed: %s", exc)
            log.info("Paper trading disabled")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        from ui.panels.settings_panel import SettingsPanel
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(820, 640)
        layout = QVBoxLayout(dialog)
        panel = SettingsPanel(dialog, self.shared_queue, on_close=dialog.accept)
        layout.addWidget(panel)
        dialog.exec()


# ---------------------------------------------------------------------------
# Background startup worker (GAP 6)
# ---------------------------------------------------------------------------

def _do_background_startup(shared_queue: queue.Queue) -> None:
    """
    Runs in daemon background thread.
    Phases 2-4: load historical data, compute indicators, fetch market data.
    Puts splash_update messages on queue for progress display.
    Puts startup_complete when done.
    """

    def _splash(msg: str, pct: int) -> None:
        shared_queue.put({"type": "splash_update", "data": {"message": msg, "pct": pct}})

    try:
        _splash("Loading historical OHLCV data…", 10)

        # Phase 2: Count candles
        candle_count = 0
        try:
            from core.local_db import get_connection
            conn = get_connection()
            candle_count = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            log.info("Phase 2: %d candles in DB", candle_count)
        except Exception as exc:
            log.warning("Phase 2 DB check: %s", exc)

        # Phase 2: compute indicators for all active instruments
        try:
            from analysis.indicators import run_for_all
            _cfg2 = {}
            try:
                import json as _json2
                with open("config/config.json") as _f2:
                    _cfg2 = _json2.load(_f2)
            except Exception:
                pass
            _active_syms2 = _cfg2.get("instruments", {}).get("active", ["NIFTY"])
            if candle_count > 0:
                results2 = run_for_all(
                    symbols=_active_syms2,
                    timeframes=["5m", "15m", "1h", "4h", "1d", "1w"],
                    limit=1000,
                )
                log.info("Phase 2 indicators computed: %s", results2)
            else:
                log.warning("Phase 2: no candles in DB — skipping indicator computation")
        except Exception as exc:
            log.warning("Phase 2 indicator computation failed: %s", exc)

        # Phase 2b: compute S&R zones for all active instruments
        try:
            from analysis.sr_detector import SRDetector
            import json as _json_sr
            _cfg_sr = {}
            try:
                with open("config/config.json") as _f_sr:
                    _cfg_sr = _json_sr.load(_f_sr)
            except Exception:
                pass
            _active_syms_sr = _cfg_sr.get("instruments", {}).get("active", ["NIFTY"])
            _sr_detector = SRDetector()
            for _sym_sr in _active_syms_sr:
                try:
                    _sr_detector.run_for_symbol(_sym_sr)
                    log.info("Phase 2b: S&R zones computed for %s", _sym_sr)
                except Exception as _e_sr:
                    log.warning(
                        "Phase 2b: S&R computation failed for %s: %s",
                        _sym_sr, _e_sr,
                    )
        except Exception as exc:
            log.warning("Phase 2b S&R computation failed: %s", exc)

        _splash("Computing indicators and S&R zones…", 30)
        time.sleep(0.3)

        # Phase 3: Validate / refresh Upstox access token
        _splash("Validating Upstox token… (check browser if prompted)", 50)
        token = ""
        try:
            import os as _os
            from core.token_manager import get_access_token as _get_token
            token = _get_token()
            _os.environ["UPSTOX_ACCESS_TOKEN"] = token
            log.info("Phase 3: Upstox token validated OK.")
        except Exception as exc:
            log.warning("Phase 3: Upstox token validation failed — %s. WebSocket may fail.", exc)

        # Phase 3b: Fetch today's intraday candles
        if token:
            _splash("Loading today's market data…", 60)
            try:
                import json as _json
                _cfg = {}
                try:
                    with open("config/config.json") as _f:
                        _cfg = _json.load(_f)
                except Exception:
                    pass
                _active_syms = _cfg.get("instruments", {}).get("active", ["NIFTY"])
                from feeds.upstox_rest import UpstoxRestClient, HistoricalLoader
                _client = UpstoxRestClient(access_token=token)
                _loader = HistoricalLoader(_client)
                for _sym in _active_syms:
                    try:
                        _written = _loader.load_instrument(_sym)
                        log.info("Phase 3b: %s intraday loaded — %s", _sym, _written)
                    except Exception as _e:
                        log.warning("Phase 3b: %s intraday load failed — %s", _sym, _e)
            except Exception as exc:
                log.warning("Phase 3b: intraday fetch skipped — %s", exc)

        _splash("Fetching global market data…", 70)

        # Phase 4: Fetch global data + news
        global_data = None
        try:
            from feeds.global_fetcher import GlobalFetcher
            global_data = GlobalFetcher(token).fetch()
            log.info("Phase 4: VIX = %s", global_data.india_vix)
            try:
                shared_queue.put_nowait({
                    "type": "morning_briefing",
                    "data": {"global": global_data.to_dict(), "headlines": []},
                })
            except queue.Full:
                log.warning("Phase 4: queue full on morning_briefing (global)")
        except Exception as exc:
            log.warning("Phase 4 global fetch: %s", exc)

        try:
            from feeds.news_fetcher import get_headlines
            headlines = get_headlines()
            global_dict = global_data.to_dict() if global_data is not None else {}
            try:
                shared_queue.put_nowait({
                    "type": "morning_briefing",
                    "data": {"global": global_dict, "headlines": headlines},
                })
            except queue.Full:
                log.warning("Phase 4: queue full on morning_briefing (headlines)")
        except Exception as exc:
            log.warning("Phase 4 news fetch: %s", exc)

        _splash("Starting network feeds…", 85)

        try:
            _start_network_feeds(shared_queue, token)
        except Exception as exc:
            log.warning("Network feeds start: %s", exc)

        _splash("Ready.", 100)
        time.sleep(0.2)

    finally:
        shared_queue.put({"type": "startup_complete", "data": {}})


def _start_network_feeds(shared_queue: queue.Queue, token: str) -> None:
    """Start daemon network thread for VIX/news/options refresh every 3600s."""

    def _network_loop():
        import time as _time
        import os as _os

        while True:
            _time.sleep(3600)  # 60 minutes — NewsAPI free tier 100 calls/day

            # Step A: VIX refresh
            try:
                from feeds.global_fetcher import get_india_vix
                vix = get_india_vix(token)
                if vix is not None:
                    try:
                        shared_queue.put_nowait({
                            "type": "morning_briefing",
                            "data": {"global": {"india_vix": vix}, "headlines": []},
                        })
                    except queue.Full:
                        log.warning("Network thread: queue full on VIX update")
            except Exception as exc:
                log.warning("Network thread VIX refresh: %s", exc)

            # Step B: News refresh
            try:
                from feeds.news_fetcher import get_headlines
                headlines = get_headlines()
                if headlines:
                    try:
                        shared_queue.put_nowait({
                            "type": "morning_briefing",
                            "data": {"global": {}, "headlines": headlines},
                        })
                    except queue.Full:
                        log.warning("Network thread: queue full on news update")
            except Exception as exc:
                log.warning("Network thread news refresh: %s", exc)

            # Step C: Options chain refresh
            try:
                import upstox_client as _upstox
                from feeds.upstox_rest import INSTRUMENT_KEYS
                from datetime import datetime as _dt
                import pytz as _pytz

                _ist = _pytz.timezone("Asia/Kolkata")
                _today_str = _dt.now(_ist).strftime("%Y-%m-%d")

                _configuration = _upstox.Configuration()
                _configuration.access_token = _os.environ.get("UPSTOX_ACCESS_TOKEN", "")
                _api_client = _upstox.ApiClient(_configuration)
                _options_api = _upstox.OptionsApi(_api_client)

                _cfg_opts = {}
                try:
                    import json as _json
                    with open("config/config.json") as _f:
                        _cfg_opts = _json.load(_f)
                except Exception:
                    pass
                _active_syms_opts = _cfg_opts.get("instruments", {}).get("active", ["NIFTY"])

                for _sym in _active_syms_opts:
                    try:
                        _ikey = INSTRUMENT_KEYS.get(_sym)
                        if not _ikey:
                            continue

                        _contracts_resp = _options_api.get_option_contracts(_ikey)
                        if not _contracts_resp or not _contracts_resp.data:
                            log.warning("Options contracts empty for %s", _sym)
                            continue

                        _expiries = sorted(set(
                            str(getattr(c, "expiry", "") or "")
                            for c in _contracts_resp.data
                            if getattr(c, "expiry", None)
                        ))
                        _valid_expiries = [e for e in _expiries if e >= _today_str]
                        if not _valid_expiries:
                            log.warning("No valid expiry found for %s", _sym)
                            continue
                        _nearest_expiry = _valid_expiries[0]

                        _chain_resp = _options_api.get_put_call_option_chain(_ikey, _nearest_expiry)
                        if not _chain_resp or not _chain_resp.data:
                            log.warning("Options chain empty for %s expiry %s", _sym, _nearest_expiry)
                            continue

                        _chain_data = _chain_resp.data
                        _total_ce_oi = 0
                        _total_pe_oi = 0
                        _strike_data = {}

                        for _row in _chain_data:
                            _strike = getattr(_row, "strike_price", 0) or 0
                            _ce = getattr(_row, "call_options", None)
                            _pe = getattr(_row, "put_options", None)
                            _ce_oi = 0
                            _pe_oi = 0
                            if _ce:
                                _mkt = getattr(_ce, "market_data", None)
                                if _mkt:
                                    _ce_oi = int(getattr(_mkt, "oi", 0) or 0)
                            if _pe:
                                _mkt = getattr(_pe, "market_data", None)
                                if _mkt:
                                    _pe_oi = int(getattr(_mkt, "oi", 0) or 0)
                            _total_ce_oi += _ce_oi
                            _total_pe_oi += _pe_oi
                            _strike_data[float(_strike)] = {
                                "ce_oi": _ce_oi,
                                "pe_oi": _pe_oi,
                            }

                        _pcr = round(_total_pe_oi / _total_ce_oi, 2) if _total_ce_oi > 0 else 0.0

                        _max_pain_strike = 0.0
                        _min_pain = float("inf")
                        for _s in _strike_data:
                            _pain = sum(
                                max(0, _s2 - _s) * _d2["ce_oi"] + max(0, _s - _s2) * _d2["pe_oi"]
                                for _s2, _d2 in _strike_data.items()
                            )
                            if _pain < _min_pain:
                                _min_pain = _pain
                                _max_pain_strike = _s

                        _ltp_row = None
                        try:
                            from core.local_db import get_connection
                            _conn = get_connection()
                            _ltp_row = _conn.execute(
                                "SELECT close FROM candles WHERE symbol=? AND timeframe='5m' "
                                "ORDER BY timestamp DESC LIMIT 1", (_sym,)
                            ).fetchone()
                        except Exception:
                            pass
                        _ltp = float(_ltp_row[0]) if _ltp_row else 0.0

                        _atm_strike = (
                            min(_strike_data.keys(), key=lambda s: abs(s - _ltp))
                            if _strike_data and _ltp > 0 else 0.0
                        )
                        _atm_ce_oi = _strike_data.get(_atm_strike, {}).get("ce_oi", 0)
                        _atm_pe_oi = _strike_data.get(_atm_strike, {}).get("pe_oi", 0)

                        _top_strikes = sorted(
                            [{"strike": s, "ce_oi": d["ce_oi"], "pe_oi": d["pe_oi"]}
                             for s, d in _strike_data.items()],
                            key=lambda x: x["ce_oi"] + x["pe_oi"],
                            reverse=True,
                        )[:5]

                        # DTE — calendar days from today to expiry
                        try:
                            from datetime import date as _date_cls
                            _expiry_date = _dt.strptime(_nearest_expiry, "%Y-%m-%d").date()
                            _today_date = _dt.now(_ist).date()
                            _dte = max(0, (_expiry_date - _today_date).days)
                        except Exception:
                            _dte = 0

                        _iv_rank = None

                        _options_payload = {
                            "symbol":      _sym,
                            "pcr":         _pcr,
                            "max_pain":    _max_pain_strike,
                            "iv_rank":     _iv_rank,
                            "atm_oi_ce":   _atm_ce_oi,
                            "atm_oi_pe":   _atm_pe_oi,
                            "top_strikes": _top_strikes,
                            "expiry":      _nearest_expiry,
                            "dte":         _dte,
                        }

                        try:
                            shared_queue.put_nowait({
                                "type": "options_update", "data": _options_payload
                            })
                            log.info(
                                "Options update queued for %s — PCR=%.2f MaxPain=%.0f DTE=%d",
                                _sym, _pcr, _max_pain_strike, _dte,
                            )
                        except queue.Full:
                            log.warning("Queue full — options update dropped for %s", _sym)

                    except Exception as _exc:
                        log.warning("Network thread options refresh for %s: %s", _sym, _exc)

            except Exception as _exc:
                log.warning("Network thread options block failed: %s", _exc)

    t = threading.Thread(target=_network_loop, daemon=True, name="network")
    t.start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from core.startup import _setup_logging
    _setup_logging()

    from core.local_db import init_db
    init_db()

    import sys
    app = QApplication(sys.argv)
    apply_stylesheet(app, theme="dark_teal.xml")
    # Override qt-material's QPushButton:checked color
    # which makes checkable button text invisible on dark background
    extra_css = """
        QPushButton:checked {
            color: #ffffff;
            font-weight: bold;
        }
        QPushButton:checked:hover {
            color: #ffffff;
        }
    """
    current = app.styleSheet()
    app.setStyleSheet(current + extra_css)
    window = TradeDeskApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
