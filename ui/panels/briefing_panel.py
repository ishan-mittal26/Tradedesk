"""
Briefing panel for TradeDesk.

Thread: MAIN THREAD only.

Responsibilities:
  - Display morning briefing from Agent 4 (News + FII).
  - GIFT Nifty, US markets, India VIX, FII action, Sentiment badges.
  - Key news headlines with HIGH/MEDIUM/LOW impact colouring.
  - Three things to watch.
  - Full briefing text in scrollable QTextEdit.
"""

from __future__ import annotations

import logging

import pytz
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTextEdit,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

IMPACT_COLORS = {"HIGH": "#F44336", "MEDIUM": "#FF9800", "LOW": "#607D8B"}
IMPACT_ICONS  = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}


def _badge_group(title: str) -> tuple[QFrame, QLabel]:
    """Return (frame, value_label) for a single indicator badge."""
    frame = QFrame()
    frame.setFrameShape(QFrame.Box)
    frame.setStyleSheet("border: 1px solid #333; padding: 2px;")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(6, 3, 6, 3)
    layout.setSpacing(0)

    title_lbl = QLabel(title)
    t_font = QFont()
    t_font.setPointSize(7)
    title_lbl.setFont(t_font)
    title_lbl.setStyleSheet("color: #607D8B; border: none;")
    layout.addWidget(title_lbl)

    val_lbl = QLabel("—")
    v_font = QFont()
    v_font.setBold(True)
    v_font.setPointSize(10)
    val_lbl.setFont(v_font)
    val_lbl.setStyleSheet("color: #607D8B; font-weight: bold; border: none;")
    layout.addWidget(val_lbl)

    return frame, val_lbl


