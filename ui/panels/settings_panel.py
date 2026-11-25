"""
Settings panel for TradeDesk — PySide6 rewrite.

Thread: MAIN THREAD only.

7 tabs:
  1. Capital     — capital, risk%, paper trading gate
  2. Thresholds  — VIX-adjusted alert score thresholds (alert_score_config.json)
  3. Instruments — active instrument list
  4. Behaviors   — all 4 behavior thresholds (hot-reload, GAP 29)
  5. Alert Score — per-condition TF / points / enabled (NEW)
  6. SMC         — placeholder (Sprint 9)
  7. Display     — theme, chart candle count

Only Behaviors and Alert Score tabs write immediately.
Capital / Instruments / Display require restart (warning shown).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTabWidget, QComboBox, QLineEdit, QScrollArea,
    QGridLayout, QCheckBox, QSpinBox, QSizePolicy,
    QListWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

log = logging.getLogger(__name__)

CONFIG_PATH              = Path("config/config.json")
BEHAVIOR_THRESHOLDS_PATH = Path("config/behavior_thresholds.json")
ALERT_SCORE_CONFIG_PATH  = Path("config/alert_score_config.json")

_TF_OPTIONS = ["5m", "15m", "1h", "4h", "1d"]

_VIX_BAND_LABELS = {
    "low":      "VIX < 13 (Low)",
    "normal":   "VIX 13-18 (Normal)",
    "high":     "VIX > 18 (High)",
    "fallback": "Fallback",
}

_BEHAVIOR_SECTIONS = [
    ("behavior_1_opening_fake",          "Behavior 1 — Opening Fake"),
    ("behavior_2_institutional_defense", "Behavior 2 — Institutional Defense"),
    ("behavior_3_liquidity_sweep",       "Behavior 3 — Liquidity Sweep"),
    ("behavior_4_squeeze_breakout",      "Behavior 4 — Post-Squeeze Breakout"),
    ("indicator_confirmation",           "Indicator Confirmation"),
    ("pipeline_scoring",                 "Pipeline Scoring"),
    ("kelly",                            "Kelly Position Sizing"),
]

_VALID_SYMBOLS: set = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("_load_json %s: %s", path, exc)
        return {}


def _save_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------

def _lbl(text: str, size: int = 9, bold: bool = False,
         muted: bool = False, color: str = "") -> QLabel:
    w = QLabel(text)
    font = QFont("Segoe UI", size)
    font.setBold(bold)
    w.setFont(font)
    if color:
        w.setStyleSheet(f"color: {color};")
    elif muted:
        w.setStyleSheet("color: #888888;")
    return w


def _warn(parent_layout: QVBoxLayout, text: str, orange: bool = True) -> None:
    color = "#ffa726" if orange else "#26a69a"
    lbl = _lbl(f"⚠ {text}" if orange else f"✓ {text}", size=8, color=color)
    parent_layout.addWidget(lbl)


def _section_frame(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Return a framed section with a bold title label and inner layout."""
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(6, 4, 6, 4)
    layout.setSpacing(4)
    title_lbl = _lbl(title, size=8, bold=True)
    layout.addWidget(title_lbl)
    return frame, layout


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class SettingsPanel(QWidget):
    """Full settings panel with 7 tabs."""

    def __init__(
        self,
        parent: QWidget,
        shared_queue=None,
        on_close: Optional[Callable] = None,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self._shared_queue = shared_queue
        self._on_close = on_close

        self._cfg          = _load_json(CONFIG_PATH)
        self._behaviors    = _load_json(BEHAVIOR_THRESHOLDS_PATH)
        self._alert_config = _load_json(ALERT_SCORE_CONFIG_PATH)

        # Alert Score tab widget dicts (populated in _build_alert_score_tab)
        self._alert_cond_tf:       dict[str, QComboBox] = {}
        self._alert_cond_tf_flip:  dict[str, QComboBox] = {}
        self._alert_cond_tf_confirm: dict[str, QComboBox] = {}
        self._alert_cond_pts:      dict[str, QSpinBox]  = {}
        self._alert_cond_enabled:  dict[str, QCheckBox] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        self._notebook = QTabWidget()
        root.addWidget(self._notebook)

        for name, builder in [
            ("Capital",     self._build_capital_tab),
            ("Thresholds",  self._build_thresholds_tab),
            ("Instruments", self._build_instruments_tab),
            ("Behaviors",   self._build_behaviors_tab),
            ("Alert Score", self._build_alert_score_tab),
            ("SMC",         self._build_smc_tab),
            ("Display",     self._build_display_tab),
        ]:
            tab = QWidget()
            self._notebook.addTab(tab, name)
            builder(tab)

        # Close button row
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFont(QFont("Segoe UI", 9))
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self._on_close or (lambda: None))
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

    # ------------------------------------------------------------------
    # Tab 1 — Capital
    # ------------------------------------------------------------------

    def _build_capital_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignTop)

        _warn(layout, "Changes to Capital require app restart.")

        cap  = self._cfg.get("capital", {})
        risk = self._cfg.get("risk", {})

        layout.addWidget(_lbl("Total Capital (₹):", size=10))
        self._capital_input = QLineEdit(str(cap.get("total", 100000)))
        self._capital_input.setFont(QFont("Segoe UI", 10))
        self._capital_input.setFixedWidth(160)
        layout.addWidget(self._capital_input)

        layout.addWidget(_lbl("Paper trading gate — weeks:", size=10))
        self._paper_weeks_input = QLineEdit(str(cap.get("paper_trading_gate_weeks", 8)))
        self._paper_weeks_input.setFixedWidth(80)
        layout.addWidget(self._paper_weeks_input)

        layout.addWidget(_lbl("Paper trading gate — trades:", size=10))
        self._paper_trades_input = QLineEdit(str(cap.get("paper_trading_gate_trades", 100)))
        self._paper_trades_input.setFixedWidth(80)
        layout.addWidget(self._paper_trades_input)

        layout.addWidget(_lbl("Daily loss limit (%):", size=10))
        self._daily_loss_input = QLineEdit(str(risk.get("daily_loss_limit_pct", 10.0)))
        self._daily_loss_input.setFixedWidth(80)
        layout.addWidget(self._daily_loss_input)

        save_btn = QPushButton("Save Capital Settings")
        save_btn.setFont(QFont("Segoe UI", 9))
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._save_capital)
        layout.addWidget(save_btn)

        self._capital_status_lbl = QLabel("")
        self._capital_status_lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(self._capital_status_lbl)

        layout.addStretch()

    def _save_capital(self) -> None:
        try:
            cap     = float(self._capital_input.text().replace(",", "").replace("₹", ""))
            weeks   = int(self._paper_weeks_input.text())
            trades  = int(self._paper_trades_input.text())
            loss_pct = float(self._daily_loss_input.text())
            cfg = _load_json(CONFIG_PATH)
            cfg["capital"]["total"] = cap
            cfg["capital"]["paper_trading_gate_weeks"] = weeks
            cfg["capital"]["paper_trading_gate_trades"] = trades
            cfg["risk"]["daily_loss_limit_pct"] = loss_pct
            _save_json(CONFIG_PATH, cfg)
            self._capital_status_lbl.setText("✓ Saved — restart required")
            self._capital_status_lbl.setStyleSheet("color: #26a69a;")
            log.info("Capital settings saved")
        except Exception as exc:
            self._capital_status_lbl.setText(f"Error: {exc}")
            self._capital_status_lbl.setStyleSheet("color: #ef5350;")
            log.error("Capital save: %s", exc)

    # ------------------------------------------------------------------
    # Tab 2 — Thresholds (reads alert_score_config.json VIX bands)
    # ------------------------------------------------------------------

    def _build_thresholds_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        _warn(layout, "Changes apply immediately — no restart needed.", orange=False)

        vix_bands = self._alert_config.get("vix_adjusted_thresholds", {})
        self._threshold_inputs: dict[str, dict[str, QSpinBox]] = {}

        for band_key, band_label in _VIX_BAND_LABELS.items():
            frame, inner = _section_frame(band_label)
            band_cfg = vix_bands.get(band_key, {})
            row_layout = QHBoxLayout()
            row_layout.setSpacing(16)
            self._threshold_inputs[band_key] = {}
            for field in ("soft", "strong", "max"):
                cell = QVBoxLayout()
                cell.addWidget(_lbl(f"{field.capitalize()}:", size=8))
                spin = QSpinBox()
                spin.setRange(0, 25)
                spin.setValue(int(band_cfg.get(field, 0)))
                spin.setFixedWidth(60)
                cell.addWidget(spin)
                row_layout.addLayout(cell)
                self._threshold_inputs[band_key][field] = spin
            row_layout.addStretch()
            inner.addLayout(row_layout)
            layout.addWidget(frame)

        save_btn = QPushButton("Save Thresholds")
        save_btn.setFont(QFont("Segoe UI", 9))
        save_btn.setFixedWidth(160)
        save_btn.clicked.connect(self._save_thresholds)
        layout.addWidget(save_btn)

        self._thresh_status_lbl = QLabel("")
        self._thresh_status_lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(self._thresh_status_lbl)

        layout.addStretch()

    def _save_thresholds(self) -> None:
        try:
            cfg = _load_json(ALERT_SCORE_CONFIG_PATH)
            for band_key, fields in self._threshold_inputs.items():
                for field, spin in fields.items():
                    cfg["vix_adjusted_thresholds"][band_key][field] = spin.value()
            _save_json(ALERT_SCORE_CONFIG_PATH, cfg)
            self._thresh_status_lbl.setText("✓ Saved — effective immediately")
            self._thresh_status_lbl.setStyleSheet("color: #26a69a;")
        except Exception as exc:
            self._thresh_status_lbl.setText(f"Error: {exc}")
            self._thresh_status_lbl.setStyleSheet("color: #ef5350;")
            log.error("Threshold save: %s", exc)

    # ------------------------------------------------------------------
    # Tab 3 — Instruments
    # ------------------------------------------------------------------

    def _build_instruments_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignTop)

        _warn(layout, "Changes apply immediately to the instrument selector.")

        layout.addWidget(_lbl("Active instruments:", size=10))

        list_row = QHBoxLayout()

        self._instrument_list = QListWidget()
        self._instrument_list.setFont(QFont("Segoe UI", 9))
        self._instrument_list.setFixedHeight(140)
        self._instrument_list.setFixedWidth(180)
        list_row.addWidget(self._instrument_list)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        add_btn = QPushButton("+ Add")
        add_btn.setFont(QFont("Segoe UI", 8))
        add_btn.clicked.connect(self._add_instrument)
        btn_col.addWidget(add_btn)

        remove_btn = QPushButton("– Remove")
        remove_btn.setFont(QFont("Segoe UI", 8))
        remove_btn.setStyleSheet("color: #ef5350;")
        remove_btn.clicked.connect(self._remove_instrument)
        btn_col.addWidget(remove_btn)
        btn_col.addStretch()
        list_row.addLayout(btn_col)
        list_row.addStretch()
        layout.addLayout(list_row)

        add_row = QHBoxLayout()
        add_row.addWidget(_lbl("Symbol:", size=9))
        self._new_symbol_input = QLineEdit()
        self._new_symbol_input.setFixedWidth(100)
        self._new_symbol_input.setPlaceholderText("e.g. NIFTY")
        add_row.addWidget(self._new_symbol_input)
        add_row.addStretch()
        layout.addLayout(add_row)

        self._instr_status_lbl = QLabel("")
        self._instr_status_lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(self._instr_status_lbl)

        layout.addStretch()
        self._refresh_instrument_list()

    def _refresh_instrument_list(self) -> None:
        cfg = _load_json(CONFIG_PATH)
        active = cfg.get("instruments", {}).get("active", [])
        self._refresh_instrument_list_from_cfg(active)

    def _refresh_instrument_list_from_cfg(self, active: list) -> None:
        self._instrument_list.clear()
        for sym in active:
            self._instrument_list.addItem(sym)

    def _add_instrument(self) -> None:
        sym = self._new_symbol_input.text().strip().upper()
        if not sym:
            return
        if sym not in _VALID_SYMBOLS:
            self._instr_status_lbl.setText(
                f"✖ '{sym}' not supported. Valid: {', '.join(sorted(_VALID_SYMBOLS))}"
            )
            self._instr_status_lbl.setStyleSheet("color: #ef5350;")
            return
        cfg = _load_json(CONFIG_PATH)
        active = cfg.get("instruments", {}).get("active", [])
        if sym in active:
            self._instr_status_lbl.setText(f"'{sym}' already in list.")
            self._instr_status_lbl.setStyleSheet("color: #888888;")
        else:
            active.append(sym)
            cfg.setdefault("instruments", {})["active"] = active
            _save_json(CONFIG_PATH, cfg)
            self._refresh_instrument_list_from_cfg(active)
            self._instr_status_lbl.setText(f"✓ Added {sym}")
            self._instr_status_lbl.setStyleSheet("color: #26a69a;")
            if self._shared_queue:
                try:
                    self._shared_queue.put_nowait(
                        {"type": "instruments_changed", "data": {"active": active}}
                    )
                except Exception:
                    pass
        self._new_symbol_input.clear()

    def _remove_instrument(self) -> None:
        item = self._instrument_list.currentItem()
        if not item:
            return
        sym = item.text()
        cfg = _load_json(CONFIG_PATH)
        active = cfg.get("instruments", {}).get("active", [])
        if sym in active:
            active.remove(sym)
            cfg.setdefault("instruments", {})["active"] = active
            _save_json(CONFIG_PATH, cfg)
            self._refresh_instrument_list_from_cfg(active)
            self._instr_status_lbl.setText(f"✓ Removed {sym}")
            self._instr_status_lbl.setStyleSheet("color: #26a69a;")
            if self._shared_queue:
                try:
                    self._shared_queue.put_nowait(
                        {"type": "instruments_changed", "data": {"active": active}}
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Tab 4 — Behaviors (HOT-RELOAD — GAP 29)
    # ------------------------------------------------------------------

    def _build_behaviors_tab(self, parent: QWidget) -> None:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        outer_layout = QVBoxLayout(parent)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(inner)

        hot_lbl = _lbl(
            "✓ Save writes behavior_thresholds.json and reloads WITHOUT restart",
            size=8, color="#26a69a",
        )
        layout.addWidget(hot_lbl)

        behaviors = _load_json(BEHAVIOR_THRESHOLDS_PATH)
        self._behavior_inputs: dict[str, dict[str, tuple]] = {}

        for key, label in _BEHAVIOR_SECTIONS:
            section_data = behaviors.get(key, {})
            if not section_data:
                continue

            frame, inner_layout = _section_frame(label)
            grid = QGridLayout()
            grid.setSpacing(4)
            inner_layout.addLayout(grid)

            self._behavior_inputs[key] = {}
            for col_offset, (field, value) in enumerate(section_data.items()):
                row = col_offset // 3
                col = (col_offset % 3) * 2

                field_lbl = _lbl(field + ":", size=8, muted=True)
                field_lbl.setFixedWidth(200)
                grid.addWidget(field_lbl, row, col, Qt.AlignLeft)

                if isinstance(value, bool):
                    widget: QWidget = QComboBox()
                    widget.addItems(["True", "False"])
                    widget.setCurrentText(str(value))
                    widget.setFixedWidth(80)
                else:
                    widget = QLineEdit(str(value))
                    widget.setFixedWidth(80)

                grid.addWidget(widget, row, col + 1, Qt.AlignLeft)
                self._behavior_inputs[key][field] = (widget, type(value))

            layout.addWidget(frame)

        save_btn = QPushButton("Save & Hot-Reload Behaviors")
        save_btn.setFont(QFont("Segoe UI", 9))
        save_btn.setFixedWidth(220)
        save_btn.setStyleSheet("background-color: #26a69a; color: white; padding: 4px 8px;")
        save_btn.clicked.connect(self._save_behaviors)
        layout.addWidget(save_btn)

        self._behaviors_status_lbl = QLabel("")
        self._behaviors_status_lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(self._behaviors_status_lbl)

        layout.addStretch()

    def _save_behaviors(self) -> None:
        try:
            behaviors = _load_json(BEHAVIOR_THRESHOLDS_PATH)
            for section_key, fields in self._behavior_inputs.items():
                behaviors.setdefault(section_key, {})
                for field, (widget, orig_type) in fields.items():
                    raw = (widget.currentText()
                           if isinstance(widget, QComboBox)
                           else widget.text())
                    if orig_type is bool:
                        behaviors[section_key][field] = raw.lower() == "true"
                    elif orig_type is int:
                        behaviors[section_key][field] = int(float(raw))
                    elif orig_type is float:
                        behaviors[section_key][field] = float(raw)
                    else:
                        behaviors[section_key][field] = raw

            _save_json(BEHAVIOR_THRESHOLDS_PATH, behaviors)

            try:
                from strategy.behavior_library import reload_thresholds
                reload_thresholds()
                self._behaviors_status_lbl.setText(
                    "✓ Saved and hot-reloaded — no restart needed"
                )
            except Exception as exc:
                self._behaviors_status_lbl.setText(f"✓ Saved — hot-reload failed: {exc}")
            self._behaviors_status_lbl.setStyleSheet("color: #26a69a;")
            log.info("Behavior thresholds saved and reloaded")
        except Exception as exc:
            self._behaviors_status_lbl.setText(f"Error: {exc}")
            self._behaviors_status_lbl.setStyleSheet("color: #ef5350;")
            log.error("Behavior save: %s", exc)

    # ------------------------------------------------------------------
    # Tab 5 — Alert Score (NEW)
    # ------------------------------------------------------------------

    def _build_alert_score_tab(self, parent: QWidget) -> None:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        outer_layout = QVBoxLayout(parent)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(inner)

        layout.addWidget(
            _lbl("✓ Save writes alert_score_config.json — effective immediately",
                 size=8, color="#26a69a")
        )

        # Cooldown trigger level
        cooldown_row = QHBoxLayout()
        cooldown_row.addWidget(_lbl("Cooldown trigger level:", size=9, bold=True))
        self._cooldown_combo = QComboBox()
        self._cooldown_combo.addItems(["MAX", "STRONG"])
        self._cooldown_combo.setCurrentText(
            self._alert_config.get("cooldown_trigger_level", "MAX")
        )
        self._cooldown_combo.setFixedWidth(100)
        cooldown_row.addWidget(self._cooldown_combo)
        cooldown_row.addStretch()
        layout.addLayout(cooldown_row)

        layout.addWidget(
            _lbl("12 Conditions — configure timeframe, points, and enable/disable:",
                 size=8, bold=True)
        )

        for cond_key, cond_data in self._alert_config.get("conditions", {}).items():
            self._build_condition_row(layout, cond_key, cond_data)

        save_btn = QPushButton("Save Alert Score Config")
        save_btn.setFont(QFont("Segoe UI", 9))
        save_btn.setFixedWidth(200)
        save_btn.setStyleSheet("background-color: #26a69a; color: white; padding: 4px 8px;")
        save_btn.clicked.connect(self._save_alert_score)
        layout.addWidget(save_btn)

        self._alert_score_status_lbl = QLabel("")
        self._alert_score_status_lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(self._alert_score_status_lbl)

        layout.addStretch()

    def _build_condition_row(
        self,
        parent_layout: QVBoxLayout,
        cond_key: str,
        cond_data: dict,
    ) -> None:
        row_frame = QFrame()
        row_frame.setFixedHeight(44)
        row_layout = QHBoxLayout(row_frame)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(8)

        # Condition name
        name_lbl = _lbl(cond_key, size=9, bold=True)
        name_lbl.setFixedWidth(160)
        row_layout.addWidget(name_lbl)

        if cond_key == "supertrend_flip":
            # Flip TF
            row_layout.addWidget(_lbl("Flip TF:", size=8))
            tf_flip_combo = QComboBox()
            tf_flip_combo.addItems(_TF_OPTIONS)
            tf_flip_combo.setCurrentText(cond_data.get("timeframe_flip", "15m"))
            tf_flip_combo.setFixedWidth(60)
            row_layout.addWidget(tf_flip_combo)
            self._alert_cond_tf_flip[cond_key] = tf_flip_combo
            # Store in _alert_cond_tf so the save guard passes
            self._alert_cond_tf[cond_key] = tf_flip_combo

            # Confirm TF
            row_layout.addWidget(_lbl("Confirm TF:", size=8))
            tf_confirm_combo = QComboBox()
            tf_confirm_combo.addItems(_TF_OPTIONS)
            tf_confirm_combo.setCurrentText(cond_data.get("timeframe_confirm", "1h"))
            tf_confirm_combo.setFixedWidth(60)
            row_layout.addWidget(tf_confirm_combo)
            self._alert_cond_tf_confirm[cond_key] = tf_confirm_combo
        else:
            row_layout.addWidget(_lbl("TF:", size=8))
            tf_combo = QComboBox()
            tf_combo.addItems(_TF_OPTIONS)
            tf_combo.setCurrentText(cond_data.get("timeframe", "5m"))
            tf_combo.setFixedWidth(60)
            row_layout.addWidget(tf_combo)
            self._alert_cond_tf[cond_key] = tf_combo

        # Points
        row_layout.addWidget(_lbl("Points:", size=8))
        pts_spin = QSpinBox()
        pts_spin.setRange(0, 21)
        pts_spin.setValue(cond_data.get("points", 1))
        pts_spin.setFixedWidth(55)
        row_layout.addWidget(pts_spin)
        self._alert_cond_pts[cond_key] = pts_spin

        # Enabled
        enabled_chk = QCheckBox("Enabled")
        enabled_chk.setFont(QFont("Segoe UI", 8))
        enabled_chk.setChecked(cond_data.get("enabled", True))
        row_layout.addWidget(enabled_chk)
        self._alert_cond_enabled[cond_key] = enabled_chk

        # Description (muted)
        desc = cond_data.get("description", "")
        if desc:
            desc_lbl = _lbl(desc, size=7, muted=True)
            desc_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row_layout.addWidget(desc_lbl)

        row_layout.addStretch()
        parent_layout.addWidget(row_frame)

    def _save_alert_score(self) -> None:
        try:
            cfg = _load_json(ALERT_SCORE_CONFIG_PATH)
            cfg["cooldown_trigger_level"] = self._cooldown_combo.currentText()

            for cond_key in cfg["conditions"]:
                if cond_key not in self._alert_cond_tf:
                    continue
                if cond_key == "supertrend_flip":
                    if hasattr(self, "_alert_cond_tf_flip"):
                        cfg["conditions"][cond_key]["timeframe_flip"] = (
                            self._alert_cond_tf_flip.get(cond_key, QComboBox()).currentText()
                        )
                    if hasattr(self, "_alert_cond_tf_confirm"):
                        cfg["conditions"][cond_key]["timeframe_confirm"] = (
                            self._alert_cond_tf_confirm.get(cond_key, QComboBox()).currentText()
                        )
                else:
                    cfg["conditions"][cond_key]["timeframe"] = (
                        self._alert_cond_tf[cond_key].currentText()
                    )
                cfg["conditions"][cond_key]["points"] = (
                    self._alert_cond_pts[cond_key].value()
                )
                cfg["conditions"][cond_key]["enabled"] = (
                    self._alert_cond_enabled[cond_key].isChecked()
                )

            _save_json(ALERT_SCORE_CONFIG_PATH, cfg)
            self._alert_score_status_lbl.setText("✓ Saved — effective immediately")
            self._alert_score_status_lbl.setStyleSheet("color: #26a69a;")
            log.info("Alert score config saved")
        except Exception as exc:
            self._alert_score_status_lbl.setText(f"Error: {exc}")
            self._alert_score_status_lbl.setStyleSheet("color: #ef5350;")
            log.error("Alert score save: %s", exc)

    # ------------------------------------------------------------------
    # Tab 6 — SMC (placeholder)
    # ------------------------------------------------------------------

    def _build_smc_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 12, 16, 12)
        lbl = QLabel("SMC Thresholds\n(Sprint 9 — coming soon)")
        lbl.setFont(QFont("Segoe UI", 12))
        lbl.setStyleSheet("color: #888888;")
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)

    # ------------------------------------------------------------------
    # Tab 7 — Display
    # ------------------------------------------------------------------

    def _build_display_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignTop)

        _warn(layout, "Display changes require app restart.")

        disp = self._cfg.get("display", {})

        layout.addWidget(_lbl("Theme:", size=10))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(
            ["dark_teal", "dark_blue", "dark_cyan", "dark_amber"]
        )
        self._theme_combo.setCurrentText(disp.get("theme", "dark_teal"))
        self._theme_combo.setFont(QFont("Segoe UI", 9))
        self._theme_combo.setFixedWidth(140)
        layout.addWidget(self._theme_combo)

        layout.addWidget(_lbl("Candles on chart:", size=10))
        self._candles_combo = QComboBox()
        self._candles_combo.addItems(["20", "50", "80", "100", "150"])
        self._candles_combo.setCurrentText("80")
        self._candles_combo.setFont(QFont("Segoe UI", 9))
        self._candles_combo.setFixedWidth(80)
        layout.addWidget(self._candles_combo)

        save_btn = QPushButton("Save Display Settings")
        save_btn.setFont(QFont("Segoe UI", 9))
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._save_display)
        layout.addWidget(save_btn)

        self._display_status_lbl = QLabel("")
        self._display_status_lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(self._display_status_lbl)

        layout.addStretch()

    def _save_display(self) -> None:
        try:
            cfg = _load_json(CONFIG_PATH)
            cfg.setdefault("display", {})["theme"] = self._theme_combo.currentText()
            cfg["display"]["chart_candles"] = int(self._candles_combo.currentText())
            _save_json(CONFIG_PATH, cfg)
            self._display_status_lbl.setText("✓ Saved — restart required")
            self._display_status_lbl.setStyleSheet("color: #26a69a;")
        except Exception as exc:
            self._display_status_lbl.setText(f"Error: {exc}")
            self._display_status_lbl.setStyleSheet("color: #ef5350;")
