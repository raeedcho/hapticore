# Phase 1: Messaging Backbone and Configuration System

## Goal

Build the foundational Python package for Hapticore: the ZeroMQ + msgpack inter-process messaging layer, Pydantic configuration system, and hardware interface contracts (Protocol classes). This is Phase 1 of 7. Nothing in this phase requires physical hardware — everything should be testable with `pytest` on any machine.

## Context

Hapticore coordinates a Force Dimensions delta.3 haptic robot (C++ server), visual stimulus display (PsychoPy), neural recording (Ripple Grapevine, Neuropixels/SpikeGLX), and hardware sync (Teensy). All components communicate via ZeroMQ with msgpack serialization. This phase builds the communication infrastructure that all subsequent phases plug into.

Read `.github/copilot-instructions.md` for the full architecture overview before starting.

## Plan — implement in this order

Work through these steps sequentially. After each step, run `pytest` to verify everything passes before moving on.

### Step 1: Package scaffolding and dependencies

Create the package structure:

```
python/
├── hapticore/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── messages.py      # message dataclasses and serialization
│   │   ├── messaging.py     # EventBus, CommandClient, CommandServer
│   │   ├── config.py        # Pydantic configuration models
│   │   └── interfaces.py    # Protocol classes for hardware abstraction
│   ├── hardware/
│   │   ├── __init__.py
│   │   └── mock.py          # mock implementations of all interfaces
│   └── py.typed             # PEP 561 marker
├── pyproject.toml
└── configs/
    └── example_config.yaml
tests/
├── conftest.py
├── unit/
│   ├── __init__.py
│   ├── test_messages.py
│   ├── test_messaging.py
│   └── test_config.py
└── integration/
    ├── __init__.py
    └── test_pubsub.py
```

Create `pyproject.toml` with:
- Package name: `hapticore`
- Python requires: `>=3.11`
- Dependencies: `pyzmq>=26.0`, `msgpack>=1.0`, `pydantic>=2.0`, `pydantic-settings>=2.0`, `pyyaml>=6.0`, `numpy>=1.24`
- Optional `[dev]` dependencies: `pytest>=8.0`, `pytest-benchmark>=4.0`, `pytest-timeout>=2.0`, `ruff>=0.4`, `mypy>=1.10`
- Configure ruff (line-length=100, target python 3.11) and mypy (strict for core/) in pyproject.toml
- Add a `[project.scripts]` entry: `hapticore = "hapticore.cli:main"` (placeholder for now)

### Step 2: Message schemas (`core/messages.py`)

Define message types as Python dataclasses (not Pydantic models — dataclasses are faster for high-frequency messages). Each message type must be serializable to/from msgpack.

```python
from __future__ import annotations
import dataclasses
import time
import msgpack
import numpy as np

@dataclasses.dataclass(slots=True)
class HapticState:
    """State broadcast from the haptic server at 100-500 Hz."""
    timestamp: float          # time.monotonic() on the haptic server machine
    sequence: int             # monotonically increasing sequence number
    position: list[float]     # [x, y, z] in meters
    velocity: list[float]     # [vx, vy, vz] in m/s
    force: list[float]        # [fx, fy, fz] in Newtons (applied force)
    active_field: str         # name of the active force field
    field_state: dict         # force-field-specific state (e.g., ball angle for cart-pendulum)

@dataclasses.dataclass(slots=True)
class StateTransition:
    """Published when the task state machine changes state."""
    timestamp: float
    previous_state: str
    new_state: str
    trigger: str              # what triggered the transition
    trial_number: int
    event_code: int           # numeric code sent to recording systems

@dataclasses.dataclass(slots=True)  
class TrialEvent:
    """Arbitrary event within a trial (stimulus onset, response detected, etc.)."""
    timestamp: float
    event_name: str
    event_code: int
    trial_number: int
    data: dict                # event-specific payload

@dataclasses.dataclass(slots=True)
class Command:
    """Command sent from task controller to a hardware server."""
    command_id: str           # unique ID for request-reply matching
    method: str               # e.g., "set_force_field", "move_to_position"
    params: dict              # method-specific parameters

@dataclasses.dataclass(slots=True)
class CommandResponse:
    """Response from hardware server to a command."""
    command_id: str           # matches the Command.command_id
    success: bool
    result: dict              # method-specific return values
    error: str | None = None  # error message if success is False
```

