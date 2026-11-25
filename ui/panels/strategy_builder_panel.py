"""
Strategy builder panel for TradeDesk — PySide6 rewrite.

Thread: MAIN THREAD only.

Responsibilities:
  - Visual custom strategy builder — no coding needed (BRD 12.10).
  - Condition rows: [Indicator ▾] [Comparator ▾] [Value] [✕]
  - Live preview text showing human-readable condition summary.
  - Save: validates → calls strategy_builder.build_strategy() → strategy_library_custom.
  - Load: dropdown of existing custom strategies → populates fields.
  - Delete existing strategy.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QLineEdit, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

log = logging.getLogger(__name__)

COMPARATORS = [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"]

INDICATOR_UNIVERSE = [
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "ema_20", "ema_50", "ema_200",
    "supertrend_dir", "adx_14",
    "bb_upper", "bb_lower", "bb_width",
    "atr_14", "volume_ratio", "vwap",
    "cci_14", "stoch_k", "stoch_d",
    "williams_r", "mfi_14",
]

DIRECTION_OPTIONS = ["BULLISH", "BEARISH", "BOTH"]

INSTRUMENTS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]


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


class StrategyBuilderPanel(QWidget):
    """Visual custom strategy builder."""

    def __init__(self, parent: QWidget = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._condition_rows: list[dict] = []
        self._editing_strategy: Optional[str] = None
        self._build_ui()
        self._refresh_existing_strategies()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Header row ───────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("CUSTOM STRATEGY BUILDER", size=9, bold=True))
        hdr.addStretch()

        hdr.addWidget(_lbl("Load:", size=8, muted=True))
        self._load_combo = QComboBox()
        self._load_combo.addItem("— Select —")
        self._load_combo.setFont(QFont("Segoe UI", 8))
        self._load_combo.setMinimumWidth(160)
        self._load_combo.currentTextChanged.connect(self._on_load_select)
        hdr.addWidget(self._load_combo)

        del_btn = QPushButton("🗑 Delete")
        del_btn.setFont(QFont("Segoe UI", 8))
        del_btn.setStyleSheet("color: #ef5350;")
        del_btn.setFlat(True)
        del_btn.clicked.connect(self._delete_strategy)
        hdr.addWidget(del_btn)

        root.addLayout(hdr)
        root.addWidget(_sep())

        # ── Strategy metadata ─────────────────────────────────────────
        meta = QHBoxLayout()
        meta.setSpacing(8)

        meta.addWidget(_lbl("Name:", size=9))
        self._name_input = QLineEdit()
        self._name_input.setFixedWidth(160)
        self._name_input.setFont(QFont("Segoe UI", 9))
        meta.addWidget(self._name_input)

        meta.addWidget(_lbl("Symbol:", size=9))
        self._symbol_combo = QComboBox()
        self._symbol_combo.addItems(INSTRUMENTS)
        self._symbol_combo.setFont(QFont("Segoe UI", 9))
        meta.addWidget(self._symbol_combo)

        meta.addWidget(_lbl("Direction:", size=9))
        self._direction_combo = QComboBox()
        self._direction_combo.addItems(DIRECTION_OPTIONS)
        self._direction_combo.setFont(QFont("Segoe UI", 9))
        meta.addWidget(self._direction_combo)

        meta.addStretch()
        root.addLayout(meta)

        # ── Conditions section ────────────────────────────────────────
        root.addWidget(_lbl("Conditions (AND logic):", size=8, bold=True))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        self._conditions_layout = QVBoxLayout(inner)
        self._conditions_layout.setContentsMargins(0, 0, 0, 0)
        self._conditions_layout.setSpacing(2)
        self._conditions_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        add_btn = QPushButton("+ Add Condition")
        add_btn.setFont(QFont("Segoe UI", 8))
        add_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        add_btn.clicked.connect(self._add_condition)
        root.addWidget(add_btn, alignment=Qt.AlignLeft)

        # ── Preview ──────────────────────────────────────────────────
        root.addWidget(_lbl("Preview:", size=8, bold=True))

        self._preview_lbl = QLabel("— Add conditions above —")
        self._preview_lbl.setFont(QFont("Segoe UI", 8))
        self._preview_lbl.setWordWrap(True)
        self._preview_lbl.setStyleSheet("color: #aaaaaa; padding: 4px 8px;")
        root.addWidget(self._preview_lbl)

        root.addWidget(_sep())

        # ── Action buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 Save Strategy")
        save_btn.setFont(QFont("Segoe UI", 8))
        save_btn.setStyleSheet("background-color: #26a69a; color: white; padding: 4px 10px;")
        save_btn.clicked.connect(self._save_strategy)
        btn_row.addWidget(save_btn)

        clear_btn = QPushButton("✕ Clear")
        clear_btn.setFont(QFont("Segoe UI", 8))
        clear_btn.setFlat(True)
        clear_btn.clicked.connect(self._clear_form)
        btn_row.addWidget(clear_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont("Segoe UI", 8))
        self._status_lbl.setStyleSheet("color: #26a69a; padding: 2px 8px;")
        root.addWidget(self._status_lbl)

        root.addStretch()

    # ------------------------------------------------------------------
    # Condition management
    # ------------------------------------------------------------------

    def _add_condition(self, ind: str = "", cmp: str = ">", val: str = "50") -> None:
        if len(self._condition_rows) >= 10:
            self._status_lbl.setText("Maximum 10 conditions reached")
            return

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setSpacing(4)
        row_layout.setContentsMargins(0, 0, 0, 0)

        ind_combo = QComboBox()
        ind_combo.addItems(INDICATOR_UNIVERSE)
        ind_combo.setCurrentText(ind or INDICATOR_UNIVERSE[0])
        ind_combo.setFixedWidth(120)
        ind_combo.setFont(QFont("Segoe UI", 8))

        cmp_combo = QComboBox()
        cmp_combo.addItems(COMPARATORS)
        cmp_combo.setCurrentText(cmp)
        cmp_combo.setFixedWidth(110)
        cmp_combo.setFont(QFont("Segoe UI", 8))

        val_input = QLineEdit(val)
        val_input.setFixedWidth(70)
        val_input.setFont(QFont("Segoe UI", 8))

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(24)
        remove_btn.setFont(QFont("Segoe UI", 8))
        remove_btn.setStyleSheet("color: #ef5350;")
        remove_btn.setFlat(True)

        row_layout.addWidget(ind_combo)
        row_layout.addWidget(cmp_combo)
        row_layout.addWidget(val_input)
        row_layout.addWidget(remove_btn)
        row_layout.addStretch()

        row_data = {
            "ind": ind_combo,
            "cmp": cmp_combo,
            "val": val_input,
            "widget": row_widget,
        }
        self._condition_rows.append(row_data)

        # Insert before the trailing stretch in the layout
        count = self._conditions_layout.count()
        self._conditions_layout.insertWidget(count - 1, row_widget)

        remove_btn.clicked.connect(lambda: self._remove_condition(row_data))
        ind_combo.currentTextChanged.connect(self._update_preview)
        cmp_combo.currentTextChanged.connect(self._update_preview)
        val_input.textChanged.connect(self._update_preview)

        self._update_preview()

    def _remove_condition(self, row_data: dict) -> None:
        try:
            self._condition_rows.remove(row_data)
            row_data["widget"].deleteLater()
        except Exception:
            pass
        self._update_preview()

    def _update_preview(self, *_) -> None:
        if not self._condition_rows:
            self._preview_lbl.setText("— Add conditions above —")
            return
        parts = []
        for rd in self._condition_rows:
            parts.append(
                f"{rd['ind'].currentText()} {rd['cmp'].currentText()} {rd['val'].text()}"
            )
        symbol = self._symbol_combo.currentText()
        direction = self._direction_combo.currentText()
        self._preview_lbl.setText(f"[{symbol} {direction}]  {'  AND  '.join(parts)}")

    # ------------------------------------------------------------------
    # Load / Save / Delete
    # ------------------------------------------------------------------

    def _refresh_existing_strategies(self) -> None:
        try:
            from strategy.strategy_library_custom import list_custom_strategies
            rows = list_custom_strategies()
            names = [r["name"] if isinstance(r, dict) else str(r) for r in rows]
            self._load_combo.blockSignals(True)
            current = self._load_combo.currentText()
            self._load_combo.clear()
            self._load_combo.addItems(["— Select —"] + names)
            idx = self._load_combo.findText(current)
            self._load_combo.setCurrentIndex(max(0, idx))
            self._load_combo.blockSignals(False)
        except Exception as exc:
            log.debug("Refresh strategies: %s", exc)

    def _on_load_select(self, name: str) -> None:
        if name == "— Select —":
            return
        try:
            from strategy.strategy_library_custom import get_custom_strategy
            import json
            row = get_custom_strategy(name)
            if not row:
                return
            conditions_json = json.loads(row.get("conditions_json", "{}"))

            self._name_input.setText(name)
            self._symbol_combo.setCurrentText(conditions_json.get("symbol", "NIFTY"))
            self._direction_combo.setCurrentText(conditions_json.get("direction", "BULLISH"))

            self._clear_conditions()
            for cond in conditions_json.get("conditions", []):
                self._add_condition(
                    ind=cond.get("indicator", "rsi_14"),
                    cmp=cond.get("comparator", ">"),
                    val=str(cond.get("value", "50")),
                )
            self._editing_strategy = name
            self._status_lbl.setText(f"Loaded: {name}")
        except Exception as exc:
            log.error("Load strategy: %s", exc)
            self._status_lbl.setText(f"Error loading: {exc}")

    def _save_strategy(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            self._status_lbl.setText("Error: Strategy name required")
            return
        if not self._condition_rows:
            self._status_lbl.setText("Error: At least one condition required")
            return

        conditions = []
        for rd in self._condition_rows:
            ind = rd["ind"].currentText()
            cmp = rd["cmp"].currentText()
            val_raw = rd["val"].text()
            try:
                val: object = float(val_raw) if "." in val_raw else int(val_raw)
            except ValueError:
                val = val_raw
            conditions.append({"indicator": ind, "comparator": cmp, "value": val})

        try:
            from strategy.strategy_library_custom import save_custom_strategy
            config = {
                "name":       name,
                "symbol":     self._symbol_combo.currentText(),
                "direction":  self._direction_combo.currentText(),
                "conditions": conditions,
            }
            ok, reason = save_custom_strategy(config)
            if not ok:
                self._status_lbl.setText(f"Error: {reason}")
                return
            verb = "Updated" if self._editing_strategy == name else "Saved"
            self._status_lbl.setText(f"✓ {verb}: {name}")
            self._refresh_existing_strategies()
            log.info("Strategy saved: %s", name)
        except Exception as exc:
            self._status_lbl.setText(f"Error: {exc}")
            log.error("Save strategy: %s", exc)

    def _delete_strategy(self) -> None:
        name = self._load_combo.currentText()
        if name == "— Select —":
            return
        try:
            from strategy.strategy_library_custom import delete_custom_strategy
            delete_custom_strategy(name)
            self._clear_form()
            self._refresh_existing_strategies()
            self._status_lbl.setText(f"✓ Deleted: {name}")
        except Exception as exc:
            self._status_lbl.setText(f"Error: {exc}")

    def _clear_form(self) -> None:
        self._name_input.clear()
        self._symbol_combo.setCurrentText("NIFTY")
        self._direction_combo.setCurrentText("BULLISH")
        self._editing_strategy = None
        self._clear_conditions()
        self._status_lbl.setText("")
        self._load_combo.blockSignals(True)
        self._load_combo.setCurrentIndex(0)
        self._load_combo.blockSignals(False)

    def _clear_conditions(self) -> None:
        for rd in self._condition_rows:
            rd["widget"].deleteLater()
        self._condition_rows.clear()
        self._update_preview()
