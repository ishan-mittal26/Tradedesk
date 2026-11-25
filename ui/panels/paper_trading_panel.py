"""
Paper trading panel for TradeDesk — PySide6 rewrite.

Thread: MAIN THREAD only.

Responsibilities:
  - Open paper trades with live P&L display.
  - Per-behavior win rate + P&L stats.
  - Kelly status per behavior.
  - 8-week gate progress indicator.
"""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTreeWidget, QTreeWidgetItem, QHeaderView, QSizePolicy,
    QGridLayout,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QFont

log = logging.getLogger(__name__)

BEHAVIOR_NAMES = [
    "OPENING_FAKE",
    "INSTITUTIONAL_DEFENSE",
    "LIQUIDITY_SWEEP",
    "SQUEEZE_BREAKOUT",
]


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def _lbl(text: str, size: int = 8, bold: bool = False, muted: bool = False) -> QLabel:
    lbl = QLabel(text)
    font = QFont("Segoe UI", size)
    font.setBold(bold)
    lbl.setFont(font)
    if muted:
        lbl.setStyleSheet("color: #888888;")
    return lbl


class PaperTradingPanel(QWidget):
    """Screen 1 paper trading dashboard."""

    def __init__(self, parent: QWidget, shared_queue, **kwargs):
        super().__init__(parent, **kwargs)
        self._shared_queue = shared_queue
        self._beh_lbls: dict[str, dict[str, QLabel]] = {}
        self._build_ui()
        self._refresh_stats()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Header row ───────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("PAPER TRADING", size=9, bold=True))
        hdr.addStretch()
        self._gate_lbl = _lbl("Gate: —", size=8, muted=True)
        self._gate_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hdr.addWidget(self._gate_lbl)
        root.addLayout(hdr)

        # ── Summary stats row ─────────────────────────────────────────
        stats_row = QHBoxLayout()
        self._total_trades_lbl = _lbl("Trades: —", size=9, muted=True)
        self._win_rate_lbl     = _lbl("Win Rate: —", size=9, muted=True)
        self._pnl_lbl          = _lbl("Total P&L: —", size=9, muted=True)
        stats_row.addWidget(self._total_trades_lbl)
        stats_row.addWidget(self._win_rate_lbl)
        stats_row.addWidget(self._pnl_lbl)
        stats_row.addStretch()
        root.addLayout(stats_row)

        root.addWidget(_sep())

        # ── Open trades ───────────────────────────────────────────────
        root.addWidget(_lbl("Open Trades", size=8, bold=True))

        self._open_tree = QTreeWidget()
        self._open_tree.setColumnCount(8)
        self._open_tree.setHeaderLabels(
            ["Symbol", "Dir", "Behavior", "Entry", "LTP", "P&L(₹)", "SL", "T1"]
        )
        self._open_tree.setAlternatingRowColors(True)
        self._open_tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self._open_tree.setMaximumHeight(140)
        self._open_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        root.addWidget(self._open_tree)

        root.addWidget(_sep())

        # ── Per-behavior stats ────────────────────────────────────────
        root.addWidget(_lbl("Per-Behavior Statistics", size=8, bold=True))

        grid = QGridLayout()
        grid.setSpacing(4)

        for col, header_text in enumerate(
            ["Behavior", "Trades", "Win%", "Avg P&L", "Kelly Status"]
        ):
            h = _lbl(header_text, size=8, bold=True, muted=True)
            grid.addWidget(h, 0, col)

        for row_idx, beh in enumerate(BEHAVIOR_NAMES, start=1):
            label = beh.replace("_", " ").title()
            name_lbl = _lbl(label, size=8)
            grid.addWidget(name_lbl, row_idx, 0)

            cell: dict[str, QLabel] = {}
            for col_idx, key in enumerate(["trades", "winrate", "pnl", "kelly"], start=1):
                lbl_widget = _lbl("—", size=8, muted=True)
                grid.addWidget(lbl_widget, row_idx, col_idx)
                cell[key] = lbl_widget
            self._beh_lbls[beh] = cell

        root.addLayout(grid)

        refresh_btn = QPushButton("↺ Refresh Stats")
        refresh_btn.setFont(QFont("Segoe UI", 8))
        refresh_btn.setFlat(True)
        refresh_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        refresh_btn.clicked.connect(self._refresh_stats)
        root.addWidget(refresh_btn, alignment=Qt.AlignLeft)

        root.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_paper_trade_update(self, data: dict) -> None:
        """Handle paper trade state transitions."""
        self._refresh_open_trades()
        self._refresh_stats()

    # ------------------------------------------------------------------
    # Internal refresh
    # ------------------------------------------------------------------

    def _refresh_open_trades(self) -> None:
        self._open_tree.clear()
        try:
            from core.local_db import get_connection
            conn = get_connection()
            rows = conn.execute(
                """
                SELECT symbol, ai_verdict, behavior_triggered,
                       entry_price, stop_loss, target_1, pnl_rupees
                FROM trades
                WHERE outcome IN ('OPEN', 'PENDING') AND trade_type='PAPER'
                ORDER BY entry_time DESC
                """,
            ).fetchall()
            for r in rows:
                pnl = r["pnl_rupees"] or 0
                behavior = (r["behavior_triggered"] or "COMBO").replace("_", " ")[:14]
                entry_str = f"{r['entry_price']:,.2f}" if r["entry_price"] else "—"
                pnl_str   = f"{pnl:+,.0f}" if pnl else "—"
                sl_str    = f"{r['stop_loss']:,.2f}" if r["stop_loss"] else "—"
                t1_str    = f"{r['target_1']:,.2f}" if r["target_1"] else "—"

                item = QTreeWidgetItem([
                    r["symbol"] or "—",
                    r["ai_verdict"] or "—",
                    behavior,
                    entry_str,
                    "—",
                    pnl_str,
                    sl_str,
                    t1_str,
                ])
                color = QColor("#26a69a") if pnl >= 0 else QColor("#ef5350")
                item.setForeground(5, QBrush(color))
                self._open_tree.addTopLevelItem(item)
        except Exception as exc:
            log.debug("Paper open trades: %s", exc)

    def _refresh_stats(self) -> None:
        try:
            from core.local_db import get_connection
            conn = get_connection()

            rows = conn.execute(
                """
                SELECT pnl_rupees, behavior_triggered, position_size_rupees,
                       position_size_method
                FROM trades
                WHERE outcome='CLOSED' AND not_taken=0
                ORDER BY entry_time
                """,
            ).fetchall()

            total     = len(rows)
            wins      = sum(1 for r in rows if (r["pnl_rupees"] or 0) > 0)
            total_pnl = sum((r["pnl_rupees"] or 0) for r in rows)
            win_rate  = (wins / total * 100) if total else 0.0

            self._total_trades_lbl.setText(f"Trades: {total}")
            self._win_rate_lbl.setText(f"Win Rate: {win_rate:.1f}%")

            pnl_color = "#26a69a" if total_pnl >= 0 else "#ef5350"
            self._pnl_lbl.setText(f"Total P&L: ₹{total_pnl:+,.0f}")
            self._pnl_lbl.setStyleSheet(f"color: {pnl_color};")

            week_num = (date.today() - date(date.today().year, 1, 1)).days // 7 + 1
            self._gate_lbl.setText(f"Paper: {total}/100 trades  |  week {week_num}")

            for beh in BEHAVIOR_NAMES:
                beh_rows = [r for r in rows if r["behavior_triggered"] == beh]
                bt   = len(beh_rows)
                bw   = sum(1 for r in beh_rows if (r["pnl_rupees"] or 0) > 0)
                bpnl = sum((r["pnl_rupees"] or 0) for r in beh_rows)
                bwr  = (bw / bt * 100) if bt else 0.0

                kelly_methods = [
                    r["position_size_method"] for r in beh_rows
                    if r["position_size_method"]
                ]
                if kelly_methods:
                    last_method = kelly_methods[-1]
                    kelly_style = "KELLY_ROLLING" if "KELLY" in last_method else "FLOOR"
                else:
                    kelly_style = "FLOOR (no data)"

                cell = self._beh_lbls[beh]
                cell["trades"].setText(str(bt) if bt else "—")
                cell["winrate"].setText(f"{bwr:.0f}%" if bt else "—")
                cell["pnl"].setText(f"₹{bpnl:+,.0f}" if bt else "—")
                cell["kelly"].setText(kelly_style if bt else "—")

            self._refresh_open_trades()

        except Exception as exc:
            log.debug("Paper stats refresh: %s", exc)