Implement these helper functions in the same module:

```python
def serialize(msg: HapticState | StateTransition | TrialEvent | Command | CommandResponse) -> bytes:
    """Serialize a message dataclass to msgpack bytes."""
    # Use msgpack.packb with dataclasses.asdict()
    ...

def deserialize(data: bytes, msg_type: type) -> ...:
    """Deserialize msgpack bytes to a message dataclass."""
    # Use msgpack.unpackb then construct the dataclass
    ...

# Define topic constants
TOPIC_STATE = b"state"
TOPIC_EVENT = b"event"  
TOPIC_DISPLAY = b"display"
TOPIC_TRIAL = b"trial"
```

**Important:** msgpack cannot serialize numpy arrays directly. If any field contains numpy arrays, convert to lists before serialization. Add a `default` function for `msgpack.packb` that handles numpy types.

**Tests for Step 2** (`tests/unit/test_messages.py`):
- Round-trip test for each message type: create instance → serialize → deserialize → assert equal to original
- Test that HapticState with float position values survives round-trip with correct precision
- Test that field_state dict with nested data (lists, strings, numbers) round-trips correctly
- Test that numpy arrays in position/velocity fields are correctly handled
- Test that serialization produces bytes (not str)
- Benchmark: serialization + deserialization of HapticState should take < 50 µs

### Step 3: ZeroMQ messaging wrappers (`core/messaging.py`)

Implement three classes:

**`EventBus`** — wraps ZeroMQ PUB-SUB for broadcasting events:
```python
class EventBus:
    """Publish-subscribe event distribution.
    
    Publisher side: call publish(topic, message) to broadcast.
    Subscriber side: call subscribe(topic, callback) to receive.
    Uses ipc:// transport by default for lowest latency on same machine.
    """
    def __init__(self, address: str = "ipc:///tmp/hapticore_events"):
        ...
    
    def create_publisher(self) -> EventPublisher:
        """Create a PUB socket bound to the address."""
        ...
    
    def create_subscriber(self, topics: list[bytes] | None = None) -> EventSubscriber:
        """Create a SUB socket connected to the address.
        topics: list of topic prefixes to subscribe to, or None for all.
        """
        ...
```

**`EventPublisher`** and **`EventSubscriber`** — the actual socket wrappers:
```python
class EventPublisher:
    def publish(self, topic: bytes, message: bytes) -> None:
        """Send a multipart message: [topic, payload]."""
        # Use zmq.NOBLOCK to never block the publisher
        ...
    
    def close(self) -> None: ...

class EventSubscriber:
    def recv(self, timeout_ms: int = 0) -> tuple[bytes, bytes] | None:
        """Non-blocking receive. Returns (topic, payload) or None if no message."""
        # Use zmq.Poller with timeout
        ...
    
    def close(self) -> None: ...
```

**`CommandClient`** and **`CommandServer`** — wraps ZeroMQ DEALER-ROUTER for request-reply:
```python
class CommandServer:
    """Receives commands, dispatches to handlers, sends responses.
    
    Uses ROUTER socket so multiple clients can connect.
    """
    def __init__(self, address: str = "ipc:///tmp/hapticore_commands"):
        ...
    
    def register_handler(self, method: str, handler: Callable[[dict], dict]) -> None:
        """Register a handler for a command method name."""
        ...
    
    def poll_and_dispatch(self, timeout_ms: int = 0) -> bool:
        """Check for incoming command, dispatch to handler, send response.
        Returns True if a command was processed.
        """
        ...
    
    def close(self) -> None: ...

class CommandClient:
    """Sends commands and receives responses.
    
    Uses DEALER socket for async-compatible request-reply.
    """
    def __init__(self, address: str = "ipc:///tmp/hapticore_commands"):
        ...
    
    def send_command(self, command: Command, timeout_ms: int = 1000) -> CommandResponse:
        """Send a command and wait for response with timeout.
        Raises TimeoutError if no response within timeout.
        """
        ...
    
    def close(self) -> None: ...
```

