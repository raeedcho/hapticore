"""Tests for mock hardware implementations and protocol compliance."""

from __future__ import annotations

from hapticore.core.interfaces import (
    DisplayInterface,
    HapticInterface,
    NeuralRecordingInterface,
    SyncInterface,
)
from hapticore.core.messages import Command
from hapticore.backends.mock import (
    MockDisplay,
    MockHapticInterface,
    MockNeuralRecording,
    MockSync,
)


class TestProtocolCompliance:
    """Verify each mock satisfies its Protocol via isinstance check."""

    def test_haptic_interface(self) -> None:
        mock = MockHapticInterface()
        assert isinstance(mock, HapticInterface)

    def test_neural_recording_interface(self) -> None:
        mock = MockNeuralRecording()
        assert isinstance(mock, NeuralRecordingInterface)

    def test_sync_interface(self) -> None:
        mock = MockSync()
        assert isinstance(mock, SyncInterface)

    def test_display_interface(self) -> None:
        mock = MockDisplay()
        assert isinstance(mock, DisplayInterface)


class TestMockHaptic:
    """Tests for MockHapticInterface behavior."""

    def test_get_latest_state(self) -> None:
        mock = MockHapticInterface(initial_position=[0.1, 0.2, 0.3])
        state = mock.get_latest_state()
        assert state is not None
        assert state.position == [0.1, 0.2, 0.3]
        assert state.sequence == 1

    def test_sequence_increments(self) -> None:
        mock = MockHapticInterface()
        s1 = mock.get_latest_state()
        s2 = mock.get_latest_state()
        assert s1 is not None and s2 is not None
        assert s2.sequence == s1.sequence + 1

    def test_send_command_logs(self) -> None:
        mock = MockHapticInterface()
        cmd = Command(command_id="test", method="set_field", params={"type": "spring"})
        resp = mock.send_command(cmd)
        assert resp.success is True
        assert len(mock._command_log) == 1
        assert mock._command_log[0].method == "set_field"

    def test_subscribe_unsubscribe(self) -> None:
        mock = MockHapticInterface()
        callback_called = False

        def cb(state: object) -> None:
            nonlocal callback_called
            callback_called = True

        mock.subscribe_state(cb)
        assert mock._callback is not None
        mock.unsubscribe_state()
        assert mock._callback is None


class TestMockNeuralRecording:
    def test_recording_lifecycle(self) -> None:
        mock = MockNeuralRecording()
        assert not mock.is_recording()
        mock.start_recording("test_file.ns5")
        assert mock.is_recording()
        mock.stop_recording()
        assert not mock.is_recording()

    def test_timestamp(self) -> None:
        mock = MockNeuralRecording()
        assert mock.get_timestamp() == 0.0
        mock.start_recording("test")
        ts = mock.get_timestamp()
        assert ts >= 0.0


class TestMockSync:
    def test_sync_lifecycle(self) -> None:
        mock = MockSync()
        assert not mock.is_sync_running()
        mock.start_sync_pulses()
        assert mock.is_sync_running()
        mock.stop_sync_pulses()
        assert not mock.is_sync_running()

    def test_event_codes(self) -> None:
        mock = MockSync()
        mock.send_event_code(42)
        mock.send_event_code(99)
        assert mock._event_codes == [42, 99]

    def test_reward_delivery(self) -> None:
        mock = MockSync()
        mock.deliver_reward(100)
        mock.deliver_reward(150)
        assert mock._reward_durations_ms == [100, 150]
        assert mock._call_log[-2:] == [
            ("deliver_reward", 100),
            ("deliver_reward", 150),
        ]

    def test_camera_trigger_lifecycle(self) -> None:
        mock = MockSync()
        assert not mock.is_camera_trigger_running()
        mock.set_camera_trigger_rate(60.0)
        assert mock._camera_trigger_rate_hz == 60.0
        mock.start_camera_trigger()
        assert mock.is_camera_trigger_running()
        mock.stop_camera_trigger()
        assert not mock.is_camera_trigger_running()

    def test_camera_trigger_independent_from_sync_pulse(self) -> None:
        """Sync pulse and camera trigger track independently."""
        mock = MockSync()
        mock.start_sync_pulses()
        assert mock.is_sync_running()
        assert not mock.is_camera_trigger_running()
        mock.start_camera_trigger()
        assert mock.is_sync_running()
        assert mock.is_camera_trigger_running()
        mock.stop_sync_pulses()
        assert not mock.is_sync_running()
        assert mock.is_camera_trigger_running()


class TestMockDisplay:
    def test_show_hide_stimulus(self) -> None:
        mock = MockDisplay()
        mock.show_stimulus("target", {"color": "red", "size": 0.02})
        assert "target" in mock._visible_stimuli
        mock.hide_stimulus("target")
        assert "target" not in mock._visible_stimuli

    def test_clear(self) -> None:
        mock = MockDisplay()
        mock.show_stimulus("s1", {})
        mock.show_stimulus("s2", {})
        mock.clear()
        assert len(mock._visible_stimuli) == 0

    def test_flip_timestamp(self) -> None:
        mock = MockDisplay()
        assert mock.get_flip_timestamp() is None
        mock.update_scene({"cursor_pos": [0.0, 0.0]})
        assert mock.get_flip_timestamp() is not None

    def test_show_cart_pendulum(self) -> None:
        mock = MockDisplay()
        mock.show_cart_pendulum()
        assert "__cup" in mock._visible_stimuli
        assert "__ball" in mock._visible_stimuli
        assert "__string" in mock._visible_stimuli
        assert mock._visible_stimuli["__cup"]["type"] == "polygon"
        assert mock._visible_stimuli["__ball"]["type"] == "circle"
        assert mock._visible_stimuli["__string"]["type"] == "line"

    def test_hide_cart_pendulum(self) -> None:
        mock = MockDisplay()
        mock.show_cart_pendulum()
        mock.hide_cart_pendulum()
        assert "__cup" not in mock._visible_stimuli
        assert "__ball" not in mock._visible_stimuli
        assert "__string" not in mock._visible_stimuli

    def test_show_physics_bodies(self) -> None:
        mock = MockDisplay()
        mock.show_physics_bodies({
            "puck": {"type": "circle", "radius": 0.02},
            "wall": {"type": "polygon", "vertices": []},
        })
        assert "__body_puck" in mock._visible_stimuli
        assert "__body_wall" in mock._visible_stimuli

    def test_hide_physics_bodies(self) -> None:
        mock = MockDisplay()
        mock.show_physics_bodies({
            "puck": {"type": "circle"},
            "wall": {"type": "polygon"},
        })
        mock.hide_physics_bodies(["puck", "wall"])
        assert "__body_puck" not in mock._visible_stimuli
        assert "__body_wall" not in mock._visible_stimuli
