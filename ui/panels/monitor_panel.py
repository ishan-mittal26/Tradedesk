"""
Monitor panel for TradeDesk.

Thread: MAIN THREAD only. All methods called from poll_queue().

Responsibilities:
  - Live price ticker per active instrument.
  - Alert Score badge (0-21) with colour coding.
  - Scrollable alert card list (max 20, newest first).
  - WebSocket connection status display.
  - Per-instrument cooldown indicator.
  - Expiry warning cards.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor

log = logging.getLogger(__name__)

MAX_ALERT_CARDS = 20

LEVEL_COLORS = {
    "SOFT":   "#FF9800",
    "STRONG": "#FF6D00",
    "MAX":    "#F44336",
}


class MonitorPanel(QWidget):
    """Left panel: live prices, alert score, alert cards, WS status."""

    def __init__(self, parent, shared_queue):
        super().__init__(parent)
        self._shared_queue = shared_queue
        self._alert_cards: list[QFrame] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ── ROW 1: top bar ─────────────────────────────────────────────
        top_bar = QFrame()
        top_bar.setFixedHeight(36)
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(6, 0, 6, 0)
        top_bar_layout.setSpacing(8)

        self._price_lbl = QLabel("— ···")
        price_font = QFont()
        price_font.setBold(True)
        price_font.setPointSize(13)
        self._price_lbl.setFont(price_font)
        top_bar_layout.addWidget(self._price_lbl)

        self._alert_score_lbl = QLabel("Alert: —/21")
        score_font = QFont()
        score_font.setPointSize(10)
        self._alert_score_lbl.setFont(score_font)
        top_bar_layout.addWidget(self._alert_score_lbl)

        top_bar_layout.addStretch()

        self._cooldown_lbl = QLabel("")
        cooldown_font = QFont()
        cooldown_font.setPointSize(8)
        self._cooldown_lbl.setFont(cooldown_font)
        self._cooldown_lbl.setStyleSheet("color: #FF9800;")
        top_bar_layout.addWidget(self._cooldown_lbl)

        self._ws_lbl = QLabel("WS: Connecting…")
        ws_font = QFont()
        ws_font.setPointSize(9)
        self._ws_lbl.setFont(ws_font)
        self._ws_lbl.setStyleSheet("color: #607D8B;")
        top_bar_layout.addWidget(self._ws_lbl)

        main_layout.addWidget(top_bar)

        # ── ROW 2: separator ───────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(sep)

        # ── ROW 3: alerts header ────────────────────────────────────────
        alerts_hdr = QFrame()
        alerts_hdr.setFixedHeight(24)
        hdr_layout = QHBoxLayout(alerts_hdr)
        hdr_layout.setContentsMargins(6, 0, 6, 0)
        hdr_layout.setSpacing(0)

        alerts_title = QLabel("ALERTS")
        alerts_title_font = QFont()
        alerts_title_font.setBold(True)
        alerts_title_font.setPointSize(8)
        alerts_title.setFont(alerts_title_font)
        alerts_title.setStyleSheet("color: #607D8B;")
        hdr_layout.addWidget(alerts_title)

        hdr_layout.addStretch()

        self._card_count_lbl = QLabel("0 alerts")
        count_font = QFont()
        count_font.setPointSize(8)
        self._card_count_lbl.setFont(count_font)
        self._card_count_lbl.setStyleSheet("color: #607D8B;")
        hdr_layout.addWidget(self._card_count_lbl)

        main_layout.addWidget(alerts_hdr)

        # ── ROW 4: scrollable cards ─────────────────────────────────────
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.NoFrame)

        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setSpacing(2)
        self._cards_layout.setContentsMargins(4, 4, 4, 4)
        self._cards_layout.addStretch()

        self._scroll_area.setWidget(self._cards_widget)
        main_layout.addWidget(self._scroll_area)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_tick(self, data: dict) -> None:
        symbol  = data.get("symbol", "")
        ltp     = data.get("ltp", data.get("close", 0.0))
        change  = data.get("change", 0.0)
        chg_pct = data.get("change_pct", 0.0)
        arrow   = "▲" if change >= 0 else "▼"
        color   = "#26a69a" if change >= 0 else "#ef5350"
        self._price_lbl.setText(
            f"{symbol}  {ltp:,.2f}  {arrow} {change:+.2f} ({chg_pct:+.2f}%)"
        )
        self._price_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    def display_alert_card(self, data: dict) -> None:
        card = self._create_alert_card(data)
        # insertWidget(0, ...) inserts before the stretch item at index 0;
        # the stretch was added last so it sits at index count-1 — insert at 0
        # places new cards at the visual top.
        self._cards_layout.insertWidget(0, card)
        self._alert_cards.insert(0, card)
        self._prune_old_cards()
        self._card_count_lbl.setText(f"{len(self._alert_cards)} alert(s)")
        self.update_alert_score(data.get("alert_score", 0))
        self._scroll_area.verticalScrollBar().setValue(0)

    def update_alert_score(self, score: int) -> None:
        if score >= 18:
            level, color = "MAX", "#F44336"
        elif score >= 14:
            level, color = "STRONG", "#FF6D00"
        elif score >= 10:
            level, color = "SOFT", "#FF9800"
        else:
            level, color = "—", "#607D8B"
        self._alert_score_lbl.setText(f"Alert: {score}/21  {level}")
        self._alert_score_lbl.setStyleSheet(
            f"color: {color}; font-weight: bold; padding: 2px 6px;"
        )

    def display_expiry_alert(self, data: dict) -> None:
        card_data = {
            "symbol":        data.get("symbol", ""),
            "alert_level":   "SOFT",
            "alert_score":   0,
            "direction":     "NEUTRAL",
            "timestamp":     data.get("timestamp", ""),
            "conditions_met": [
                f"DTE: {data.get('dte', '?')} — {data.get('message', 'Expiry alert')}"
            ],
            "vix_adjusted":    False,
            "vix_level":       "NORMAL",
            "cooldown_active": False,
            "bb_squeeze":      False,
            "suppress_mode":   False,
        }
        self.display_alert_card(card_data)

    def update_ws_status(self, data: dict) -> None:
        status = data.get("status", "")
        if status == "CONNECTED":
            self._ws_lbl.setText("WS: CONNECTED ●")
            self._ws_lbl.setStyleSheet("color: #4CAF50;")
        elif status in ("DISCONNECTED", "RECONNECTING"):
            attempt = data.get("attempt", "")
            self._ws_lbl.setText(f"WS: RECONNECTING ({attempt}/5)…")
            self._ws_lbl.setStyleSheet("color: #FF9800;")
        elif status == "FAILED":
            self._ws_lbl.setText("WS: FAILED — restart app")
            self._ws_lbl.setStyleSheet("color: #F44336;")
        else:
            self._ws_lbl.setText(f"WS: {status}")
            self._ws_lbl.setStyleSheet("color: #607D8B;")

    def set_cooldown(self, symbol: str, minutes_remaining: int) -> None:
        if minutes_remaining > 0:
            self._cooldown_lbl.setText(f"⏸ {symbol} cooldown {minutes_remaining}m")
        else:
            self._cooldown_lbl.setText("")

    # ------------------------------------------------------------------
    # Card factory
    # ------------------------------------------------------------------

    def _create_alert_card(self, card: dict) -> QFrame:
        level      = card.get("alert_level", "SOFT")
        symbol     = card.get("symbol", "")
        score      = card.get("alert_score", 0)
        direction  = card.get("direction", "NEUTRAL")
        conditions = card.get("conditions_met", [])
        ts_raw     = card.get("timestamp", "")
        vix_adj    = card.get("vix_adjusted", False)
        bb_squeeze = card.get("bb_squeeze", False)
        border_color = LEVEL_COLORS.get(level, "#607D8B")

        outer = QFrame()
        outer.setFrameShape(QFrame.StyledPanel)
        outer.setStyleSheet(
            f"QFrame {{ border-left: 4px solid {border_color};"
            f"background: #1e2a3a; margin: 1px; padding: 4px; }}"
        )
        layout = QVBoxLayout(outer)
        layout.setSpacing(2)
        layout.setContentsMargins(6, 4, 6, 4)

        # Row 1: symbol + level + direction + time
        row1 = QHBoxLayout()

        sym_lbl = QLabel(symbol)
        sym_lbl.setStyleSheet("font-weight: bold; color: #ECEFF1;")
        row1.addWidget(sym_lbl)

        level_lbl = QLabel(f"{level} {score}/21")
        level_lbl.setStyleSheet(
            f"color: {border_color}; font-weight: bold; font-size: 9px;"
        )
        row1.addWidget(level_lbl)

        dir_color = (
            "#26a69a" if direction == "BULLISH"
            else ("#ef5350" if direction == "BEARISH" else "#607D8B")
        )
        dir_lbl = QLabel(direction)
        dir_lbl.setStyleSheet(f"color: {dir_color}; font-size: 9px;")
        row1.addWidget(dir_lbl)

        row1.addStretch()

        try:
            dt = datetime.fromisoformat(ts_raw)
            ts_str = dt.strftime("%H:%M")
        except Exception:
            ts_str = ts_raw[:5] if ts_raw else "--:--"
        ts_lbl = QLabel(ts_str)
        ts_lbl.setStyleSheet("color: #607D8B; font-size: 8px;")
        row1.addWidget(ts_lbl)

        layout.addLayout(row1)

        # Row 2: conditions (first 2)
        if conditions:
            cond_text = "  •  ".join(conditions[:2])
            if len(conditions) > 2:
                cond_text += f"  +{len(conditions)-2} more"
            cond_lbl = QLabel(cond_text)
            cond_lbl.setStyleSheet("color: #90A4AE; font-size: 7px;")
            cond_lbl.setWordWrap(True)
            layout.addWidget(cond_lbl)

        # Row 3: badges
        badge_row = QHBoxLayout()
        if vix_adj:
            b = QLabel("VIX-adj")
            b.setStyleSheet("color: #29B6F6; font-size: 7px;")
            badge_row.addWidget(b)
        if bb_squeeze:
            b = QLabel("BB SQUEEZE")
            b.setStyleSheet("color: #F44336; font-weight: bold; font-size: 7px;")
            badge_row.addWidget(b)
        badge_row.addStretch()
        layout.addLayout(badge_row)

        return outer

    def _prune_old_cards(self) -> None:
        while len(self._alert_cards) > MAX_ALERT_CARDS:
            oldest = self._alert_cards.pop()
            self._cards_layout.removeWidget(oldest)
            oldest.deleteLater()