**Design decisions:**
- Use `zmq.Context.instance()` (singleton) so all sockets in a process share one context.
- PUB-SUB uses `ipc://` transport by default (faster than TCP loopback). Fall back to `tcp://` for cross-machine use.
- All sockets use `zmq.LINGER = 0` so `close()` never blocks.
- CommandServer uses ROUTER socket. CommandClient uses DEALER socket. The DEALER socket prepends an empty delimiter frame; the ROUTER socket prepends the client identity. Handle the framing correctly.
- Multipart message format for PUB-SUB: `[topic_bytes, msgpack_payload_bytes]`
- Multipart message format for DEALER-ROUTER commands: `[client_identity, empty_delimiter, msgpack_command_bytes]` (ROUTER side) / `[empty_delimiter, msgpack_command_bytes]` (DEALER side)
- Generate `command_id` using `uuid.uuid4().hex[:12]` for uniqueness.

**Tests for Step 3** (`tests/unit/test_messaging.py`):
- Test EventPublisher → EventSubscriber: publish a message, verify subscriber receives it with correct topic and payload
- Test topic filtering: subscriber subscribes to `b"state"` only, publisher sends `b"state"` and `b"event"` messages, verify subscriber only receives `b"state"`
- Test CommandClient → CommandServer round-trip: register a handler that echoes params back, send a command, verify response matches
- Test CommandClient timeout: send command to server that has no handler registered, verify TimeoutError is raised
- Test multiple subscribers: two subscribers both receive the same published message
- All tests should use `ipc://` with unique temp paths (use `tmp_path` fixture) to avoid conflicts between parallel test runs

**Integration test** (`tests/integration/test_pubsub.py`):
- Launch a publisher in one `multiprocessing.Process`, two subscribers in two other processes
- Publisher sends 1000 messages at 1 kHz (1 ms apart)
- Each subscriber counts received messages — verify both receive all 1000 (with a small tolerance for the slow-joiner issue: subscriber may miss the first 1-5 messages)
- Measure end-to-end latency: publisher timestamps message, subscriber measures receipt time. Assert median latency < 1 ms.

### Step 4: Configuration models (`core/config.py`)

Implement the Pydantic v2 configuration hierarchy:

```python
from pydantic import BaseModel, Field
from pathlib import Path

class ZMQConfig(BaseModel):
    event_pub_address: str = "ipc:///tmp/hapticore_events"
    haptic_state_address: str = "ipc:///tmp/hapticore_haptic_state"
    haptic_command_address: str = "ipc:///tmp/hapticore_haptic_cmd"
    transport: str = "ipc"  # "ipc" or "tcp"

class SubjectConfig(BaseModel):
    subject_id: str = Field(..., min_length=1, description="Subject identifier")
    species: str = "macaque"
    implant_info: dict = Field(default_factory=dict)

class HapticConfig(BaseModel):
    server_address: str = "localhost"
    workspace_bounds: dict = Field(
        default_factory=lambda: {"x": [-0.15, 0.15], "y": [-0.15, 0.15], "z": [-0.15, 0.15]},
        description="Workspace limits in meters"
    )
    force_limit_n: float = Field(default=20.0, gt=0, le=40.0, description="Maximum force in Newtons")
    publish_rate_hz: float = Field(default=200.0, gt=0, le=1000.0)

class DisplayConfig(BaseModel):
    resolution: tuple[int, int] = (1920, 1080)
    refresh_rate_hz: int = Field(default=60, gt=0, le=240)
    fullscreen: bool = True
    monitor_distance_cm: float = Field(default=50.0, gt=0)
    background_color: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])

class RecordingConfig(BaseModel):
    ripple_enabled: bool = False
    spikeglx_enabled: bool = False
    lsl_enabled: bool = True
    save_dir: Path = Field(default=Path("data"))

class TaskConfig(BaseModel):
    task_class: str = Field(..., description="Dotted path to task class, e.g. 'hapticore.tasks.center_out.CenterOutTask'")
    params: dict = Field(default_factory=dict)
    conditions: list[dict] = Field(default_factory=list)
    block_size: int = Field(default=20, gt=0)
    num_blocks: int = Field(default=10, gt=0)
    randomization: str = Field(default="pseudorandom", pattern="^(pseudorandom|sequential|latin_square)$")

class SyncConfig(BaseModel):
    teensy_port: str = "/dev/ttyACM0"
    sync_pulse_rate_hz: float = Field(default=1.0, gt=0, le=10.0)
    event_code_bits: int = Field(default=8, ge=1, le=16)

class ExperimentConfig(BaseModel):
    experiment_name: str
    subject: SubjectConfig
    haptic: HapticConfig = Field(default_factory=HapticConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    task: TaskConfig
    sync: SyncConfig = Field(default_factory=SyncConfig)
    zmq: ZMQConfig = Field(default_factory=ZMQConfig)
```

