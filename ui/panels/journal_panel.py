"""
Journal panel for TradeDesk.

Thread: MAIN THREAD only.

Responsibilities:
  - PENDING trade section with 5-minute countdown (QTimer per trade).
  - Three buttons per pending trade: ACCEPT, NOT TAKEN (reason combo), MODIFY SIZE.
  - Auto-ACCEPT after 300 s.
  - Trade history QTreeWidget (color-coded).
  - Today's P&L stats bar.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytz
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QTreeWidget, QTreeWidgetItem,
    QDialog, QLineEdit, QHeaderView, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QBrush

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

PENDING_SECONDS = 300

NOT_TAKEN_REASONS = [
    "Missed entry",
    "Disagreed with AI signal",
    "Risk too high for today",
    "Already have max positions",
    "Other",
]


class JournalPanel(QWidget):
    """Trade journal panel with pending timer and history table."""

    def __init__(self, parent, shared_queue):
        super().__init__(parent)
        self._shared_queue = shared_queue
        # {trade_id: {"remaining": int, "timer": QTimer, "card": QFrame,
        #              "label": QLabel, "trade": dict}}
        self._pending_trades: dict = {}
        self._build_ui()
        self._refresh_trade_history()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # ── PENDING section ─────────────────────────────────────────────
        pending_frame = QFrame()
        pending_frame.setFrameShape(QFrame.StyledPanel)
        pending_outer = QVBoxLayout(pending_frame)
        pending_outer.setContentsMargins(6, 4, 6, 4)
        pending_outer.setSpacing(4)

        pending_title = QLabel("PENDING TRADES")
        ptf = QFont()
        ptf.setBold(True)
        ptf.setPointSize(8)
        pending_title.setFont(ptf)
        pending_title.setStyleSheet("color: #90A4AE;")
        pending_outer.addWidget(pending_title)

        self._no_pending_lbl = QLabel("No pending trades")
        npl_font = QFont()
        npl_font.setPointSize(8)
        self._no_pending_lbl.setFont(npl_font)
        self._no_pending_lbl.setStyleSheet("color: #607D8B;")
        pending_outer.addWidget(self._no_pending_lbl)

        self._pending_container = QWidget()
        self._pending_layout = QVBoxLayout(self._pending_container)
        self._pending_layout.setSpacing(4)
        self._pending_layout.setContentsMargins(0, 0, 0, 0)
        pending_outer.addWidget(self._pending_container)

        main_layout.addWidget(pending_frame)

        # ── Stats bar ────────────────────────────────────────────────────
        stats_bar = QHBoxLayout()
        stats_bar.setSpacing(8)

        self._stats_lbl = QLabel("Today: No trades yet")
        stats_font = QFont()
        stats_font.setPointSize(8)
        self._stats_lbl.setFont(stats_font)
        self._stats_lbl.setStyleSheet("color: #607D8B;")
        stats_bar.addWidget(self._stats_lbl)

        stats_bar.addStretch()

        refresh_btn = QPushButton("↺ Refresh")
        refresh_btn.setFlat(True)
        refresh_btn.setStyleSheet("color: #607D8B; font-size: 8px;")
        refresh_btn.clicked.connect(self._refresh_trade_history)
        stats_bar.addWidget(refresh_btn)

        main_layout.addLayout(stats_bar)

        # ── Separator ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(sep)

        # ── Trade history QTreeWidget ────────────────────────────────────
        self._tree_widget = QTreeWidget()
        self._tree_widget.setColumnCount(8)
        self._tree_widget.setHeaderLabels(
            ["Time", "Symbol", "Dir", "Behavior", "Entry", "Exit", "P&L (₹)", "Outcome"]
        )
        self._tree_widget.setAlternatingRowColors(True)
        self._tree_widget.setEditTriggers(QTreeWidget.NoEditTriggers)
        self._tree_widget.setSelectionMode(QTreeWidget.NoSelection)
        self._tree_widget.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        main_layout.addWidget(self._tree_widget)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_paper_trade_update(self, data: dict) -> None:
        event    = data.get("event", "")
        trade    = data.get("trade", {})
        trade_id = trade.get("trade_id") or data.get("trade_id")

        if event == "PENDING_CREATED":
            self._show_pending_trade(trade)
        elif event in ("ACCEPTED", "AUTO_ACCEPTED", "NOT_TAKEN",
                       "T1_HIT", "T2_HIT", "SL_HIT", "HARD_EXIT"):
            self._remove_pending_widget(trade_id)
            self._refresh_trade_history()
        elif event == "MODIFIED":
            self._remove_pending_widget(trade_id)
            self._show_pending_trade(trade)

        self._refresh_stats()

    # ------------------------------------------------------------------
    # Pending trade widget
    # ------------------------------------------------------------------

    def _show_pending_trade(self, trade: dict) -> None:
        trade_id = trade.get("trade_id", id(trade))
        if trade_id in self._pending_trades:
            return

        self._no_pending_lbl.setVisible(False)

        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            "QFrame { background: #1a237e; border: 1px solid #3949AB; padding: 4px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(2)
        card_layout.setContentsMargins(6, 4, 6, 4)

        symbol    = trade.get("symbol", "")
        direction = trade.get("ai_verdict", trade.get("direction", ""))
        behavior  = trade.get("behavior_triggered", "")
        size      = trade.get("position_size_rupees", 0)
        entry_low = trade.get("entry_zone_low", 0)
        entry_hi  = trade.get("entry_zone_high", 0)

        # Row 1: info
        row1 = QHBoxLayout()
        sym_lbl = QLabel(symbol)
        sym_lbl.setStyleSheet("font-weight: bold; color: #ECEFF1;")
        row1.addWidget(sym_lbl)

        dir_color = "#26a69a" if "CE" in str(direction) else "#ef5350"
        dir_lbl = QLabel(direction)
        dir_lbl.setStyleSheet(f"color: {dir_color}; font-weight: bold;")
        row1.addWidget(dir_lbl)

        if behavior:
            beh_lbl = QLabel(behavior.replace("_", " "))
            beh_lbl.setStyleSheet("color: #90A4AE; font-size: 8px;")
            row1.addWidget(beh_lbl)

        row1.addStretch()

        if size:
            sz_lbl = QLabel(f"₹{size:,.0f}")
            sz_lbl.setStyleSheet("font-weight: bold; color: #ECEFF1;")
            row1.addWidget(sz_lbl)

        card_layout.addLayout(row1)

        # Row 2: entry zone
        if entry_low and entry_hi:
            ez_lbl = QLabel(f"Entry: {entry_low:,.2f} – {entry_hi:,.2f}")
            ez_lbl.setStyleSheet("color: #90A4AE; font-size: 8px;")
            card_layout.addWidget(ez_lbl)

        # Row 3: countdown label
        countdown_lbl = QLabel(f"PENDING: {PENDING_SECONDS}s")
        countdown_lbl.setStyleSheet(
            "color: #29B6F6; font-weight: bold; font-size: 8px;"
        )
        card_layout.addWidget(countdown_lbl)

        # Row 4: action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        accept_btn = QPushButton("✓ ACCEPT")
        accept_btn.setStyleSheet(
            "QPushButton { background: #26a69a; color: white; padding: 4px 8px; }"
        )
        accept_btn.clicked.connect(lambda checked=False, tid=trade_id: self._on_accept(tid))
        btn_row.addWidget(accept_btn)

        reason_combo = QComboBox()
        reason_combo.addItems(NOT_TAKEN_REASONS)
        reason_combo.setMaximumWidth(160)
        btn_row.addWidget(reason_combo)

        nt_btn = QPushButton("✕ NOT TAKEN")
        nt_btn.setStyleSheet(
            "QPushButton { background: #ef5350; color: white; padding: 4px 8px; }"
        )
        nt_btn.clicked.connect(
            lambda checked=False, tid=trade_id, cb=reason_combo:
                self._on_not_taken(tid, cb.currentText())
        )
        btn_row.addWidget(nt_btn)

        mod_btn = QPushButton("✎ MODIFY")
        mod_btn.setStyleSheet(
            "QPushButton { background: #37474F; color: white; padding: 4px 8px; }"
        )
        mod_btn.clicked.connect(
            lambda checked=False, tid=trade_id, t=trade: self._on_modify_size(tid, t)
        )
        btn_row.addWidget(mod_btn)

        card_layout.addLayout(btn_row)
        self._pending_layout.addWidget(card)

        # QTimer for countdown (1 s intervals)
        timer = QTimer()
        timer.setInterval(1000)

        self._pending_trades[trade_id] = {
            "remaining": PENDING_SECONDS,
            "timer":     timer,
            "card":      card,
            "label":     countdown_lbl,
            "trade":     trade,
        }
        timer.timeout.connect(lambda tid=trade_id: self._tick_timer(tid))
        timer.start()

    def _tick_timer(self, trade_id: int) -> None:
        if trade_id not in self._pending_trades:
            return
        state = self._pending_trades[trade_id]
        state["remaining"] -= 1
        remaining = state["remaining"]
        state["label"].setText(f"PENDING: {remaining}s — Act now or auto-accept")
        if remaining <= 0:
            state["timer"].stop()
            self._auto_accept(trade_id)

    def _auto_accept(self, trade_id: int) -> None:
        if trade_id not in self._pending_trades:
            return
        trade = self._pending_trades[trade_id].get("trade", {})
        log.info("Auto-accepting pending trade %s", trade_id)
        self._remove_pending_widget(trade_id)
        self._call_accept_api(trade_id, trade)
        self._refresh_trade_history()
        self._refresh_stats()

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_accept(self, trade_id: int) -> None:
        trade = self._pending_trades.get(trade_id, {}).get("trade", {})
        self._remove_pending_widget(trade_id)
        self._call_accept_api(trade_id, trade)
        self._refresh_trade_history()

    def _on_not_taken(self, trade_id: int, reason: str) -> None:
        self._remove_pending_widget(trade_id)
        try:
            from journal.trade_logger import mark_not_taken
            mark_not_taken(trade_id)
            log.info("Trade %d marked NOT_TAKEN: %s", trade_id, reason)
        except Exception as exc:
            log.error("mark_not_taken failed: %s", exc)
        self._refresh_trade_history()

    def _on_modify_size(self, trade_id: int, trade: dict) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Modify Position Size")
        dialog.setFixedSize(320, 160)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("New position size (₹):"))
        size_input = QLineEdit(str(int(trade.get("position_size_rupees", 5000))))
        layout.addWidget(size_input)

        btn_row = QHBoxLayout()
        apply_btn  = QPushButton("Apply")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        apply_btn.clicked.connect(
            lambda: self._apply_modify(dialog, trade_id, trade, size_input.text())
        )
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        dialog.exec()

    def _apply_modify(
        self, dialog: QDialog, trade_id: int, trade: dict, size_text: str
    ) -> None:
        try:
            new_size = float(size_text.replace(",", "").replace("₹", ""))
            from journal.trade_logger import get_connection as _get_conn
            conn = _get_conn()
            conn.execute(
                "UPDATE trades SET position_size_rupees=?, "
                "position_size_method='MANUAL_OVERRIDE' WHERE id=?",
                (new_size, trade_id),
            )
            conn.commit()
            log.info("Trade %d size modified to ₹%.0f", trade_id, new_size)
            dialog.accept()
            updated_trade = {**trade, "position_size_rupees": new_size}
            self._remove_pending_widget(trade_id)
            self._show_pending_trade(updated_trade)
        except Exception as exc:
            log.error("Modify size error: %s", exc)
            dialog.reject()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remove_pending_widget(self, trade_id: int) -> None:
        if trade_id not in self._pending_trades:
            return
        state = self._pending_trades.pop(trade_id)
        state["timer"].stop()
        state["card"].deleteLater()
        if not self._pending_trades:
            self._no_pending_lbl.setVisible(True)

    def _call_accept_api(self, trade_id: int, trade: dict) -> None:
        try:
            from journal.auto_paper_s1 import get_instance
            get_instance().accept_trade(trade_id)
        except Exception as exc:
            log.warning("accept_trade API: %s — using direct DB update", exc)
            try:
                from core.local_db import get_connection
                conn = get_connection()
                conn.execute(
                    "UPDATE trades SET outcome='OPEN' WHERE id=? AND outcome='PENDING'",
                    (trade_id,),
                )
                conn.commit()
            except Exception as exc2:
                log.error("Direct accept DB update: %s", exc2)

    def _refresh_trade_history(self) -> None:
        self._tree_widget.clear()
        try:
            from core.local_db import get_connection
            conn = get_connection()
            rows = conn.execute(
                """
                SELECT id, entry_time, instrument, ai_verdict,
                       behavior_triggered, entry_price, exit_price,
                       pnl, outcome
                FROM trades
                ORDER BY entry_time DESC
                LIMIT 100
                """,
            ).fetchall()

            for r in rows:
                try:
                    ts = r["entry_time"]
                    ts_str = (
                        datetime.fromtimestamp(ts, tz=IST).strftime("%H:%M")
                        if ts else "—"
                    )
                except Exception:
                    ts_str = "—"

                pnl     = r["pnl"]
                outcome = r["outcome"] or "—"
                pnl_str = f"{pnl:+,.0f}" if pnl is not None else "—"
                behavior = (r["behavior_triggered"] or "—").replace("_", " ")[:14]

                item = QTreeWidgetItem([
                    ts_str,
                    r["instrument"] or "—",
                    r["ai_verdict"] or "—",
                    behavior,
                    f"{r['entry_price']:,.2f}" if r["entry_price"] else "—",
                    f"{r['exit_price']:,.2f}"  if r["exit_price"]  else "—",
                    pnl_str,
                    outcome,
                ])

                # P&L column colour (index 6)
                if pnl is not None and pnl > 0:
                    item.setForeground(6, QBrush(QColor("#26a69a")))
                elif pnl is not None and pnl < 0:
                    item.setForeground(6, QBrush(QColor("#ef5350")))

                # Outcome column colour (index 7)
                if outcome == "OPEN":
                    item.setForeground(7, QBrush(QColor("#ffc107")))
                elif outcome == "PENDING":
                    item.setForeground(7, QBrush(QColor("#42A5F5")))
                elif outcome == "NOT_TAKEN":
                    item.setForeground(7, QBrush(QColor("#78909c")))

                self._tree_widget.addTopLevelItem(item)

        except Exception as exc:
            log.warning("JournalPanel._refresh_trade_history: %s", exc)

    def _refresh_stats(self) -> None:
        try:
            from core.local_db import get_connection
            import datetime as _dt
            today = _dt.date.today().isoformat()
            conn  = get_connection()
            rows  = conn.execute(
                """
                SELECT pnl, outcome FROM trades
                WHERE date(entry_time, 'unixepoch', '+5 hours', '30 minutes') = ?
                  AND outcome IN ('CLOSED', 'OPEN')
                """,
                (today,),
            ).fetchall()
            total     = len(rows)
            closed    = [r for r in rows if r["outcome"] == "CLOSED"]
            wins      = sum(1 for r in closed if (r["pnl"] or 0) > 0)
            losses    = len(closed) - wins
            total_pnl = sum((r["pnl"] or 0) for r in closed)
            self._stats_lbl.setText(
                f"Today: {total} trades  |  {wins}W {losses}L  |  "
                + (f"₹{total_pnl:+,.0f}" if closed else "No closed trades")
            )
        except Exception as exc:
            log.debug("Stats refresh: %s", exc)
