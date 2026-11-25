"""
Analysis panel for TradeDesk.

Thread: MAIN THREAD only. All methods called from poll_queue().

Responsibilities:
  - Display BOTH scores prominently (Alert Score 0-21 + Pipeline Signal Score 0-100).
  - Analyze button with 3 states gated by Signal Score.
  - Pulsing animation at score >= 85.
  - Agent 7 verdict display: verdict badge, behavior, position size, WHY text.
  - Compact Agent 1-6 summary grid.
  - Risk flags and conflict log.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytz
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QGridLayout, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

SCORE_STRONG = 85
SCORE_SIGNAL = 70
SCORE_WATCH  = 50


class AnalysisPanel(QWidget):
    """Right-side analysis panel: scores, Analyze button, agent outputs."""

    def __init__(self, parent, shared_queue, symbol, on_analyze_click=None):
        super().__init__(parent)
        self._shared_queue = shared_queue
        self._symbol = symbol
        self._on_analyze_click = on_analyze_click or (lambda: None)
        self._pipeline_score = 0
        self._alert_score = 0
        self._pulse_timer = QTimer()
        self._pulse_timer.setInterval(500)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_state = False
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # ── SECTION 1: Score header ─────────────────────────────────────
        score_hdr = QFrame()
        score_hdr_layout = QHBoxLayout(score_hdr)
        score_hdr_layout.setContentsMargins(0, 0, 0, 0)
        score_hdr_layout.setSpacing(8)

        # Alert score group
        alert_group = QFrame()
        alert_group.setFrameShape(QFrame.StyledPanel)
        alert_group_layout = QVBoxLayout(alert_group)
        alert_group_layout.setContentsMargins(6, 4, 6, 4)
        alert_group_layout.setSpacing(0)

        alert_title = QLabel("Alert Score")
        alert_title_font = QFont()
        alert_title_font.setPointSize(8)
        alert_title.setFont(alert_title_font)
        alert_title.setStyleSheet("color: #607D8B;")
        alert_group_layout.addWidget(alert_title)

        self._alert_score_lbl = QLabel("—/21")
        alert_score_font = QFont()
        alert_score_font.setBold(True)
        alert_score_font.setPointSize(14)
        self._alert_score_lbl.setFont(alert_score_font)
        self._alert_score_lbl.setStyleSheet("color: #607D8B; font-weight: bold; font-size: 14px;")
        alert_group_layout.addWidget(self._alert_score_lbl)

        self._alert_level_lbl = QLabel("SILENCE")
        alert_level_font = QFont()
        alert_level_font.setPointSize(8)
        self._alert_level_lbl.setFont(alert_level_font)
        self._alert_level_lbl.setStyleSheet("color: #607D8B;")
        alert_group_layout.addWidget(self._alert_level_lbl)

        score_hdr_layout.addWidget(alert_group)

        # Signal score group
        signal_group = QFrame()
        signal_group.setFrameShape(QFrame.StyledPanel)
        signal_group_layout = QVBoxLayout(signal_group)
        signal_group_layout.setContentsMargins(6, 4, 6, 4)
        signal_group_layout.setSpacing(0)

        signal_title = QLabel("Signal Score")
        signal_title_font = QFont()
        signal_title_font.setPointSize(8)
        signal_title.setFont(signal_title_font)
        signal_title.setStyleSheet("color: #607D8B;")
        signal_group_layout.addWidget(signal_title)

        self._signal_score_lbl = QLabel("—/100")
        signal_score_font = QFont()
        signal_score_font.setBold(True)
        signal_score_font.setPointSize(14)
        self._signal_score_lbl.setFont(signal_score_font)
        self._signal_score_lbl.setStyleSheet("color: #607D8B; font-weight: bold; font-size: 14px;")
        signal_group_layout.addWidget(self._signal_score_lbl)

        self._signal_level_lbl = QLabel("—")
        signal_level_font = QFont()
        signal_level_font.setPointSize(8)
        self._signal_level_lbl.setFont(signal_level_font)
        self._signal_level_lbl.setStyleSheet("color: #607D8B;")
        signal_group_layout.addWidget(self._signal_level_lbl)

        score_hdr_layout.addWidget(signal_group)

        # Analyze button
        self._analyze_btn = QPushButton("")
        self._analyze_btn.setFixedWidth(200)
        self._analyze_btn.setVisible(False)
        self._analyze_btn.clicked.connect(self._on_analyze)
        score_hdr_layout.addWidget(self._analyze_btn)

        score_hdr_layout.addStretch()
        main_layout.addWidget(score_hdr)

        # ── SECTION 2: Separator ────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(sep)

        # ── SECTION 3: Behavior row ─────────────────────────────────────
        behavior_row = QHBoxLayout()
        behavior_row.setContentsMargins(4, 0, 4, 0)

        self._behavior_lbl = QLabel("No behavior detected")
        beh_font = QFont()
        beh_font.setPointSize(9)
        self._behavior_lbl.setFont(beh_font)
        self._behavior_lbl.setStyleSheet("color: #607D8B;")
        behavior_row.addWidget(self._behavior_lbl)

        behavior_row.addStretch()

        self._regime_lbl = QLabel("")
        regime_font = QFont()
        regime_font.setPointSize(8)
        self._regime_lbl.setFont(regime_font)
        self._regime_lbl.setStyleSheet("color: #607D8B;")
        self._regime_lbl.setAlignment(Qt.AlignRight)
        behavior_row.addWidget(self._regime_lbl)

        main_layout.addLayout(behavior_row)

        # ── SECTION 4: Verdict frame ────────────────────────────────────
        self._verdict_frame = QFrame()
        self._verdict_frame.setFrameShape(QFrame.StyledPanel)
        self._verdict_frame.setVisible(False)
        verdict_layout = QVBoxLayout(self._verdict_frame)
        verdict_layout.setContentsMargins(8, 6, 8, 6)
        verdict_layout.setSpacing(4)

        self._verdict_badge_lbl = QLabel("")
        badge_font = QFont()
        badge_font.setBold(True)
        badge_font.setPointSize(16)
        self._verdict_badge_lbl.setFont(badge_font)
        self._verdict_badge_lbl.setAlignment(Qt.AlignCenter)
        verdict_layout.addWidget(self._verdict_badge_lbl)

        # Details row
        details_row = QHBoxLayout()
        details_row.setSpacing(12)

        self._pipeline_score_lbl = QLabel("")
        self._pipeline_score_lbl.setStyleSheet("color: #90A4AE;")
        details_row.addWidget(self._pipeline_score_lbl)

        self._position_size_lbl = QLabel("")
        pos_font = QFont()
        pos_font.setBold(True)
        self._position_size_lbl.setFont(pos_font)
        self._position_size_lbl.setStyleSheet("color: #ECEFF1; font-weight: bold;")
        details_row.addWidget(self._position_size_lbl)

        self._confidence_lbl = QLabel("")
        self._confidence_lbl.setStyleSheet("color: #90A4AE;")
        details_row.addWidget(self._confidence_lbl)

        details_row.addStretch()
        verdict_layout.addLayout(details_row)

        self._exit_lbl = QLabel("")
        exit_font = QFont()
        exit_font.setPointSize(8)
        self._exit_lbl.setFont(exit_font)
        self._exit_lbl.setStyleSheet("color: #607D8B;")
        self._exit_lbl.setWordWrap(True)
        verdict_layout.addWidget(self._exit_lbl)

        why_title = QLabel("Why:")
        why_title_font = QFont()
        why_title_font.setBold(True)
        why_title_font.setPointSize(8)
        why_title.setFont(why_title_font)
        why_title.setStyleSheet("color: #90A4AE;")
        verdict_layout.addWidget(why_title)

        self._why_text = QTextEdit()
        self._why_text.setReadOnly(True)
        self._why_text.setMaximumHeight(100)
        why_text_font = QFont()
        why_text_font.setPointSize(8)
        self._why_text.setFont(why_text_font)
        verdict_layout.addWidget(self._why_text)

        self._risk_flags_lbl = QLabel("")
        risk_font = QFont()
        risk_font.setPointSize(8)
        self._risk_flags_lbl.setFont(risk_font)
        self._risk_flags_lbl.setStyleSheet("color: #FF9800;")
        self._risk_flags_lbl.setWordWrap(True)
        verdict_layout.addWidget(self._risk_flags_lbl)

        main_layout.addWidget(self._verdict_frame)

        # ── SECTION 5: Agents 1-6 grid ──────────────────────────────────
        agents_frame = QFrame()
        agents_frame.setFrameShape(QFrame.StyledPanel)
        agents_outer_layout = QVBoxLayout(agents_frame)
        agents_outer_layout.setContentsMargins(6, 4, 6, 4)
        agents_outer_layout.setSpacing(4)

        agents_title = QLabel("Agents 1-6")
        agents_title_font = QFont()
        agents_title_font.setBold(True)
        agents_title.setFont(agents_title_font)
        agents_title.setStyleSheet("color: #90A4AE;")
        agents_outer_layout.addWidget(agents_title)

        grid = QGridLayout()
        grid.setSpacing(4)
        agents_outer_layout.addLayout(grid)

        self._agent_vals: dict[str, QLabel] = {}
        agent_names = [
            "1-Indicator", "2-Confluence", "3-Options",
            "4-News", "5-Chart", "6-Journal",
        ]
        for i, name in enumerate(agent_names):
            row = i // 3
            col = i % 3
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(0)

            name_lbl = QLabel(name)
            name_font = QFont()
            name_font.setPointSize(7)
            name_lbl.setFont(name_font)
            name_lbl.setStyleSheet("color: #607D8B;")
            cell_layout.addWidget(name_lbl)

            val_lbl = QLabel("—")
            val_font = QFont()
            val_font.setBold(True)
            val_font.setPointSize(8)
            val_lbl.setFont(val_font)
            val_lbl.setStyleSheet("color: #607D8B; font-weight: bold;")
            cell_layout.addWidget(val_lbl)

            self._agent_vals[name] = val_lbl
            grid.addWidget(cell, row, col)

        main_layout.addWidget(agents_frame)
        main_layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_pipeline_score(self, data: dict) -> None:
        score = data.get("pipeline_score", 0)
        behavior = data.get("behavior_fired", None)
        direction = data.get("behavior_direction", None)
        self._pipeline_score = score
        self._signal_score_lbl.setText(f"{score}/100")

        if score >= SCORE_STRONG:
            color, level = "#F44336", "STRONG SIGNAL"
        elif score >= SCORE_SIGNAL:
            color, level = "#FF9800", "SIGNAL"
        elif score >= SCORE_WATCH:
            color, level = "#607D8B", "WATCH"
        else:
            color, level = "#607D8B", "NOISE"

        self._signal_score_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 14px;")
        self._signal_level_lbl.setText(level)

        if behavior:
            self._behavior_lbl.setText(
                f"⬟ {behavior.replace('_', ' ')}{' — ' + direction if direction else ''}"
            )
        else:
            self._behavior_lbl.setText("No behavior detected")

        self._update_analyze_button(score)

        if score >= SCORE_STRONG:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._analyze_btn.setStyleSheet("")

    def update_alert_score(self, score: int) -> None:
        self._alert_score = score
        self._alert_score_lbl.setText(f"{score}/21")
        if score >= 18:
            color, level = "#F44336", "MAX"
        elif score >= 14:
            color, level = "#FF6D00", "STRONG"
        elif score >= 10:
            color, level = "#FF9800", "SOFT"
        else:
            color, level = "#607D8B", "SILENCE"
        self._alert_score_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 14px;")
        self._alert_level_lbl.setText(level)

    def display_verdict(self, data: dict) -> None:
        agent7 = data.get("agent_7_json", {})
        verdict = agent7.get("verdict", "WAIT")
        pipeline_score = agent7.get("pipeline_score", self._pipeline_score)
        pos_size = agent7.get("position_size_rupees", 0)
        pos_method = agent7.get("position_size_method", "")
        confidence = agent7.get("confidence", 0)
        why_text = agent7.get("why", "")
        risk_flags = agent7.get("risk_flags", [])
        conflict_log = agent7.get("conflict_log", [])
        sl = agent7.get("stop_loss", 0)
        t1 = agent7.get("target_1", 0)
        t2 = agent7.get("target_2", 0)

        self._verdict_frame.setVisible(True)

        verdict_colors = {
            "BUY CE": ("#26a69a", "▲ BUY CE"),
            "BUY PE": ("#ef5350", "▼ BUY PE"),
            "WAIT":   ("#FF9800", "◆ WAIT"),
            "AVOID":  ("#607D8B", "✕ AVOID"),
        }
        color, label = verdict_colors.get(verdict, ("#607D8B", verdict))
        self._verdict_badge_lbl.setText(label)
        self._verdict_badge_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 16px;")

        self._pipeline_score_lbl.setText(f"Score: {pipeline_score}/100")
        if pos_size:
            self._position_size_lbl.setText(f"Size: ₹{pos_size:,.0f}  ({pos_method})")
        self._confidence_lbl.setText(f"Confidence: {confidence}%")

        if sl and t1 and t2:
            self._exit_lbl.setText(f"SL: {sl:,.2f}  →  T1: {t1:,.2f}  →  T2: {t2:,.2f}")

        self._why_text.setPlainText(why_text)

        all_flags = risk_flags + conflict_log
        if all_flags:
            self._risk_flags_lbl.setText("⚠ " + "  |  ".join(all_flags))
        else:
            self._risk_flags_lbl.setText("")

        # Update agents 1-6
        agent_map = [
            ("1-Indicator",  data.get("agent_1_json", {})),
            ("2-Confluence", data.get("agent_2_json", {})),
            ("3-Options",    data.get("agent_3_json", {})),
            ("4-News",       data.get("agent_4_json", {})),
            ("5-Chart",      data.get("agent_5_json", {})),
            ("6-Journal",    data.get("agent_6_json", {})),
        ]
        for name, aj in agent_map:
            lbl = self._agent_vals.get(name)
            if not lbl:
                continue
            if name == "1-Indicator":
                sig = aj.get("signal", "—")
                conf = aj.get("confidence", 0)
                lbl.setText(f"{sig} {conf}%")
                c = "#26a69a" if "BULL" in sig else ("#ef5350" if "BEAR" in sig else "#607D8B")
                lbl.setStyleSheet(f"color: {c}; font-weight: bold;")
            elif name == "2-Confluence":
                lbl.setText(f"{aj.get('confluence_score', '—')} {aj.get('confluence_label', '')}")
            elif name == "3-Options":
                bias = aj.get("oi_bias", "—")
                veto = " 🚫" if (aj.get("ce_blocked") or aj.get("pe_blocked")) else ""
                lbl.setText(f"{bias}{veto}")
            elif name == "4-News":
                sent = aj.get("overall_sentiment", "—")
                tag = " ⚠HIGH" if aj.get("news_event_day") else ""
                lbl.setText(f"{sent}{tag}")
            elif name == "5-Chart":
                squeeze = " 🔲" if aj.get("squeeze_alert") else ""
                lbl.setText(f"{aj.get('primary_pattern', '—')[:12]}{squeeze}")
            elif name == "6-Journal":
                lbl.setText(aj.get("strongest_behavior", "cold start") or "cold start")

        # Add signal marker to chart
        try:
            app = self.window()
            if hasattr(app, "chart_panel"):
                entry = agent7.get("entry_zone", {})
                mid = (entry.get("low", 0) + entry.get("high", 0)) / 2
                if mid and verdict in ("BUY CE", "BUY PE"):
                    from datetime import datetime
                    import pytz
                    now_str = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
                    app.chart_panel.add_signal_marker(now_str, verdict, mid)
        except Exception:
            pass

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol
        self._pipeline_score = 0
        self._alert_score = 0
        self._signal_score_lbl.setText("—/100")
        self._alert_score_lbl.setText("—/21")
        self._behavior_lbl.setText("No behavior detected")
        self._verdict_frame.setVisible(False)
        self._analyze_btn.setVisible(False)
        self._pulse_timer.stop()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_analyze_button(self, score: int) -> None:
        if score >= SCORE_SIGNAL:
            self._analyze_btn.setVisible(True)
            self._analyze_btn.setEnabled(True)
            if score >= SCORE_STRONG:
                self._analyze_btn.setText(f"Score: {score} — STRONG SIGNAL")
                self._analyze_btn.setStyleSheet(
                    "QPushButton { background: #F44336; color: white; font-weight: bold; padding: 6px; }"
                )
            else:
                self._analyze_btn.setText(f"Score: {score} — Analyze Now")
                self._analyze_btn.setStyleSheet(
                    "QPushButton { background: #FF9800; color: white; font-weight: bold; padding: 6px; }"
                )
        elif score >= SCORE_WATCH:
            self._analyze_btn.setVisible(True)
            self._analyze_btn.setEnabled(False)
            self._analyze_btn.setText(f"Score: {score} — Setup forming")
            self._analyze_btn.setStyleSheet(
                "QPushButton { background: #37474F; color: #607D8B; padding: 6px; }"
            )
        else:
            self._analyze_btn.setVisible(False)

    def _on_analyze(self) -> None:
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.setText("Analyzing…")
        self._on_analyze_click()

    def _pulse_tick(self) -> None:
        self._pulse_state = not self._pulse_state
        style = (
            "QPushButton { background: #F44336; color: white; font-weight: bold; padding: 6px; }"
            if self._pulse_state else
            "QPushButton { background: #FF6D00; color: white; font-weight: bold; padding: 6px; }"
        )
        self._analyze_btn.setStyleSheet(style)