Add a loader function:
```python
def load_config(yaml_path: str | Path) -> ExperimentConfig:
    """Load and validate experiment configuration from a YAML file."""
    ...
```

Create `configs/example_config.yaml` with a complete example configuration.

**Tests for Step 4** (`tests/unit/test_config.py`):
- Test that `example_config.yaml` loads and validates successfully
- Test that missing required fields (`experiment_name`, `subject.subject_id`, `task.task_class`) raise ValidationError
- Test that out-of-range values (force_limit > 40, negative refresh rate) raise ValidationError
- Test that invalid randomization string raises ValidationError
- Test that default values are applied correctly when optional fields are omitted
- Test round-trip: load config → `model_dump()` → `ExperimentConfig.model_validate()` → assert equal

### Step 5: Interface contracts (`core/interfaces.py`)

Define Protocol classes for every hardware interface. These are the contracts that both real implementations and mock implementations must satisfy.

```python
from __future__ import annotations
from typing import Protocol, Callable, runtime_checkable

@runtime_checkable
class HapticInterface(Protocol):
    def get_latest_state(self) -> HapticState | None: ...
    def send_command(self, cmd: Command) -> CommandResponse: ...
    def subscribe_state(self, callback: Callable[[HapticState], None]) -> None: ...
    def unsubscribe_state(self) -> None: ...

@runtime_checkable
class NeuralRecordingInterface(Protocol):
    def start_recording(self, filename: str) -> None: ...
    def stop_recording(self) -> None: ...
    def is_recording(self) -> bool: ...
    def get_timestamp(self) -> float: ...

@runtime_checkable
class SyncInterface(Protocol):
    def send_event_code(self, code: int) -> None: ...
    def start_sync_pulses(self) -> None: ...
    def stop_sync_pulses(self) -> None: ...
    def is_running(self) -> bool: ...

@runtime_checkable
class DisplayInterface(Protocol):
    def update_scene(self, scene_state: dict) -> None: ...
    def show_stimulus(self, stim_id: str, params: dict) -> None: ...
    def hide_stimulus(self, stim_id: str) -> None: ...
    def clear(self) -> None: ...
    def get_flip_timestamp(self) -> float | None: ...
```

### Step 6: Mock implementations (`hardware/mock.py`)

Implement mock versions of every Protocol that work without hardware. These are used for testing and simulation.

```python
class MockHapticInterface:
    """Mock haptic interface that returns configurable synthetic data."""
    
    def __init__(self, initial_position: list[float] | None = None):
        self._position = initial_position or [0.0, 0.0, 0.0]
        self._velocity = [0.0, 0.0, 0.0]
        self._sequence = 0
        self._callback: Callable | None = None
        self._command_log: list[Command] = []  # log all received commands for test assertions
    
    # ... implement all HapticInterface methods
    # get_latest_state returns a HapticState with the current synthetic position
    # send_command logs the command and returns success
    # subscribe_state stores the callback
```

Similarly implement `MockNeuralRecording`, `MockSync`, `MockDisplay`. Each mock should:
- Log all method calls for test verification (append to an internal list)
- Return sensible defaults
- Be configurable (e.g., MockHapticInterface can accept a trajectory to replay)

**Tests:** Verify each mock satisfies its Protocol: `assert isinstance(MockHapticInterface(), HapticInterface)`

## Verification checklist

Before considering Phase 1 complete, verify:

- [ ] `pip install -e ".[dev]"` succeeds
- [ ] `ruff check python/` passes with no errors
- [ ] `mypy python/hapticore/core/ --strict` passes
- [ ] `pytest tests/unit/` — all tests pass
- [ ] `pytest tests/integration/` — pubsub integration test passes
- [ ] Message round-trip benchmark < 50 µs (run with `pytest --benchmark-only`)
- [ ] Each mock `isinstance` check passes against its Protocol
- [ ] `example_config.yaml` loads without errors
- [ ] Invalid configs raise clear ValidationError messages

## What NOT to build in this phase

- No PsychoPy imports or display code
- No actual hardware communication (Ripple, SpikeGLX, Teensy, Force Dimension)
- No state machine or task execution logic
- No C++ code
- No CLI beyond a placeholder
- No data logging or session management
