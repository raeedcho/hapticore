"""Configuration panel widget for the Hapticore Control Center."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hapticore.core.config import ExperimentConfig, load_session_config


def _populate_tree(
    tree: QTreeWidget,
    data: dict[str, Any],
    parent: QTreeWidgetItem | None = None,
) -> None:
    """Recursively populate a QTreeWidget from a nested dict."""
    for key, value in data.items():
        if isinstance(value, dict):
            item = QTreeWidgetItem([str(key), ""])
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            _populate_tree(tree, value, item)
        elif isinstance(value, list):
            item = QTreeWidgetItem([str(key), ""])
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            for idx, element in enumerate(value):
                if isinstance(element, dict):
                    child = QTreeWidgetItem([f"[{idx}]", ""])
                    item.addChild(child)
                    _populate_tree(tree, element, child)
                else:
                    child = QTreeWidgetItem([f"[{idx}]", str(element)])
                    item.addChild(child)
        else:
            item = QTreeWidgetItem([str(key), str(value)])
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)


class ConfigPanel(QWidget):
    """Widget for selecting and validating experiment config files."""

    config_validated = pyqtSignal(object)

    def __init__(
        self,
        configs_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._configs_root = configs_root or Path("configs")
        self._validated_config: ExperimentConfig | None = None

        layout = QVBoxLayout(self)

        # Rig row
        rig_row = QHBoxLayout()
        rig_row.addWidget(QLabel("Rig:"))
        self._rig_combo = self._make_combo("rig")
        rig_row.addWidget(self._rig_combo)
        self._rig_browse_btn = QPushButton("…")
        self._rig_browse_btn.clicked.connect(
            lambda: self._browse("rig", self._rig_combo)
        )
        rig_row.addWidget(self._rig_browse_btn)
        layout.addLayout(rig_row)

        # Subject row
        subject_row = QHBoxLayout()
        subject_row.addWidget(QLabel("Subject:"))
        self._subject_combo = self._make_combo("subject")
        subject_row.addWidget(self._subject_combo)
        self._subject_browse_btn = QPushButton("…")
        self._subject_browse_btn.clicked.connect(
            lambda: self._browse("subject", self._subject_combo)
        )
        subject_row.addWidget(self._subject_browse_btn)
        layout.addLayout(subject_row)

        # Task row
        task_row = QHBoxLayout()
        task_row.addWidget(QLabel("Task:"))
        self._task_combo = self._make_combo("task")
        task_row.addWidget(self._task_combo)
        self._task_browse_btn = QPushButton("…")
        self._task_browse_btn.clicked.connect(
            lambda: self._browse("task", self._task_combo)
        )
        task_row.addWidget(self._task_browse_btn)
        layout.addLayout(task_row)

        # Extra row
        extra_row = QHBoxLayout()
        extra_row.addWidget(QLabel("Extra:"))
        self._extra_combo = QComboBox()
        self._extra_combo.addItem("(none)", None)
        extra_row.addWidget(self._extra_combo)
        self._extra_browse_btn = QPushButton("…")
        self._extra_browse_btn.clicked.connect(self._browse_extra)
        extra_row.addWidget(self._extra_browse_btn)
        layout.addLayout(extra_row)

        # Button row
        btn_row = QHBoxLayout()
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.clicked.connect(self._on_validate)
        btn_row.addWidget(self._validate_btn)
        self.start_session_btn = QPushButton("Start Session")
        self.start_session_btn.setEnabled(False)
        btn_row.addWidget(self.start_session_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Config tree (read-only display)
        self._config_tree = QTreeWidget()
        self._config_tree.setColumnCount(2)
        self._config_tree.setHeaderLabels(["Key", "Value"])
        layout.addWidget(self._config_tree)

    def _make_combo(self, subdir: str) -> QComboBox:
        """Create a combo box pre-populated with YAML files from configs_root/subdir."""
        combo = QComboBox()
        combo.addItem("(select…)", None)
        scan_dir = self._configs_root / subdir
        if scan_dir.is_dir():
            yaml_files = sorted(
                p for p in scan_dir.iterdir() if p.suffix in (".yaml", ".yml")
            )
            for path in yaml_files:
                combo.addItem(path.name, path)
        return combo

    def _browse(self, subdir: str, combo: QComboBox) -> None:
        """Open a file dialog and add the selected file to the combo box."""
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {subdir} config",
            str(self._configs_root / subdir),
            "YAML files (*.yaml *.yml)",
        )
        if not path_str:
            return
        path = Path(path_str)
        for i in range(combo.count()):
            if combo.itemData(i, Qt.ItemDataRole.UserRole) == path:
                combo.setCurrentIndex(i)
                return
        combo.addItem(path.name, path)
        combo.setCurrentIndex(combo.count() - 1)

    def _browse_extra(self) -> None:
        """Open a file dialog and add an extra config file."""
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select extra config",
            str(self._configs_root),
            "YAML files (*.yaml *.yml)",
        )
        if not path_str:
            return
        path = Path(path_str)
        for i in range(self._extra_combo.count()):
            if self._extra_combo.itemData(i, Qt.ItemDataRole.UserRole) == path:
                self._extra_combo.setCurrentIndex(i)
                return
        self._extra_combo.addItem(path.name, path)
        self._extra_combo.setCurrentIndex(self._extra_combo.count() - 1)

    def _on_validate(self) -> None:
        """Validate the selected config files and populate the tree widget."""
        self._config_tree.clear()

        rig_path: Path | None = self._rig_combo.currentData(Qt.ItemDataRole.UserRole)
        subject_path: Path | None = self._subject_combo.currentData(
            Qt.ItemDataRole.UserRole
        )
        task_path: Path | None = self._task_combo.currentData(Qt.ItemDataRole.UserRole)
        extra_path: Path | None = self._extra_combo.currentData(
            Qt.ItemDataRole.UserRole
        )

        if rig_path is None or subject_path is None or task_path is None:
            error_item = QTreeWidgetItem(
                ["Error", "Please select rig, subject, and task config files."]
            )
            self._config_tree.addTopLevelItem(error_item)
            self._validated_config = None
            return

        try:
            config = load_session_config(
                rig=rig_path,
                subject=subject_path,
                task=task_path,
                extra=[extra_path] if extra_path is not None else [],
            )
        except Exception as exc:  # noqa: BLE001
            error_item = QTreeWidgetItem([type(exc).__name__, str(exc)])
            self._config_tree.addTopLevelItem(error_item)
            self._validated_config = None
            return

        _populate_tree(self._config_tree, config.model_dump())
        self._validated_config = config
        self.config_validated.emit(config)

    @property
    def validated_config(self) -> ExperimentConfig | None:
        """The last successfully validated config, or None."""
        return self._validated_config

    def set_editable(self, editable: bool) -> None:
        """Enable or disable all interactive config controls."""
        for combo in (
            self._rig_combo,
            self._subject_combo,
            self._task_combo,
            self._extra_combo,
        ):
            combo.setEnabled(editable)
        for btn in (
            self._rig_browse_btn,
            self._subject_browse_btn,
            self._task_browse_btn,
            self._extra_browse_btn,
            self._validate_btn,
        ):
            btn.setEnabled(editable)