class BriefingPanel(QWidget):
    """Morning briefing panel from Agent 4."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # ── HEADER row ─────────────────────────────────────────────────
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(0)

        hdr_title = QLabel("MORNING BRIEFING")
        hdr_title_font = QFont()
        hdr_title_font.setBold(True)
        hdr_title_font.setPointSize(9)
        hdr_title.setFont(hdr_title_font)
        hdr_layout.addWidget(hdr_title)

        hdr_layout.addStretch()

        self._time_lbl = QLabel("Waiting for briefing…")
        time_font = QFont()
        time_font.setPointSize(8)
        self._time_lbl.setFont(time_font)
        self._time_lbl.setStyleSheet("color: #607D8B;")
        self._time_lbl.setAlignment(Qt.AlignRight)
        hdr_layout.addWidget(self._time_lbl)

        main_layout.addLayout(hdr_layout)

        # ── INDICATOR BADGES row ────────────────────────────────────────
        badges_layout = QHBoxLayout()
        badges_layout.setSpacing(4)

        gift_frame, self._gift_lbl     = _badge_group("GIFT Nifty")
        us_frame,   self._us_lbl       = _badge_group("US Markets")
        vix_frame,  self._vix_lbl      = _badge_group("India VIX")
        fii_frame,  self._fii_lbl      = _badge_group("FII Action")
        sent_frame, self._sentiment_lbl = _badge_group("Sentiment")

        for frame in (gift_frame, us_frame, vix_frame, fii_frame, sent_frame):
            badges_layout.addWidget(frame)

        badges_layout.addStretch()
        main_layout.addLayout(badges_layout)

        # ── Separator ───────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(sep1)

        # ── Key News ────────────────────────────────────────────────────
        news_title = QLabel("Key News")
        news_title_font = QFont()
        news_title_font.setBold(True)
        news_title_font.setPointSize(8)
        news_title.setFont(news_title_font)
        news_title.setStyleSheet("color: #90A4AE;")
        main_layout.addWidget(news_title)

        self._news_container = QWidget()
        self._news_layout = QVBoxLayout(self._news_container)
        self._news_layout.setSpacing(2)
        self._news_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._news_container)

        # ── Three Things to Watch ───────────────────────────────────────
        watch_title = QLabel("Three Things to Watch")
        watch_title_font = QFont()
        watch_title_font.setBold(True)
        watch_title_font.setPointSize(8)
        watch_title.setFont(watch_title_font)
        watch_title.setStyleSheet("color: #90A4AE;")
        main_layout.addWidget(watch_title)

        self._watch_container = QWidget()
        self._watch_layout = QVBoxLayout(self._watch_container)
        self._watch_layout.setSpacing(2)
        self._watch_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._watch_container)

        # ── Separator ───────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(sep2)

        # ── Full Briefing ───────────────────────────────────────────────
        briefing_title = QLabel("Full Briefing")
        briefing_title_font = QFont()
        briefing_title_font.setBold(True)
        briefing_title_font.setPointSize(8)
        briefing_title.setFont(briefing_title_font)
        briefing_title.setStyleSheet("color: #90A4AE;")
        main_layout.addWidget(briefing_title)

        self._briefing_text = QTextEdit()
        self._briefing_text.setReadOnly(True)
        self._briefing_text.setMaximumHeight(120)
        briefing_font = QFont()
        briefing_font.setPointSize(8)
        self._briefing_text.setFont(briefing_font)
        main_layout.addWidget(self._briefing_text)

        main_layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_briefing(self, data: dict) -> None:
        if not data:
            return

        from datetime import datetime
        import pytz
        now_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%I:%M %p")
        self._time_lbl.setText(f"Updated: {now_str}")

        # GIFT Nifty
        gift_pts = data.get("gift_nifty_pts", 0)
        gift_dir = data.get("gift_nifty_direction", "FLAT")
        arrow = "▲" if gift_dir == "UP" else ("▼" if gift_dir == "DOWN" else "—")
        color = "#26a69a" if gift_dir == "UP" else ("#ef5350" if gift_dir == "DOWN" else "#607D8B")
        self._gift_lbl.setText(f"{arrow} {gift_pts:+.0f}" if gift_pts else "—")
        self._gift_lbl.setStyleSheet(f"color: {color}; font-weight: bold; border: none;")

        # US Markets
        us = data.get("us_market", "MIXED")
        color = "#26a69a" if us == "POSITIVE" else ("#ef5350" if us == "NEGATIVE" else "#607D8B")
        self._us_lbl.setText(us)
        self._us_lbl.setStyleSheet(f"color: {color}; font-weight: bold; border: none;")

        # VIX
        vix_val = data.get("india_vix", 0)
        vix_label = data.get("vix_label", "")
        color_map = {"EXTREME": "#F44336", "HIGH": "#FF9800", "LOW": "#26a69a"}
        color = color_map.get(vix_label, "#607D8B")
        self._vix_lbl.setText(f"{vix_val:.1f}" if vix_val else "—")
        self._vix_lbl.setStyleSheet(f"color: {color}; font-weight: bold; border: none;")

        # FII
        fii_action = data.get("fii_action", "UNKNOWN")
        fii_amt = data.get("fii_amount_cr", None)
        color = "#26a69a" if fii_action == "BUYING" else ("#ef5350" if fii_action == "SELLING" else "#607D8B")
        fii_text = fii_action + (f" ₹{abs(fii_amt):,.0f}Cr" if fii_amt else "")
        self._fii_lbl.setText(fii_text)
        self._fii_lbl.setStyleSheet(f"color: {color}; font-weight: bold; border: none;")

        # Sentiment
        sentiment = data.get("overall_sentiment", "NEUTRAL")
        color = "#26a69a" if sentiment == "POSITIVE" else ("#ef5350" if sentiment == "NEGATIVE" else "#607D8B")
        sent_text = sentiment + (" ⚠HIGH IMPACT" if data.get("news_event_day") else "")
        self._sentiment_lbl.setText(sent_text)
        self._sentiment_lbl.setStyleSheet(f"color: {color}; font-weight: bold; border: none;")

        # Key news — clear and rebuild
        while self._news_layout.count():
            item = self._news_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for item in data.get("key_news", [])[:5]:
            impact = item.get("impact", "LOW")
            headline = item.get("headline", "")
            effect = item.get("effect", "")
            icon = IMPACT_ICONS.get(impact, "•")
            color = IMPACT_COLORS.get(impact, "#607D8B")

            row = QHBoxLayout()
            row.setSpacing(6)

            lbl = QLabel(f"{icon} {headline[:80]}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {color}; font-size: 8px;")
            row.addWidget(lbl)

            if effect:
                eff_lbl = QLabel(f"→ {effect[:30]}")
                eff_lbl.setStyleSheet("color: #607D8B; font-size: 7px;")
                row.addWidget(eff_lbl)

            container = QWidget()
            container.setLayout(row)
            self._news_layout.addWidget(container)

        # Three things — clear and rebuild
        while self._watch_layout.count():
            item = self._watch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, watch_item in enumerate(data.get("three_things_to_watch", [])[:3], 1):
            lbl = QLabel(f"{i}. {watch_item}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #ECEFF1; font-size: 8px;")
            self._watch_layout.addWidget(lbl)

        # Full briefing
        self._briefing_text.setPlainText(data.get("reasoning", ""))
