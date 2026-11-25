"""
Options panel for TradeDesk.

Thread: MAIN THREAD only.

Responsibilities:
  - PCR bar with interpretation label.
  - Max Pain price + distance from current price.
  - IV Rank badge (LOW / NORMAL / HIGH / EXTREME).
  - ATM CE vs ATM PE OI comparison bar.
  - Top strikes OI table.
  - Veto banner (ce_blocked, pe_blocked) from Agent 3.
  - Theta warning banner.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytz
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QProgressBar,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class OptionsPanel(QWidget):
    """Options chain summary panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._symbol = "NIFTY"
        self._atm_ce_oi = 0
        self._atm_pe_oi = 0
        self._pcr_raw = 1.0
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ── HEADER row ─────────────────────────────────────────────────
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(6)

        hdr_title = QLabel("OPTIONS CHAIN")
        hdr_title_font = QFont()
        hdr_title_font.setBold(True)
        hdr_title_font.setPointSize(9)
        hdr_title.setFont(hdr_title_font)
        hdr_layout.addWidget(hdr_title)

        hdr_layout.addStretch()

        self._last_update_lbl = QLabel("")
        lu_font = QFont()
        lu_font.setPointSize(7)
        self._last_update_lbl.setFont(lu_font)
        self._last_update_lbl.setStyleSheet("color: #607D8B;")
        self._last_update_lbl.setAlignment(Qt.AlignRight)
        hdr_layout.addWidget(self._last_update_lbl)

        self._dte_lbl = QLabel("DTE: —")
        dte_font = QFont()
        dte_font.setPointSize(8)
        self._dte_lbl.setFont(dte_font)
        self._dte_lbl.setAlignment(Qt.AlignRight)
        hdr_layout.addWidget(self._dte_lbl)

        self._expiry_lbl = QLabel("Expiry: —")
        expiry_font = QFont()
        expiry_font.setPointSize(8)
        self._expiry_lbl.setFont(expiry_font)
        self._expiry_lbl.setAlignment(Qt.AlignRight)
        hdr_layout.addWidget(self._expiry_lbl)

        main_layout.addLayout(hdr_layout)

        # ── VETO banner (hidden) ────────────────────────────────────────
        self._veto_frame = QFrame()
        self._veto_frame.setStyleSheet("background: #F44336;")
        veto_layout = QHBoxLayout(self._veto_frame)
        veto_layout.setContentsMargins(6, 3, 6, 3)

        self._veto_lbl = QLabel("")
        veto_lbl_font = QFont()
        veto_lbl_font.setBold(True)
        self._veto_lbl.setFont(veto_lbl_font)
        self._veto_lbl.setStyleSheet("color: white;")
        self._veto_lbl.setAlignment(Qt.AlignCenter)
        veto_layout.addWidget(self._veto_lbl)

        self._veto_frame.setVisible(False)
        main_layout.addWidget(self._veto_frame)

        # ── THETA warning (hidden) ──────────────────────────────────────
        self._theta_frame = QFrame()
        self._theta_frame.setStyleSheet("background: #FF9800;")
        theta_layout = QHBoxLayout(self._theta_frame)
        theta_layout.setContentsMargins(6, 2, 6, 2)

        self._theta_lbl = QLabel("")
        theta_lbl_font = QFont()
        theta_lbl_font.setPointSize(8)
        self._theta_lbl.setFont(theta_lbl_font)
        self._theta_lbl.setStyleSheet("color: white;")
        self._theta_lbl.setAlignment(Qt.AlignCenter)
        theta_layout.addWidget(self._theta_lbl)

        self._theta_frame.setVisible(False)
        main_layout.addWidget(self._theta_frame)

        # ── PCR group ──────────────────────────────────────────────────
        pcr_group = QFrame()
        pcr_group.setFrameShape(QFrame.StyledPanel)
        pcr_group_layout = QVBoxLayout(pcr_group)
        pcr_group_layout.setContentsMargins(6, 4, 6, 4)
        pcr_group_layout.setSpacing(4)

        pcr_title = QLabel("PCR — Put-Call Ratio")
        pcr_title_font = QFont()
        pcr_title_font.setPointSize(8)
        pcr_title.setFont(pcr_title_font)
        pcr_title.setStyleSheet("color: #607D8B;")
        pcr_group_layout.addWidget(pcr_title)

        pcr_row = QHBoxLayout()
        pcr_row.setSpacing(12)

        self._pcr_value_lbl = QLabel("—")
        pcr_val_font = QFont()
        pcr_val_font.setBold(True)
        pcr_val_font.setPointSize(16)
        self._pcr_value_lbl.setFont(pcr_val_font)
        pcr_row.addWidget(self._pcr_value_lbl)

        self._pcr_interp_lbl = QLabel("No data")
        pcr_interp_font = QFont()
        pcr_interp_font.setPointSize(9)
        self._pcr_interp_lbl.setFont(pcr_interp_font)
        self._pcr_interp_lbl.setStyleSheet("color: #607D8B;")
        pcr_row.addWidget(self._pcr_interp_lbl)

        pcr_row.addStretch()
        pcr_group_layout.addLayout(pcr_row)

        self._pcr_bar = QProgressBar()
        self._pcr_bar.setRange(0, 100)
        self._pcr_bar.setValue(50)
        self._pcr_bar.setFixedHeight(10)
        self._pcr_bar.setTextVisible(False)
        pcr_group_layout.addWidget(self._pcr_bar)

        main_layout.addWidget(pcr_group)

        # ── MAX PAIN group ─────────────────────────────────────────────
        mp_group = QFrame()
        mp_group.setFrameShape(QFrame.StyledPanel)
        mp_group_layout = QVBoxLayout(mp_group)
        mp_group_layout.setContentsMargins(6, 4, 6, 4)
        mp_group_layout.setSpacing(2)

        mp_title = QLabel("Max Pain")
        mp_title_font = QFont()
        mp_title_font.setPointSize(8)
        mp_title.setFont(mp_title_font)
        mp_title.setStyleSheet("color: #607D8B;")
        mp_group_layout.addWidget(mp_title)

        mp_row = QHBoxLayout()
        mp_row.setSpacing(12)

        self._maxpain_lbl = QLabel("—")
        mp_val_font = QFont()
        mp_val_font.setBold(True)
        mp_val_font.setPointSize(14)
        self._maxpain_lbl.setFont(mp_val_font)
        mp_row.addWidget(self._maxpain_lbl)

        self._mp_distance_lbl = QLabel("")
        mp_dist_font = QFont()
        mp_dist_font.setPointSize(9)
        self._mp_distance_lbl.setFont(mp_dist_font)
        self._mp_distance_lbl.setStyleSheet("color: #607D8B;")
        mp_row.addWidget(self._mp_distance_lbl)

        mp_row.addStretch()
        mp_group_layout.addLayout(mp_row)

        main_layout.addWidget(mp_group)

        # ── IV RANK group ───────────────────────────────────────────────
        iv_group = QFrame()
        iv_group.setFrameShape(QFrame.StyledPanel)
        iv_group_layout = QVBoxLayout(iv_group)
        iv_group_layout.setContentsMargins(6, 4, 6, 4)
        iv_group_layout.setSpacing(2)

        iv_title = QLabel("IV Rank")
        iv_title_font = QFont()
        iv_title_font.setPointSize(8)
        iv_title.setFont(iv_title_font)
        iv_title.setStyleSheet("color: #607D8B;")
        iv_group_layout.addWidget(iv_title)

        iv_row = QHBoxLayout()
        iv_row.setSpacing(8)

        self._iv_rank_lbl = QLabel("—")
        iv_rank_font = QFont()
        iv_rank_font.setBold(True)
        iv_rank_font.setPointSize(14)
        self._iv_rank_lbl.setFont(iv_rank_font)
        iv_row.addWidget(self._iv_rank_lbl)

        self._iv_label_lbl = QLabel("")
        iv_label_font = QFont()
        iv_label_font.setBold(True)
        iv_label_font.setPointSize(9)
        self._iv_label_lbl.setFont(iv_label_font)
        iv_row.addWidget(self._iv_label_lbl)

        iv_row.addStretch()

        self._iv_extreme_lbl = QLabel("")
        iv_ext_font = QFont()
        iv_ext_font.setPointSize(8)
        self._iv_extreme_lbl.setFont(iv_ext_font)
        self._iv_extreme_lbl.setStyleSheet("color: #FF9800;")
        self._iv_extreme_lbl.setAlignment(Qt.AlignRight)
        iv_row.addWidget(self._iv_extreme_lbl)

        iv_group_layout.addLayout(iv_row)

        main_layout.addWidget(iv_group)

        # ── ATM OI group ────────────────────────────────────────────────
        atm_group = QFrame()
        atm_group.setFrameShape(QFrame.StyledPanel)
        atm_group_layout = QVBoxLayout(atm_group)
        atm_group_layout.setContentsMargins(6, 4, 6, 4)
        atm_group_layout.setSpacing(4)

        atm_title = QLabel("ATM OI  CE vs PE")
        atm_title_font = QFont()
        atm_title_font.setPointSize(8)
        atm_title.setFont(atm_title_font)
        atm_title.setStyleSheet("color: #607D8B;")
        atm_group_layout.addWidget(atm_title)

        atm_row = QHBoxLayout()

        self._atm_ce_lbl = QLabel("CE OI: —")
        atm_ce_font = QFont()
        atm_ce_font.setPointSize(9)
        self._atm_ce_lbl.setFont(atm_ce_font)
        self._atm_ce_lbl.setStyleSheet("color: #26a69a;")
        atm_row.addWidget(self._atm_ce_lbl)

        atm_row.addStretch()

        self._atm_pe_lbl = QLabel("PE OI: —")
        atm_pe_font = QFont()
        atm_pe_font.setPointSize(9)
        self._atm_pe_lbl.setFont(atm_pe_font)
        self._atm_pe_lbl.setStyleSheet("color: #ef5350;")
        atm_row.addWidget(self._atm_pe_lbl)

        atm_group_layout.addLayout(atm_row)

        self._atm_bar = QProgressBar()
        self._atm_bar.setRange(0, 100)
        self._atm_bar.setValue(50)
        self._atm_bar.setFixedHeight(12)
        self._atm_bar.setTextVisible(False)
        self._atm_bar.setStyleSheet(
            "QProgressBar::chunk { background: #26a69a; }"
            "QProgressBar { background: #ef5350; }"
        )
        atm_group_layout.addWidget(self._atm_bar)

        main_layout.addWidget(atm_group)

        # ── TOP STRIKES group ───────────────────────────────────────────
        strikes_group = QFrame()
        strikes_group.setFrameShape(QFrame.StyledPanel)
        strikes_group_layout = QVBoxLayout(strikes_group)
        strikes_group_layout.setContentsMargins(6, 4, 6, 4)
        strikes_group_layout.setSpacing(4)

        strikes_title = QLabel("Top Strikes by OI")
        strikes_title_font = QFont()
        strikes_title_font.setPointSize(8)
        strikes_title.setFont(strikes_title_font)
        strikes_title.setStyleSheet("color: #607D8B;")
        strikes_group_layout.addWidget(strikes_title)

        self._strikes_table = QTableWidget()
        self._strikes_table.setColumnCount(3)
        self._strikes_table.setHorizontalHeaderLabels(["CE OI", "Strike", "PE OI"])
        self._strikes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._strikes_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._strikes_table.setSelectionMode(QTableWidget.NoSelection)
        self._strikes_table.setAlternatingRowColors(True)
        self._strikes_table.setMaximumHeight(180)
        self._strikes_table.verticalHeader().setVisible(False)
        strikes_group_layout.addWidget(self._strikes_table)

        main_layout.addWidget(strikes_group)
        main_layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_options_data(self, data: dict) -> None:
        if not data:
            return

        pcr          = float(data.get("pcr", 1.0))
        max_pain     = float(data.get("max_pain", 0))
        iv_rank_raw  = data.get("iv_rank")
        iv_rank      = float(iv_rank_raw) if iv_rank_raw is not None else None
        atm_ce_oi    = int(data.get("atm_ce_oi", 0))
        atm_pe_oi    = int(data.get("atm_pe_oi", 0))
        expiry       = data.get("expiry_date", data.get("expiry", "—"))
        dte          = data.get("dte", "—")
        current_price = float(data.get("current_price", 0))
        ce_blocked   = data.get("ce_blocked", False)
        pe_blocked   = data.get("pe_blocked", False)
        iv_extreme   = data.get("iv_extreme", False)
        theta_warn   = data.get("theta_warning", False)
        top_ce       = data.get("top_ce_strikes", [])
        top_pe       = data.get("top_pe_strikes", [])

        # Header
        self._expiry_lbl.setText(f"Expiry: {expiry}")
        self._dte_lbl.setText(f"DTE: {dte}")
        now_str = datetime.now(IST).strftime("%H:%M:%S")
        self._last_update_lbl.setText(f"Updated: {now_str}")

        # PCR
        self._pcr_raw = pcr
        self._pcr_value_lbl.setText(f"{pcr:.2f}")
        pcr_pct = int(max(0, min(100, (pcr - 0.5) / 1.0 * 100)))
        self._pcr_bar.setValue(pcr_pct)
        if pcr_pct > 50:
            self._pcr_bar.setStyleSheet("QProgressBar::chunk { background: #26a69a; }")
            self._pcr_interp_lbl.setText("Put writing — BULLISH")
            self._pcr_interp_lbl.setStyleSheet("color: #26a69a;")
        else:
            self._pcr_bar.setStyleSheet("QProgressBar::chunk { background: #ef5350; }")
            self._pcr_interp_lbl.setText("Call writing — BEARISH")
            self._pcr_interp_lbl.setStyleSheet("color: #ef5350;")

        # Max Pain
        self._maxpain_lbl.setText(f"{max_pain:,.2f}" if max_pain else "—")
        if max_pain and current_price:
            dist = (current_price - max_pain) / max_pain * 100
            direction = "above" if dist > 0 else "below"
            self._mp_distance_lbl.setText(f"Price {abs(dist):.1f}% {direction} Max Pain")

        # IV Rank
        if iv_rank is None:
            self._iv_rank_lbl.setText("—")
            self._iv_label_lbl.setText("")
        else:
            self._iv_rank_lbl.setText(f"{iv_rank:.0f}")
            if iv_rank > 80:
                label, color = "EXTREME", "#F44336"
            elif iv_rank > 60:
                label, color = "HIGH", "#FF9800"
            elif iv_rank > 30:
                label, color = "NORMAL", "#607D8B"
            else:
                label, color = "LOW", "#26a69a"
            self._iv_label_lbl.setText(label)
            self._iv_label_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
            self._iv_rank_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 14px;")
        self._iv_extreme_lbl.setText("⚠ IV EXTREME — Size ×0.5" if iv_extreme else "")

        # ATM OI
        self._atm_ce_oi = atm_ce_oi
        self._atm_pe_oi = atm_pe_oi
        self._atm_ce_lbl.setText(f"CE OI: {atm_ce_oi:,}")
        self._atm_pe_lbl.setText(f"PE OI: {atm_pe_oi:,}")
        total = atm_ce_oi + atm_pe_oi
        ce_pct = int(atm_ce_oi / total * 100) if total > 0 else 50
        self._atm_bar.setValue(ce_pct)
        self._atm_bar.setStyleSheet(
            "QProgressBar::chunk { background: #26a69a; }"
            "QProgressBar { background: #ef5350; }"
        )

        # Veto banner
        veto_msgs = []
        if ce_blocked:
            veto_msgs.append("CE BLOCKED")
        if pe_blocked:
            veto_msgs.append("PE BLOCKED")
        if veto_msgs:
            self._veto_lbl.setText("🚫  " + "  |  ".join(veto_msgs))
            self._veto_frame.setVisible(True)
        else:
            self._veto_frame.setVisible(False)

        # Theta warning
        if theta_warn:
            self._theta_lbl.setText(f"⚠ DTE {dte} — Theta decay elevated")
            self._theta_frame.setVisible(True)
        else:
            self._theta_frame.setVisible(False)

        # Top strikes table
        self._update_strikes_table(top_ce, top_pe, max_pain)

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_strikes_table(self, top_ce: list, top_pe: list, max_pain: float) -> None:
        self._strikes_table.clearContents()

        ce_by_strike = {item.get("strike", 0): item.get("oi", 0) for item in top_ce[:5]}
        pe_by_strike = {item.get("strike", 0): item.get("oi", 0) for item in top_pe[:5]}
        all_strikes = sorted(
            set(list(ce_by_strike.keys()) + list(pe_by_strike.keys())),
            reverse=True,
        )[:8]

        self._strikes_table.setRowCount(len(all_strikes))

        for row, strike in enumerate(all_strikes):
            ce_oi = ce_by_strike.get(strike, 0)
            pe_oi = pe_by_strike.get(strike, 0)
            is_atm = abs(strike - max_pain) < 200 if max_pain else False

            ce_item = QTableWidgetItem(f"{ce_oi:,}" if ce_oi else "—")
            ce_item.setForeground(QColor("#26a69a"))
            ce_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            st_item = QTableWidgetItem(f"{strike:,.0f}")
            if is_atm:
                st_item.setForeground(QColor("#FF9800"))
                st_item.setFont(QFont("Arial", 8, QFont.Bold))
            st_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)

            pe_item = QTableWidgetItem(f"{pe_oi:,}" if pe_oi else "—")
            pe_item.setForeground(QColor("#ef5350"))
            pe_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self._strikes_table.setItem(row, 0, ce_item)
            self._strikes_table.setItem(row, 1, st_item)
            self._strikes_table.setItem(row, 2, pe_item)
