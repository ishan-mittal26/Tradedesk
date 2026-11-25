"""
Chart panel for TradeDesk — PyQtGraph rewrite.
Thread: MAIN THREAD only.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import os
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
import pyqtgraph as pg
import pytz
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPainterPath,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QButtonGroup, QSizePolicy, QFrame, QMenu,
)

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

CANDLES_TO_SHOW = 80
TIMEFRAME_MAP = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}

COLOR_UP         = "#26a69a"
COLOR_DOWN       = "#ef5350"
COLOR_WICK       = "#888888"
COLOR_EMA20      = "#42A5F5"
COLOR_EMA50      = "#FF9800"
COLOR_EMA200     = "#EF5350"
COLOR_VWAP       = "#FFFFFF"
COLOR_BB_UP      = "#607D8B"
COLOR_BB_LOW     = "#607D8B"
COLOR_BB_MID     = "#546E7A"
COLOR_ST_BULL    = "#26a69a"
COLOR_ST_BEAR    = "#ef5350"
COLOR_SR_STR     = "#FFFFFF"
COLOR_SR_MOD     = "#607D8B"
COLOR_TREND      = "#FFD700"
COLOR_VOL_UP     = "#1B5E20"
COLOR_VOL_DOWN   = "#B71C1C"
COLOR_ADX        = "#AB47BC"
COLOR_RSI        = "#29B6F6"
COLOR_MACD_L     = "#42A5F5"
COLOR_MACD_S     = "#FF7043"
COLOR_MACD_H_POS = "#26a69a"
COLOR_MACD_H_NEG = "#ef5350"
BG_COLOR         = "#1a1a2e"
GRID_COLOR       = "#2a2a4a"

_DASH = Qt.PenStyle.DashLine
_SOLID = Qt.PenStyle.SolidLine

_INDICATOR_LABELS: dict[str, str] = {
    "ema_9":        "EMA 9",
    "ema_20":       "EMA 20",
    "ema_50":       "EMA 50",
    "ema_200":      "EMA 200",
    "vwap":         "VWAP",
    "bb":           "Bollinger Bands",
    "supertrend":   "Supertrend",
    "volume":       "Volume",
    "rsi":          "RSI",
    "macd":         "MACD",
    "adx":          "ADX",
    "atr":          "ATR 14",
    "bb_width":     "BB Width",
    "volume_ratio": "Volume Ratio",
}


# ---------------------------------------------------------------------------
# DateAxisItem
# ---------------------------------------------------------------------------

class DateAxisItem(pg.AxisItem):
    """X-axis that maps integer indices to date/time strings."""

    def __init__(self, timestamps: list, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._timestamps: list = timestamps

    def updateTimestamps(self, timestamps: list) -> None:
        self._timestamps = timestamps

    def tickStrings(self, values, scale, spacing):
        n = len(self._timestamps)
        result = []
        for v in values:
            idx = int(round(v))
            if 0 <= idx < n:
                try:
                    result.append(self._timestamps[idx].strftime("%d/%m\n%H:%M"))
                except Exception:
                    result.append(str(idx))
            else:
                result.append("")
        return result


# ---------------------------------------------------------------------------
# ChartPanel
# ---------------------------------------------------------------------------

class ChartPanel(QWidget):
    """PyQtGraph candlestick chart. Must run on Main thread only."""

    def __init__(self, parent: QWidget, shared_queue, symbol: str) -> None:
        super().__init__(parent)
        self._symbol = symbol
        self._timeframe = "5m"
        self._shared_queue = shared_queue
        self._signal_markers: list = []
        self._forming_candle: Optional[dict] = None
        self._sr_lines: list = []
        self._trend_lines: list = []
        self._active_indicators: dict[str, bool] = {
            "ema_20": True, "ema_50": True, "ema_200": True,
            "vwap": True, "bb": True, "supertrend": True,
            "rsi": True, "macd": True, "volume": True, "adx": True,
            "atr": False, "ema_9": False, "bb_width": False, "volume_ratio": False,
        }
        self._current_df: Optional[pd.DataFrame] = None
        self._current_x: Optional[np.ndarray] = None
        self._candle_items: list = []
        self._live_candle_items: list = []
        self._ema9_line    = None
        self._ema20_line   = None
        self._ema50_line   = None
        self._ema200_line  = None
        self._vwap_line    = None
        self._bb_upper_line = None
        self._bb_lower_line = None
        self._bb_mid_line   = None
        self._bb_fill       = None
        self._st_bull_line  = None
        self._st_bear_line  = None
        self._vol_bars      = None
        self._rsi_line      = None
        self._macd_line_item   = None
        self._macd_signal_item = None
        self._macd_hist_bars   = None
        self._adx_line      = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._build_toolbar(layout)
        self._build_chart_area(layout)

    def _build_toolbar(self, parent_layout: QVBoxLayout) -> None:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"background:{BG_COLOR};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 2, 6, 2)
        h.setSpacing(4)

        lbl = QLabel("Timeframe:")
        lbl.setStyleSheet("color:#aaaacc;font-size:11px;")
        h.addWidget(lbl)

        self._tf_btn_group = QButtonGroup(bar)
        self._tf_btn_group.setExclusive(True)
        self._tf_buttons: dict[str, QPushButton] = {}
        for tf in TIMEFRAME_MAP:
            btn = QPushButton(tf)
            btn.setCheckable(True)
            btn.setFixedWidth(38)
            btn.setStyleSheet(self._tf_style(tf == "5m"))
            btn.clicked.connect(lambda _, t=tf: self._change_timeframe(t))
            self._tf_btn_group.addButton(btn)
            h.addWidget(btn)
            self._tf_buttons[tf] = btn
        self._tf_buttons["5m"].setChecked(True)
        self._tf_btn_group.buttonClicked.connect(self._on_tf_btn_clicked)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color:#444;")
        h.addWidget(sep)

        self._ind_btn = QPushButton("Indicators ▼")
        self._ind_btn.setStyleSheet(
            "QPushButton{background:#2a2a4a;color:#aaaacc;border:1px solid #444;"
            "border-radius:3px;padding:2px 8px;font-size:11px;}"
            "QPushButton:hover{background:#3a3a5a;}"
        )
        self._ind_btn.clicked.connect(self._show_indicator_menu)
        h.addWidget(self._ind_btn)

        h.addStretch()

        self._title_lbl = QLabel(f"{self._symbol}  5m")
        self._title_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._title_lbl.setStyleSheet(
            "color:#ffffff;font-size:12px;font-weight:bold;"
        )
        h.addWidget(self._title_lbl)
        parent_layout.addWidget(bar)

    @staticmethod
    def _tf_style(active: bool) -> str:
        if active:
            return (
                "QPushButton {"
                "  background-color: #26a69a;"
                "  color: #ffffff;"
                "  border: none;"
                "  border-radius: 3px;"
                "  font-size: 11px;"
                "  font-weight: bold;"
                "}"
                "QPushButton:checked {"
                "  background-color: #26a69a;"
                "  color: #ffffff;"
                "}"
                "QPushButton:pressed {"
                "  background-color: #1d8a80;"
                "  color: #ffffff;"
                "}"
            )
        return (
            "QPushButton {"
            "  background-color: #2a2a4a;"
            "  color: #e0e0e0;"
            "  border: 1px solid #555555;"
            "  border-radius: 3px;"
            "  font-size: 11px;"
            "}"
            "QPushButton:checked {"
            "  background-color: #26a69a;"
            "  color: #ffffff;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            "  background-color: #3a3a5a;"
            "  color: #ffffff;"
            "}"
        )

    def _on_tf_btn_clicked(self, btn: QPushButton) -> None:
        for b in self._tf_buttons.values():
            b.setStyleSheet(self._tf_style(b is btn))

    def _build_chart_area(self, parent_layout: QVBoxLayout) -> None:
        pg.setConfigOption("background", BG_COLOR)
        pg.setConfigOption("foreground", "#aaaacc")

        self._date_axis = DateAxisItem([], orientation="bottom")
        self._glw = pg.GraphicsLayoutWidget()
        self._glw.setBackground(BG_COLOR)

        # Row 0 — Price
        self._price_plot = self._glw.addPlot(
            row=0, col=0, axisItems={"bottom": self._date_axis}
        )
        self._price_plot.showGrid(x=True, y=True, alpha=0.25)
        self._price_plot.showAxis("right")
        self._price_plot.hideAxis("left")
        self._price_plot.getAxis("right").setStyle(tickTextOffset=4)
        self._price_plot.getAxis("bottom").setStyle(tickTextOffset=4)
        self._price_plot.setMenuEnabled(False)

        cross_pen = pg.mkPen("#ffffff", width=0.5, style=_DASH)
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=cross_pen)
        self._hline = pg.InfiniteLine(angle=0,  movable=False, pen=cross_pen)
        self._price_plot.addItem(self._vline, ignoreBounds=True)
        self._price_plot.addItem(self._hline, ignoreBounds=True)

        # Row 1 — Volume
        self._vol_plot = self._glw.addPlot(row=1, col=0)
        self._vol_plot.setXLink(self._price_plot)
        self._vol_plot.showGrid(x=True, y=True, alpha=0.25)
        self._vol_plot.setLabel("left", "Vol", color="#aaaacc")
        self._vol_plot.setMenuEnabled(False)
        self._vol_plot.hideAxis("bottom")

        # Row 2 — RSI
        self._rsi_plot = self._glw.addPlot(row=2, col=0)
        self._rsi_plot.setXLink(self._price_plot)
        self._rsi_plot.showGrid(x=True, y=True, alpha=0.25)
        self._rsi_plot.setLabel("left", "RSI", color="#aaaacc")
        self._rsi_plot.setYRange(0, 100, padding=0)
        self._rsi_plot.setMenuEnabled(False)
        self._rsi_plot.hideAxis("bottom")
        self._add_href(self._rsi_plot, 40)
        self._add_href(self._rsi_plot, 60)

        # Row 3 — MACD
        self._macd_plot = self._glw.addPlot(row=3, col=0)
        self._macd_plot.setXLink(self._price_plot)
        self._macd_plot.showGrid(x=True, y=True, alpha=0.25)
        self._macd_plot.setLabel("left", "MACD", color="#aaaacc")
        self._macd_plot.setMenuEnabled(False)
        self._macd_plot.hideAxis("bottom")
        self._add_href(self._macd_plot, 0)

        # Row 4 — ADX
        self._adx_plot = self._glw.addPlot(row=4, col=0)
        self._adx_plot.setXLink(self._price_plot)
        self._adx_plot.showGrid(x=True, y=True, alpha=0.25)
        self._adx_plot.setLabel("left", "ADX", color="#aaaacc")
        self._adx_plot.setMenuEnabled(False)
        self._adx_plot.hideAxis("bottom")
        self._add_href(self._adx_plot, 25)

        ci = self._glw.ci.layout
        ci.setRowStretchFactor(0, 5)
        ci.setRowStretchFactor(1, 1)
        ci.setRowStretchFactor(2, 1)
        ci.setRowStretchFactor(3, 1)
        ci.setRowStretchFactor(4, 1)
        ci.setSpacing(2)

        self._price_plot.scene().sigMouseMoved.connect(self._on_mouse_move)

        self._tooltip = QLabel(self._glw.viewport())
        self._tooltip.setStyleSheet(
            "background:#2a2a4a;color:white;padding:4px;"
            "font-size:10px;font-family:monospace;border:1px solid #444;"
        )
        self._tooltip.hide()

        parent_layout.addWidget(self._glw, stretch=1)

    @staticmethod
    def _add_href(plot: pg.PlotItem, level: float) -> None:
        plot.addItem(
            pg.InfiniteLine(
                pos=level, angle=0,
                pen=pg.mkPen("#555555", style=_DASH),
            ),
            ignoreBounds=True,
        )

    # ------------------------------------------------------------------ #
    # Qt event overrides                                                   #
    # ------------------------------------------------------------------ #

    def showEvent(self, event):
        """Called by Qt when widget becomes visible. Safe to render now."""
        super().showEvent(event)
        if self._current_df is None:
            QTimer.singleShot(100, self._reload_chart)

    def resizeEvent(self, event):
        """Reload chart if it has never successfully drawn."""
        super().resizeEvent(event)
        if self._current_df is None and self.isVisible():
            QTimer.singleShot(50, self._reload_chart)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def on_new_candle(self, data: dict) -> None:
        if (
            data.get("symbol") == self._symbol
            and data.get("timeframe") == self._timeframe
        ):
            self._reload_chart()

    def update_live_price(self, data: dict) -> None:
        ltp = data.get("ltp", 0.0)
        live = data.get("live_candle", {})
        if live:
            self._forming_candle = live
            self._update_live_candle_only()
            open_ = live.get("open", ltp)
            pct = (ltp - open_) / open_ * 100 if open_ else 0.0
            sign = "+" if pct >= 0 else ""
            self._title_lbl.setText(
                f"{self._symbol}  {self._timeframe}  {ltp:,.2f}  ({sign}{pct:.2f}%)"
            )

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol
        self._signal_markers.clear()
        self._forming_candle = None
        self._clear_all_plots()
        self._reload_chart()

    def add_signal_marker(self, timestamp, direction: str, price: float) -> None:
        if self._current_df is None or self._current_x is None:
            return
        ts = (
            pd.Timestamp(timestamp, tz=IST)
            if not isinstance(timestamp, pd.Timestamp)
            else timestamp
        )
        try:
            idx = self._current_df.index.get_loc(ts)
        except KeyError:
            idx = min(
                self._current_df.index.searchsorted(ts),
                len(self._current_df) - 1,
            )
        is_ce = "CE" in direction
        marker = pg.ArrowItem(
            angle=90 if is_ce else -90,
            tipAngle=30, headLen=15,
            pen=pg.mkPen(COLOR_UP if is_ce else COLOR_DOWN),
            brush=pg.mkBrush(COLOR_UP if is_ce else COLOR_DOWN),
        )
        row = self._current_df.iloc[idx]
        y_pos = row["low"] * 0.9985 if is_ce else row["high"] * 1.0015
        marker.setPos(float(self._current_x[idx]), y_pos)
        self._price_plot.addItem(marker)
        self._signal_markers.append(marker)
        if len(self._signal_markers) > 10:
            self._price_plot.removeItem(self._signal_markers.pop(0))

    # ------------------------------------------------------------------ #
    # Internal — chart lifecycle                                           #
    # ------------------------------------------------------------------ #

    def _reload_chart(self) -> None:
        log.info("ChartPanel._reload_chart: symbol=%s tf=%s size=%s",
                 self._symbol, self._timeframe,
                 f"{self.width()}x{self.height()}")
        df = self._load_candles()
        if df is None or df.empty:
            return
        df = df.tail(CANDLES_TO_SHOW).copy()
        self._clear_all_plots()
        x = np.arange(len(df), dtype=float)
        self._current_df = df
        self._current_x = x
        self._date_axis.updateTimestamps(df.index.tolist())
        self._draw_candles(df, x)
        self._draw_indicators(df)
        self._draw_sr_zones()
        self._draw_trend_lines(df)
        self._apply_indicator_visibility()
        self._price_plot.autoRange()
        self._update_title(df)

    def _update_live_candle_only(self) -> None:
        if not self._forming_candle or self._current_df is None:
            return
        for item in self._live_candle_items:
            try:
                self._price_plot.removeItem(item)
            except Exception:
                pass
        self._live_candle_items = []

        fc = self._forming_candle
        xi = float(len(self._current_df))
        o  = float(fc.get("open",  0))
        h  = float(fc.get("high",  0))
        l  = float(fc.get("low",   0))
        c  = float(fc.get("close", 0))
        if o <= 0 or c <= 0:
            return

        is_bull = c >= o
        color   = COLOR_UP if is_bull else COLOR_DOWN
        pen     = pg.mkPen(color, width=0.5)

        body_bottom = min(o, c)
        body_height = max(abs(c - o), 0.1)
        body = pg.BarGraphItem(
            x=[xi], y0=[body_bottom], height=[body_height],
            width=0.6, brush=pg.mkBrush(color), pen=pen,
        )
        self._price_plot.addItem(body)
        self._live_candle_items.append(body)

        if h > max(o, c):
            uw = pg.PlotDataItem([xi, xi], [max(o, c), h], pen=pen)
            self._price_plot.addItem(uw)
            self._live_candle_items.append(uw)
        if l < min(o, c):
            lw = pg.PlotDataItem([xi, xi], [l, min(o, c)], pen=pen)
            self._price_plot.addItem(lw)
            self._live_candle_items.append(lw)

    def _clear_all_plots(self) -> None:
        for plot in (
            self._price_plot, self._vol_plot,
            self._rsi_plot, self._macd_plot, self._adx_plot,
        ):
            plot.clear()
        self._sr_lines.clear()
        self._trend_lines.clear()
        self._candle_items = []
        self._live_candle_items = []
        self._ema9_line = self._ema20_line = self._ema50_line = self._ema200_line = None
        self._vwap_line = None
        self._bb_upper_line = self._bb_lower_line = self._bb_mid_line = self._bb_fill = None
        self._st_bull_line = self._st_bear_line = None
        self._vol_bars = None
        self._rsi_line = None
        self._macd_line_item = self._macd_signal_item = self._macd_hist_bars = None
        self._adx_line = None
        # Re-add persistent items removed by clear()
        self._price_plot.addItem(self._vline, ignoreBounds=True)
        self._price_plot.addItem(self._hline, ignoreBounds=True)
        self._add_href(self._rsi_plot, 40)
        self._add_href(self._rsi_plot, 60)
        self._add_href(self._macd_plot, 0)
        self._add_href(self._adx_plot, 25)

    def _change_timeframe(self, tf: str) -> None:
        self._timeframe = tf
        self._forming_candle = None
        self._reload_chart()

    def _update_title(self, df: pd.DataFrame) -> None:
        last_c = df["close"].iloc[-1]
        prev_c = df["close"].iloc[-2] if len(df) > 1 else last_c
        pct = (last_c - prev_c) / prev_c * 100 if prev_c else 0.0
        sign = "+" if pct >= 0 else ""
        self._title_lbl.setText(
            f"{self._symbol}  {self._timeframe}  {last_c:,.2f}  ({sign}{pct:.2f}%)"
        )

    # ------------------------------------------------------------------ #
    # Drawing                                                              #
    # ------------------------------------------------------------------ #

    def _draw_candles(self, df: pd.DataFrame, x) -> None:
        """Draw candlesticks using BarGraphItem (bodies) + PlotDataItem (wicks)."""
        opens  = df["open"].values.astype(float)
        closes = df["close"].values.astype(float)
        highs  = df["high"].values.astype(float)
        lows   = df["low"].values.astype(float)

        bull_mask = closes >= opens
        bear_mask = ~bull_mask

        if bull_mask.any():
            bull_x      = x[bull_mask].astype(float)
            bull_bottom = opens[bull_mask]
            bull_height = closes[bull_mask] - opens[bull_mask]
            bull_height = np.where(bull_height == 0, 0.1, bull_height)
            bull_bars = pg.BarGraphItem(
                x=bull_x, y0=bull_bottom, height=bull_height,
                width=0.6,
                brush=pg.mkBrush(COLOR_UP),
                pen=pg.mkPen(COLOR_UP, width=0.5),
            )
            self._price_plot.addItem(bull_bars)
            self._candle_items.append(bull_bars)

        if bear_mask.any():
            bear_x      = x[bear_mask].astype(float)
            bear_bottom = closes[bear_mask]
            bear_height = opens[bear_mask] - closes[bear_mask]
            bear_height = np.where(bear_height == 0, 0.1, bear_height)
            bear_bars = pg.BarGraphItem(
                x=bear_x, y0=bear_bottom, height=bear_height,
                width=0.6,
                brush=pg.mkBrush(COLOR_DOWN),
                pen=pg.mkPen(COLOR_DOWN, width=0.5),
            )
            self._price_plot.addItem(bear_bars)
            self._candle_items.append(bear_bars)

        for i, xi in enumerate(x):
            body_top    = max(opens[i], closes[i])
            body_bottom = min(opens[i], closes[i])
            color = COLOR_UP if bull_mask[i] else COLOR_DOWN
            pen   = pg.mkPen(color, width=1)
            if highs[i] > body_top:
                upper = pg.PlotDataItem(
                    [float(xi), float(xi)], [body_top, highs[i]], pen=pen
                )
                self._price_plot.addItem(upper)
                self._candle_items.append(upper)
            if lows[i] < body_bottom:
                lower = pg.PlotDataItem(
                    [float(xi), float(xi)], [body_bottom, lows[i]], pen=pen
                )
                self._price_plot.addItem(lower)
                self._candle_items.append(lower)

        self._live_candle_items = []

    def _draw_indicators(self, df: pd.DataFrame) -> None:
        x = self._current_x

        def _arr(col: str) -> Optional[np.ndarray]:
            if col in df.columns:
                return df[col].values.astype(float)
            return None

        # ── Price-panel overlays ──────────────────────────────────────
        v = _arr("ema_9")
        if v is not None:
            self._ema9_line = self._price_plot.plot(
                x, v, pen=pg.mkPen("#B0BEC5", width=0.8), connect="finite"
            )
        v = _arr("ema_20")
        if v is not None:
            self._ema20_line = self._price_plot.plot(
                x, v, pen=pg.mkPen(COLOR_EMA20, width=1), connect="finite"
            )
        v = _arr("ema_50")
        if v is not None:
            self._ema50_line = self._price_plot.plot(
                x, v, pen=pg.mkPen(COLOR_EMA50, width=1), connect="finite"
            )
        v = _arr("ema_200")
        if v is not None:
            self._ema200_line = self._price_plot.plot(
                x, v, pen=pg.mkPen(COLOR_EMA200, width=1.5), connect="finite"
            )
        v = _arr("vwap")
        if v is not None:
            self._vwap_line = self._price_plot.plot(
                x, v,
                pen=pg.mkPen(COLOR_VWAP, width=1, style=_DASH),
                connect="finite",
            )

        bb_u = _arr("bb_upper")
        bb_l = _arr("bb_lower")
        bb_m = _arr("bb_mid")
        if bb_u is not None and bb_l is not None:
            self._bb_upper_line = self._price_plot.plot(
                x, bb_u, pen=pg.mkPen(COLOR_BB_UP, width=0.8), connect="finite"
            )
            self._bb_lower_line = self._price_plot.plot(
                x, bb_l, pen=pg.mkPen(COLOR_BB_LOW, width=0.8), connect="finite"
            )
            if bb_m is not None:
                self._bb_mid_line = self._price_plot.plot(
                    x, bb_m,
                    pen=pg.mkPen(COLOR_BB_MID, width=0.8, style=_DASH),
                    connect="finite",
                )
            self._bb_fill = pg.FillBetweenItem(
                self._bb_upper_line, self._bb_lower_line,
                brush=pg.mkBrush(96, 125, 139, 30),
            )
            self._price_plot.addItem(self._bb_fill)

        st_val = _arr("supertrend_val")
        st_dir = _arr("supertrend_dir")
        if st_val is not None and st_dir is not None:
            bull_y = np.where(st_dir > 0, st_val, np.nan)
            bear_y = np.where(st_dir <= 0, st_val, np.nan)
            self._st_bull_line = self._price_plot.plot(
                x, bull_y, pen=pg.mkPen(COLOR_ST_BULL, width=1.5), connect="finite"
            )
            self._st_bear_line = self._price_plot.plot(
                x, bear_y, pen=pg.mkPen(COLOR_ST_BEAR, width=1.5), connect="finite"
            )

        # ── Volume ───────────────────────────────────────────────────
        opens  = df["open"].values.astype(float)
        closes = df["close"].values.astype(float)
        vol    = df["volume"].values.astype(float)
        vol_brushes = [
            pg.mkBrush(COLOR_VOL_UP if c >= o else COLOR_VOL_DOWN)
            for o, c in zip(opens, closes)
        ]
        self._vol_bars = pg.BarGraphItem(
            x=x, height=vol, width=0.6, brushes=vol_brushes
        )
        self._vol_plot.addItem(self._vol_bars)

        # ── RSI ──────────────────────────────────────────────────────
        v = _arr("rsi_14")
        if v is not None:
            self._rsi_line = self._rsi_plot.plot(
                x, v, pen=pg.mkPen(COLOR_RSI, width=1), connect="finite"
            )

        # ── MACD ─────────────────────────────────────────────────────
        macd_l = _arr("macd_line")
        macd_s = _arr("macd_signal")
        macd_h = _arr("macd_hist")
        if macd_l is not None:
            self._macd_line_item = self._macd_plot.plot(
                x, macd_l, pen=pg.mkPen(COLOR_MACD_L, width=1), connect="finite"
            )
        if macd_s is not None:
            self._macd_signal_item = self._macd_plot.plot(
                x, macd_s, pen=pg.mkPen(COLOR_MACD_S, width=1), connect="finite"
            )
        if macd_h is not None:
            hist = np.nan_to_num(macd_h, nan=0.0)
            hist_brushes = [
                pg.mkBrush(COLOR_MACD_H_POS if h >= 0 else COLOR_MACD_H_NEG)
                for h in hist
            ]
            self._macd_hist_bars = pg.BarGraphItem(
                x=x, height=hist, width=0.6, brushes=hist_brushes
            )
            self._macd_plot.addItem(self._macd_hist_bars)

        # ── ADX ──────────────────────────────────────────────────────
        v = _arr("adx_14")
        if v is not None:
            self._adx_line = self._adx_plot.plot(
                x, v, pen=pg.mkPen(COLOR_ADX, width=1), connect="finite"
            )

    def _draw_sr_zones(self) -> None:
        for zone in self._load_sr_zones():
            strength = zone.get("strength", "MODERATE")
            price = zone.get("price_mid", 0.0)
            is_strong = "STRONG" in strength
            line = pg.InfiniteLine(
                pos=price, angle=0, movable=False,
                pen=pg.mkPen(
                    COLOR_SR_STR if is_strong else COLOR_SR_MOD,
                    width=1,
                    style=_SOLID if is_strong else _DASH,
                ),
            )
            label = pg.TextItem(
                text=strength[:3],
                color=COLOR_SR_STR if is_strong else COLOR_SR_MOD,
                anchor=(0, 1),
            )
            label.setPos(0, price)
            self._price_plot.addItem(line, ignoreBounds=True)
            self._price_plot.addItem(label)
            self._sr_lines.append((line, label))

    def _draw_trend_lines(self, df: pd.DataFrame) -> None:
        if len(df) < 12:
            return
        x = self._current_x
        highs = df["high"].values.astype(float)
        lows  = df["low"].values.astype(float)
        window = 5
        swing_highs: list[tuple] = []
        swing_lows:  list[tuple] = []
        for i in range(window, len(df) - window):
            if highs[i] == highs[i - window: i + window + 1].max():
                swing_highs.append((x[i], highs[i]))
            if lows[i] == lows[i - window: i + window + 1].min():
                swing_lows.append((x[i], lows[i]))

        trend_pen = pg.mkPen(COLOR_TREND, width=1, style=_DASH)
        for swings in (swing_highs, swing_lows):
            if len(swings) >= 2:
                p1, p2 = swings[-2], swings[-1]
                line = self._price_plot.plot(
                    [p1[0], p2[0]], [p1[1], p2[1]], pen=trend_pen
                )
                self._trend_lines.append(line)

    # ------------------------------------------------------------------ #
    # Interaction                                                          #
    # ------------------------------------------------------------------ #

    def _on_mouse_move(self, pos) -> None:
        if self._current_df is None:
            return
        if not self._price_plot.sceneBoundingRect().contains(pos):
            self._tooltip.hide()
            return

        mp = self._price_plot.vb.mapSceneToView(pos)
        self._vline.setPos(mp.x())
        self._hline.setPos(mp.y())

        idx = int(round(mp.x()))
        n = len(self._current_df)
        if 0 <= idx < n:
            row = self._current_df.iloc[idx]
            ts  = self._current_df.index[idx]
            self._tooltip.setText(
                f"{ts.strftime('%Y-%m-%d  %H:%M')}\n"
                f"O: {row['open']:>10,.2f}   H: {row['high']:>10,.2f}\n"
                f"L: {row['low']:>10,.2f}   C: {row['close']:>10,.2f}\n"
                f"Vol: {row['volume']:>12,.0f}"
            )
            self._tooltip.adjustSize()
            vp = self._glw.mapFromScene(pos)
            tx, ty = vp.x() + 15, vp.y() - 10
            tw, th = self._tooltip.width(), self._tooltip.height()
            if tx + tw > self._glw.width() - 10:
                tx = vp.x() - tw - 15
            ty = max(0, min(ty, self._glw.height() - th - 4))
            self._tooltip.move(int(tx), int(ty))
            self._tooltip.show()
        else:
            self._tooltip.hide()

    def _show_indicator_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#2a2a4a;color:#aaaacc;border:1px solid #444;}"
            "QMenu::item:checked{color:#26a69a;}"
            "QMenu::item:selected{background:#3a3a5a;}"
        )
        for key, lbl_text in _INDICATOR_LABELS.items():
            act = QAction(lbl_text, menu)
            act.setCheckable(True)
            act.setChecked(self._active_indicators.get(key, False))
            act.triggered.connect(
                lambda checked, k=key: self._toggle_indicator(k, checked)
            )
            menu.addAction(act)
        btn = self._ind_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _toggle_indicator(self, key: str, checked: bool) -> None:
        self._active_indicators[key] = checked
        self._apply_indicator_visibility()

    def _apply_indicator_visibility(self) -> None:
        act = self._active_indicators

        def _vis(item, flag: bool) -> None:
            if item is not None:
                item.setVisible(flag)

        _vis(self._ema9_line,   act.get("ema_9",   False))
        _vis(self._ema20_line,  act.get("ema_20",  True))
        _vis(self._ema50_line,  act.get("ema_50",  True))
        _vis(self._ema200_line, act.get("ema_200", True))
        _vis(self._vwap_line,   act.get("vwap",    True))

        bb_on = act.get("bb", True)
        for item in (
            self._bb_upper_line, self._bb_lower_line,
            self._bb_mid_line,   self._bb_fill,
        ):
            _vis(item, bb_on)

        st_on = act.get("supertrend", True)
        _vis(self._st_bull_line, st_on)
        _vis(self._st_bear_line, st_on)

        ci = self._glw.ci.layout

        vol_on = act.get("volume", True)
        self._vol_plot.setVisible(vol_on)
        ci.setRowStretchFactor(1, 1 if vol_on else 0)

        rsi_on = act.get("rsi", True)
        self._rsi_plot.setVisible(rsi_on)
        ci.setRowStretchFactor(2, 1 if rsi_on else 0)

        macd_on = act.get("macd", True)
        self._macd_plot.setVisible(macd_on)
        ci.setRowStretchFactor(3, 1 if macd_on else 0)

        adx_on = act.get("adx", True)
        self._adx_plot.setVisible(adx_on)
        ci.setRowStretchFactor(4, 1 if adx_on else 0)

    # ------------------------------------------------------------------ #
    # DB helpers                                                           #
    # ------------------------------------------------------------------ #

    def _load_candles(self) -> Optional[pd.DataFrame]:
        try:
            from core.local_db import get_connection
            conn = get_connection()
            rows = conn.execute(
                """
                SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume,
                       i.ema_20, i.ema_50, i.ema_200,
                       i.vwap, i.bb_upper, i.bb_lower, i.bb_mid, i.bb_width,
                       i.supertrend_dir, i.supertrend_val,
                       i.rsi_14, i.macd_line, i.macd_signal, i.macd_hist,
                       i.adx_14, i.atr_14,
                       i.volume_sma_20, i.volume_ratio
                FROM candles c
                LEFT JOIN indicators i
                  ON c.symbol    = i.symbol
                 AND c.timeframe = i.timeframe
                 AND c.timestamp = i.timestamp
                WHERE c.symbol   = ?
                  AND c.timeframe = ?
                ORDER BY c.timestamp DESC
                LIMIT ?
                """,
                (self._symbol, self._timeframe, CANDLES_TO_SHOW),
            ).fetchall()

            if not rows:
                return None

            rows = list(reversed(rows))
            df = pd.DataFrame(rows, columns=[
                "timestamp", "open", "high", "low", "close", "volume",
                "ema_20", "ema_50", "ema_200",
                "vwap", "bb_upper", "bb_lower", "bb_mid", "bb_width",
                "supertrend_dir", "supertrend_val",
                "rsi_14", "macd_line", "macd_signal", "macd_hist",
                "adx_14", "atr_14",
                "volume_sma_20", "volume_ratio",
            ])

            if df["timestamp"].dtype == object:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            else:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)

            df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
            df.set_index("timestamp", inplace=True)
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = df[col].astype(float)
            return df

        except Exception as exc:
            log.warning("ChartPanel._load_candles: %s", exc)
            return None

    def _load_sr_zones(self) -> list[dict]:
        try:
            from core.local_db import get_connection
            conn = get_connection()
            rows = conn.execute(
                "SELECT price_mid, strength FROM support_resistance "
                "WHERE symbol = ? AND active = 1 LIMIT 20",
                (self._symbol,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.debug("ChartPanel._load_sr_zones: %s", exc)
            return []
