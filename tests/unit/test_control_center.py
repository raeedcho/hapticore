"""Unit tests for the Hapticore Control Center.

Tests cover:
- _populate_tree helper function
- ConfigPanel validation logic
- CLI subcommand registration

Does NOT test Qt rendering or simulate mouse/key events.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication, QComboBox, QTreeWidget  # noqa: E402

from hapticore.control.config_panel import ConfigPanel, _populate_tree  # noqa: E402


# ---------------------------------------------------------------------------
# Session-scoped QApplication fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide a single QApplication for all tests (required by Qt widgets)."""
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------------------
# _populate_tree helper
# ---------------------------------------------------------------------------


class TestPopulateTree:
    def test_flat_dict_produces_leaf_items(self, qapp: QApplication) -> None:
        """Flat dict → top-level items with key in column 0, value in column 1."""
        tree = QTreeWidget()
        tree.setColumnCount(2)
        _populate_tree(tree, {"name": "monkey", "age": 5})
        assert tree.topLevelItemCount() == 2
        keys = {tree.topLevelItem(i).text(0) for i in range(2)}
        assert keys == {"name", "age"}
        values = {tree.topLevelItem(i).text(1) for i in range(2)}
        assert values == {"monkey", "5"}

    def test_nested_dict_produces_parent_and_children(self, qapp: QApplication) -> None:
        """Nested dict → parent item with child items."""
        tree = QTreeWidget()
        tree.setColumnCount(2)
        _populate_tree(tree, {"subject": {"subject_id": "monkey1", "species": "macaque"}})
        assert tree.topLevelItemCount() == 1
        parent = tree.topLevelItem(0)
        assert parent.text(0) == "subject"
        assert parent.childCount() == 2
        child_keys = {parent.child(i).text(0) for i in range(2)}
        assert child_keys == {"subject_id", "species"}

    def test_list_produces_indexed_children(self, qapp: QApplication) -> None:
        """List value → parent with [0], [1], ... children."""
        tree = QTreeWidget()
        tree.setColumnCount(2)
        _populate_tree(tree, {"items": ["a", "b", "c"]})
        assert tree.topLevelItemCount() == 1
        parent = tree.topLevelItem(0)
        assert parent.text(0) == "items"
        assert parent.childCount() == 3
        assert parent.child(0).text(0) == "[0]"
        assert parent.child(0).text(1) == "a"
        assert parent.child(2).text(0) == "[2]"
        assert parent.child(2).text(1) == "c"


# ---------------------------------------------------------------------------
# ConfigPanel validation
# ---------------------------------------------------------------------------

# Path to the repo's configs/ directory (relative to where tests are run)
_CONFIGS_ROOT = Path("configs")


def _find_combo_index(combo: QComboBox, filename: str) -> int:
    """Return the combo index whose item data path has the given filename."""
    from PyQt6.QtCore import Qt

    for i in range(combo.count()):
        data = combo.itemData(i, Qt.ItemDataRole.UserRole)
        if data is not None and Path(data).name == filename:
            return i
    return -1


class TestConfigPanelValidation:
    def _make_panel(self) -> ConfigPanel:
        return ConfigPanel(configs_root=_CONFIGS_ROOT)

    def _select_valid_configs(self, panel: ConfigPanel) -> None:
        """Programmatically select ci.yaml, example_subject.yaml, center_out.yaml + experiment."""
        rig_idx = _find_combo_index(panel._rig_combo, "ci.yaml")
        subject_idx = _find_combo_index(panel._subject_combo, "example_subject.yaml")
        task_idx = _find_combo_index(panel._task_combo, "center_out.yaml")
        assert rig_idx != -1, "ci.yaml not found in rig combo"
        assert subject_idx != -1, "example_subject.yaml not found in subject combo"
        assert task_idx != -1, "center_out.yaml not found in task combo"
        panel._rig_combo.setCurrentIndex(rig_idx)
        panel._subject_combo.setCurrentIndex(subject_idx)
        panel._task_combo.setCurrentIndex(task_idx)
        # experiment_name is required and not provided by rig/subject/task configs
        extra_path = _CONFIGS_ROOT / "example_experiment.yaml"
        panel._extra_combo.addItem(extra_path.name, extra_path)
        panel._extra_combo.setCurrentIndex(panel._extra_combo.count() - 1)

    def test_validate_succeeds_with_valid_configs(self, qapp: QApplication) -> None:
        """Programmatically select valid YAML files, call _on_validate(), assert config is set."""
        panel = self._make_panel()
        self._select_valid_configs(panel)
        panel._on_validate()
        assert panel.validated_config is not None
        assert isinstance(panel.validated_config.experiment_name, str)
        assert panel.validated_config.experiment_name != ""

    def test_validate_fails_with_missing_selection(self, qapp: QApplication) -> None:
        """Leave rig combo on placeholder, call _on_validate(), assert validated_config is None."""
        panel = self._make_panel()
        # Leave rig on placeholder (index 0)
        subject_idx = _find_combo_index(panel._subject_combo, "example_subject.yaml")
        task_idx = _find_combo_index(panel._task_combo, "center_out.yaml")
        panel._subject_combo.setCurrentIndex(subject_idx)
        panel._task_combo.setCurrentIndex(task_idx)
        panel._on_validate()
        assert panel.validated_config is None
        # Tree should show an error item
        assert panel._config_tree.topLevelItemCount() == 1
        assert panel._config_tree.topLevelItem(0).text(0) == "Error"

    def test_validate_emits_signal_on_success(self, qapp: QApplication) -> None:
        """Connect a list.append to config_validated, validate, assert list is non-empty."""
        panel = self._make_panel()
        self._select_valid_configs(panel)
        captured: list[object] = []
        panel.config_validated.connect(captured.append)
        panel._on_validate()
        assert len(captured) == 1
        from hapticore.core.config import ExperimentConfig
        assert isinstance(captured[0], ExperimentConfig)

    def test_set_editable_disables_combos(self, qapp: QApplication) -> None:
        """Call set_editable(False), assert all combos and buttons are disabled."""
        panel = self._make_panel()
        panel.set_editable(False)
        assert not panel._rig_combo.isEnabled()
        assert not panel._subject_combo.isEnabled()
        assert not panel._task_combo.isEnabled()
        assert not panel._extra_combo.isEnabled()
        assert not panel._rig_browse_btn.isEnabled()
        assert not panel._subject_browse_btn.isEnabled()
        assert not panel._task_browse_btn.isEnabled()
        assert not panel._extra_browse_btn.isEnabled()
        assert not panel._validate_btn.isEnabled()
        # start_session_btn is NOT affected by set_editable
        # (it has its own enable/disable logic in CC-C.2)

    def test_set_editable_reenables_combos(self, qapp: QApplication) -> None:
        """Call set_editable(False) then set_editable(True), assert controls are enabled."""
        panel = self._make_panel()
        panel.set_editable(False)
        panel.set_editable(True)
        assert panel._rig_combo.isEnabled()
        assert panel._subject_combo.isEnabled()
        assert panel._task_combo.isEnabled()
        assert panel._extra_combo.isEnabled()
        assert panel._validate_btn.isEnabled()


# ---------------------------------------------------------------------------
# CLI subcommand registration
# ---------------------------------------------------------------------------


class TestControlCenterCLI:
    def test_gui_subcommand_registered(self) -> None:
        """Verify the gui subcommand is importable and callable."""
        from hapticore.cli import _gui
        assert callable(_gui)
