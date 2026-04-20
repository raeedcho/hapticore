# Xipppy Python API Reference

**Source:** Xipppy User Manual v1.06, March 2025 (Ripple Neuro)

> All examples below assume: `import xipppy as xp`
>
> **Key difference from xippmex (MATLAB):** Xipppy uses **zero-indexed** electrode IDs (0-511), while xippmex uses 1-indexed. All electrode references in xipppy are 0-based.

---

## Table of Contents

1. [Connection Management](#1-connection-management)
2. [Time](#2-time)
3. [Trellis Operator & Trial Control](#3-trellis-operator--trial-control)
4. [Electrode Listing](#4-electrode-listing)
5. [Informational Functions](#5-informational-functions)
6. [Signal Functions](#6-signal-functions)
7. [Filter Functions](#7-filter-functions)
8. [Continuous Data Functions](#8-continuous-data-functions)
9. [Spike & Stim Data Functions](#9-spike--stim-data-functions)
10. [Digital I/O](#10-digital-io)
11. [Spike Threshold](#11-spike-threshold)
12. [Stimulation](#12-stimulation)
13. [Fast Settle](#13-fast-settle)
14. [Impedance](#14-impedance)
15. [Transceiver](#15-transceiver)
16. [Sensors & Battery](#16-sensors--battery)
17. [Processor / Button](#17-processor--button)
18. [Stim Waveform Construction Examples](#18-stim-waveform-construction-examples)

---

## 1. Connection Management

### `xipppy._open(use_tcp=False)`

Opens a connection to the Ripple processor. **Must be called before any other xipppy function.**

| Parameter | Type | Description |
|-----------|------|-------------|
| `use_tcp` | `bool` | `False` for UDP mode (default), `True` for TCP mode |

**Returns:** Connection object

**Notes:**
- Only one connection can be open at a time
- Multiple calls to `_open()` when already connected have no effect
- Raises `XippPyException` on failure

```python
try:
    xp._open()
    print('Connected via UDP')
except:
    xp._open(use_tcp=True)
    print('Connected via TCP')
```

### `xipppy._close()`

Immediately closes the connection to the Ripple processor. Clears all cached data and buffers.

```python
xp._close()
```

### `xipppy.xipppy_open(use_tcp=False)` — Context Manager (PREFERRED)

Context manager that automatically opens and closes the connection. **Do NOT call `_close()` from within this context.**

```python
with xp.xipppy_open():
    pass  # test connection

with xp.xipppy_open(use_tcp=True):
    # TCP mode
    print(xp.time())
```

**Reentrant:** Safe to nest — only one actual open/close call is made:
```python
def print_nip_time():
    with xp.xipppy_open():
        print(xp.time())

with xp.xipppy_open():
    print_nip_time()  # no extra open/close
```

---

## 2. Time

### `xipppy.time()`

Returns the most recent Ripple processor time — number of clock cycles at 30 kHz (33.3 µs/cycle) since processor startup.

| Returns | Type | Description |
|---------|------|-------------|
| time | `int` | 30 kHz clock ticks since startup |

```python
minutes_elapsed = xp.time() / 30000 / 60
```

---

## 3. Trellis Operator & Trial Control

### `xipppy.add_operator(oper_addr, connection_policy=None, timeout=None)`

Adds a Trellis operator ID. **Required before `xipppy.trial()` in TCP mode.** Throws error in UDP mode.

| Parameter | Type | Description |
|-----------|------|-------------|
| `oper_addr` | `int` | Last octet of IPv4 address of Trellis computer |
| `connection_policy` | `int` or `None` | `None`=default, `0`=PREFER_WIRED, `1`=PREFER_WIRELESS, `2`=WIRED_ONLY, `3`=WIRELESS_ONLY |
| `timeout` | `int` or `None` | TCP connection timeout in ms |

```python
xp.add_operator(129)
```

### `xipppy.trial(oper=None, status=None, file_name_base=None, auto_stop_time=None, auto_incr=None, incr_num=None)`

Controls data recording through Trellis. **Requires "Enable Remote Control" in Trellis File Save.**

| Parameter | Type | Description |
|-----------|------|-------------|
| `oper` | `int` | Operator ID (typically `129`) |
| `status` | `str` or `None` | `'recording'`, `'stopped'`, or `'paused'` |
| `file_name_base` | `str` or `None` | Full path for recording files (`.nev`, `.ns2`, etc.) |
| `auto_stop_time` | `int` or `None` | Auto-stop time in seconds (`0` = disabled) |
| `auto_incr` | `bool` or `None` | Auto-increment filename with 4-digit padded index |
| `incr_num` | `int` or `None` | Current increment number |

**Returns:** `tuple` — `(status, file_name_base, auto_stop_time, auto_incr, incr_num)`

```python
# Poll current recording state
print(xp.trial(oper=129))
# ('stopped', 'C:\\...\\datafile', 0, True, 7)

# Start recording with 30s auto-stop
xp.trial(oper=129, status='recording', auto_stop_time=30)

# Stop recording
xp.trial(oper=129, status='stopped')
```

---

## 4. Electrode Listing

### `xipppy.list_elec(fe_type, max_elecs=256)`

Returns electrode IDs for the current Ripple processor setup.

| Parameter | Type | Description |
|-----------|------|-------------|
| `fe_type` | `str` | `'all'`, `'stim'`, `'micro'`, `'nano'`, `'surf'`, `'EMG'`, `'ecog'`, `'eeg'`, or `'analog'` |
| `max_elecs` | `int` | Max electrodes to return (default 256) |

**Returns:** `array` of zero-indexed electrode IDs

**Channel numbering:**
- Port A: 0–127
- Port B: 128–255
- Port C: 256–383
- Port D: 384–511
- Analog inputs: 10240–10269 (SMA 1-4, micro-D 1-23, 2 line-level audio)

**Notes:**
- `'micro'` covers Micro and Micro HV Front Ends
- `'surf'` covers both Surf S and Surf D Front Ends

```python
xp.list_elec('micro', 16)
# array('I', [0, 1, 2, ..., 15])

xp.list_elec('stim')
# returns all stim-capable electrode IDs
```

---

## 5. Informational Functions

### `xipppy.get_fe(elec)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Zero-indexed electrode ID |

**Returns:** `int` — Front End index. Raises exception if no Front End found.

### `xipppy.get_fe_streams(elec, max_streams=32)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Zero-indexed electrode ID |

**Returns:** `list[str]` — Stream types: `'raw'`, `'stim'`, `'hi-res'`, `'lfp'`, `'spk'`

### `xipppy.get_fe_version(elec, max_size=1024)`

**Returns:** `str` — Product/serial/hardware/software version, e.g. `'R02003-0226v6.5'`

### `xipppy.get_nip_serial(max_size=1024)`

**Returns:** `str` — Ripple processor serial number, e.g. `'R04500-0014'`

### `xipppy.get_nipexec_version(max_size=1024)`

**Returns:** `str` — Processor exec version, e.g. `'1.14.5+144'`

### `xipppy.get_version()`

**Returns:** `dict` with keys:
- `'xipp'` — XIPP protocol version (e.g. `'0.9'`)
- `'xipplib'` — C library version (e.g. `'0.9.1+8X'`)
- `'xipppy'` — Python module version (e.g. `'0.16.8'`)

---

## 6. Signal Functions

### `xipppy.signal(elec, stream_ty)`

Check if a stream is enabled.

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Zero-indexed electrode ID |
| `stream_ty` | `str` | `'raw'`, `'stim'`, `'hi-res'`, `'lfp'`, `'spk'` |

**Returns:** `bool` — whether the stream is enabled

**Note:** Only `'stim'` and `'spk'` are per-channel. All others are per-Front End.

### `xipppy.signal_set(elec, stream_ty, val)`

Enable/disable a data stream.

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Zero-indexed electrode ID |
| `stream_ty` | `str` | Stream type |
| `val` | `bool` | `True` to enable, `False` to disable |

```python
xp.signal_set(0, 'raw', True)   # enable raw on electrode 0
xp.signal_set(1, 'spk', False)  # disable spikes on electrode 1
```

### Stream-Specific Shortcuts

**Status (check if enabled):**
- `xipppy.signal_raw(elec)` → `bool`
- `xipppy.signal_lfp(elec)` → `bool`
- `xipppy.signal_spk(elec)` → `bool`
- `xipppy.signal_stim(elec)` → `bool`

**Set (enable/disable):**
- `xipppy.signal_set_raw(elec, val)`
- `xipppy.signal_set_lfp(elec, val)`
- `xipppy.signal_set_spk(elec, val)`
- `xipppy.signal_set_stim(elec, val)`

### `xipppy.signal_save(elec, stream_ty)` — *Upcoming Feature*

Returns `bool` for whether stream will be saved to file.

### `xipppy.signal_save_set(elec, stream_ty, val)` — *Upcoming Feature*

Sets file save selection.

---

## 7. Filter Functions

### Preset Filter Table

| sel | hires | hires notch | lfp | lfp notch | spike |
|-----|-------|-------------|-----|-----------|-------|
| 0 | 1-125 Hz | Off | 1-125 Hz | Off | 250-7500 Hz |
| 1 | 1-175 Hz | 60 Hz | 1-175 Hz | 60 Hz | 500-7500 Hz |
| 2 | 1-250 Hz | 60/120/180 Hz | 1-250 Hz | 60/120/180 Hz | 750-7500 Hz |
| 3 | 1-500 Hz | 50 Hz | 15-375 EMG | 50 Hz | Custom |
| 4 | 1-500 Hz | 50/100/150 Hz | Custom | 50/100/150 Hz | — |
| 5 | 15-375 EMG | Custom | Custom | Custom | — |
| 6 | Custom | — | — | — | — |

### `xipppy.filter_list_names(elec, max_names=32)`

**Returns:** `list[str]` — e.g. `['hires', 'hires notch', 'lfp', 'lfp notch', 'spike']`

### `xipppy.filter_list_selection(elec, filter_type)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `filter_type` | `str` | `'hires'`, `'hires notch'`, `'lfp'`, `'lfp notch'`, `'spike'` |

**Returns:** `tuple(sel, nfilt)` — current filter index and total number of available filters

### `xipppy.filter_set(elec, filter_type, filter_index)`

Select a preset filter.

```python
xp.filter_set(2, 'hires', 1)    # set hires to 1-175 Hz on electrode 3
xp.filter_set(129, 'lfp', 3)    # set lfp to 15-375 EMG on electrode 130
```

### `xipppy.filter_set_custom(elec, filter_type, filt_desc)`

Apply a custom filter using SOS (second-order sections) coefficients.

| Parameter | Type | Description |
|-----------|------|-------------|
| `filt_desc` | `SosFilterDesc` | Filter description object |

**SosFilterDesc fields:** `label`, `center`, `lowCutoff`, `highCutoff`, `centerOrder`, `centerFlags`, `lowOrder`, `lowFlags`, `highOrder`, `highFlags`, `maxStages`, `numStages`, `stages`

### `xipppy.filter_get_desc(elec, filter_type, filter_index)`

**Returns:** `SosFilterDesc` or `None`

### `xipppy.SosStage(b0, b1, a0, a1)`

SOS filter stage. Coefficients from `scipy.signal.butter(..., output='sos')`:
- `b0 = sos[i, 0]`, `b1 = sos[i, 1]`, `a0 = sos[i, 4]`, `a1 = sos[i, 5]`

#### Custom Filter Example

```python
import scipy.signal
import numpy as np

sos = scipy.signal.butter(3, np.array([30, 100])/(2000/2), 'bandpass', output='sos')

stages = []
for i in range(sos.shape[0]):
    stages.append(xp.SosStage(sos[i, 0], sos[i, 1], sos[i, 4], sos[i, 5]))

custom_filter = xp.filter_get_desc(0, 'hires', 6)  # get custom slot
custom_filter.label = 'myFilter'
custom_filter.stages = stages
custom_filter.lowCutoff = 30
custom_filter.highCutoff = 100
custom_filter.center = 0
custom_filter.centerOrder = 0
custom_filter.centerFlags = 0
custom_filter.lowOrder = 3
custom_filter.lowFlags = 0
custom_filter.highOrder = 3
custom_filter.highFlags = 0

xp.filter_set_custom(0, 'hires', custom_filter)
xp.filter_set(0, 'hires', 6)  # enable the custom filter
```

---

## 8. Continuous Data Functions

Xipppy maintains a **5-second circular buffer** for all enabled streams.

### `xipppy.cont_raw(npoints, elecs, start_timestamp=None)`
### `xipppy.cont_lfp(npoints, elecs, start_timestamp=None)`
### `xipppy.cont_hires(npoints, elecs, start_timestamp=None)`
### `xipppy.cont_hifreq(npoints, elecs, start_timestamp=None)`
### `xipppy.cont_emg(npoints, elecs, start_timestamp=None)`
### `xipppy.cont_status(npoints, elecs, start_timestamp=None)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `npoints` | `int` | Number of datapoints per electrode |
| `elecs` | `list[int]` | Zero-indexed electrode IDs |
| `start_timestamp` | `int` or `None` | Ripple processor timestamp (30 kHz ticks). `None` = most recent data |

**Returns:** `tuple(data, timestamp)`
- `data`: 1D array of length `npoints * len(elecs)`. First `npoints` = first electrode, etc.
  - Neural data in **µV**, analog I/O in **mV**
- `timestamp`: Timestamp for first sample (0 if `start_timestamp` not specified)

**Sampling rates:**
| Stream | Sample Rate |
|--------|-------------|
| `raw` | 30 kHz |
| `hires` | 2 kHz |
| `lfp` | 1 kHz |
| `status` | 2 kHz |

**Channel numbering:**
- Recording electrodes: 0–511
- Analog inputs: 10240–10269

```python
# Most recent 5000 raw samples from electrodes 1 and 2
data, ts = xp.cont_raw(5000, [1, 2])

# LFP data from a specific timestamp
data, ts = xp.cont_lfp(250, [6, 8, 19], xp.time())
```

### MIRA `cont_status` Channels

| Channel | Name | Units | Description |
|---------|------|-------|-------------|
| 0 | counter | — | 8-bit incrementing counter [0-255] |
| 1 | xcvr_coil_v | V | Transceiver coil voltage [~1.8-3.3] |
| 2 | xcvr_coil_a | A | Transceiver coil current |
| 3 | xcvr_input_v | V | Transceiver input voltage |
| 4 | xcvr_input_a | A | Transceiver input current |
| 5 | xcvr_temp | °C | Transceiver internal temperature |
| 6 | xcvr_temp_offboard | — | (Deprecated) |
| 7 | servo_state | — | 0=idle, 1=comm init, 2=coil init, 3=servoing, 4=searching off, 5=searching on |
| 8 | impl_serial | — | Implant serial number |
| 9 | impl_deviceid | — | Implant model number |
| 10 | impl_temp | °C | Implant temperature |
| 11 | impl_humidity | — | (Deprecated) |
| 12 | impl_v | V | Rectified implant voltage |
| 13 | impl_ver_hw | — | Implant hardware version |
| 14 | impl_ver_fw | — | Implant firmware version |

---

## 9. Spike & Stim Data Functions

Both functions use a **circular buffer of 1024** events. Buffer is flushed on read. Returned counts reflect true event count even if >1024.

### `SegmentDataPacket` Class

| Attribute | Description |
|-----------|-------------|
| `.timestamp` | Event timestamp |
| `.class_id` | Classification ID |
| `.wf` | 52-sample waveform |

### `xipppy.spk_data(elec, max_spk=1024)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Single zero-indexed electrode ID |
| `max_spk` | `int` | Max spikes to return (max 1024) |

**Returns:** `tuple(count, data)`
- `count`: Number of spikes since last call
- `data`: List of `SegmentDataPacket` objects

```python
count, data = xp.spk_data(1)
```

### `xipppy.stim_data(elec, max_spk=1024)`

Same signature as `spk_data` but for stimulation waveform markers.

```python
count, data = xp.stim_data(5, 600)
```

---

## 10. Digital I/O

### `SegmentEventPacket` Class

| Attribute | Type | Description |
|-----------|------|-------------|
| `.timestamp` | — | Event timestamp |
| `.reason` | bitmask | Trigger source: `1`=parallel, `2`=SMA1, `4`=SMA2, `8`=SMA3, `16`=SMA4, `32`=digout marker |
| `.sma1` | `uint16` | SMA port 1 value |
| `.sma2` | `uint16` | SMA port 2 value |
| `.sma3` | `uint16` | SMA port 3 value |
| `.sma4` | `uint16` | SMA port 4 value |
| `.parallel` | `uint16` | Parallel port value |

### `xipppy.digin(max_events=1024)`

Read digital input events. **Buffer is flushed on read (circular, 1024 events max).**

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_events` | `int` | Max events to return |

**Returns:** `tuple(n, events)`
- `n`: Number of events since last call
- `events`: List of `SegmentEventPacket` objects

### `xipppy.digout(outputs, values)`

Set digital output ports. Supply voltage is **3.3V**.

| Parameter | Type | Description |
|-----------|------|-------------|
| `outputs` | `list[int]` | Output port indices: `0-3` = SMA outputs 1-4, `4` = parallel port |
| `values` | `list[int]` | Values (SMA: binary 0/1, parallel: 16-bit unsigned) |

**Note:** `len(values)` must equal `len(outputs)`

```python
# TTL pulse on SMA outputs 2 and 3
xp.digout([1, 2], [1, 1])
time.sleep(0.001)
xp.digout([1, 2], [0, 0])

# Set parallel port value
xp.digout([4], [0xFF00])
```

---

## 11. Spike Threshold

### `xipppy.spk_thresh(elec)`

Get current spike thresholds.

**Returns:** `tuple(low_thresh, high_thresh)` — both in µV

### `xipppy.spk_thresh_set(elecs, lower, upper)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `elecs` | `list[int]` | Zero-indexed electrode IDs |
| `lower` | `int` | Lower threshold in µV (**must be negative**) |
| `upper` | `int` | Upper threshold in µV (**must be positive**) |

### `xipppy.spk_thresh_set_lower(elecs, lower)`

Set only the lower threshold.

### `xipppy.spk_thresh_set_upper(elecs, upper)`

Set only the upper threshold.

```python
low, high = xp.spk_thresh(3)

xp.spk_thresh_set_lower([4, 10], -40)
xp.spk_thresh_set([2], -60, 90)
```

---

## 12. Stimulation

### Stim Enable

#### `xipppy.stim_enable()`

**Returns:** `bool` — current global stimulation enable state

#### `xipppy.stim_enable_set(val)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `val` | `bool` | `True` to enable, `False` to disable |

**Notes:**
- **Must enable globally before any stimulation**
- Default is `False` on processor startup
- Can be used to immediately stop all stimulation
- Normal usage: enable → stimulate → disable after completion

```python
xp.stim_enable()        # False (default)
xp.stim_enable_set(True)
xp.stim_enable()        # True
```

### Stim Resolution

#### `xipppy.stim_get_res(elec)`

**Returns:** `int` — current resolution index (0-4, or 0-5 for Macro2)

#### `xipppy.stim_set_res(elec, level)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Zero-indexed electrode (sets for entire Front End) |
| `level` | `int` | Resolution index |

**Resolution Table:**

| Index | Pico/Nano/Micro | Macro | Macro2 |
|-------|-----------------|-------|--------|
| 0 | 1 µA/step | 10 µA/step | 1 µA/step |
| 1 | 2 µA/step | 20 µA/step | 2 µA/step |
| 2 | 5 µA/step | 50 µA/step | 10 µA/step |
| 3 | 10 µA/step | 100 µA/step | 20 µA/step |
| 4 | 20 µA/step | 200 µA/step | 50 µA/step |
| 5 | — | — | 120 µA/step |

**Notes:**
- Applied per-Front End (all channels on the Front End change)
- Amplitude is specified in unitless steps (0-100). Actual current = resolution × steps
- At highest resolution index, range is only ±75 steps
- Default on power-on: lowest step size (index 0)

```python
xp.stim_get_res(15)     # 0
xp.stim_set_res(15, 3)  # sets entire FE to index 3
xp.stim_get_res(1)      # 3 (same FE, also changed)
```

### StimSegment

#### `xipppy.StimSegment(length, amplitude, polarity, enable=True, delay=0, fast_settle=False, amp_select=1)`

Defines a single segment of a stimulation waveform.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `length` | `int` | *required* | Duration in 30 kHz clock cycles (33.33 µs each). Range [0, 65535] |
| `amplitude` | `int` | *required* | Amplitude in stimulation steps (0-100). Current = resolution × amplitude |
| `polarity` | `int` | *required* | `-1` = cathodic, `1` = anodic |
| `enable` | `bool` | `True` | `True` = current output, `False` = transition to zero (interphase interval) |
| `delay` | `int` | `0` | Fine timing delay in units of 33.33µs/32 ≈ 1.04µs. Range [0, 31] |
| `fast_settle` | `bool` | `False` | Enable fast settling during this segment |
| `amp_select` | `int` | `1` | `1` = NEURAL_AMP (default), `0` = STIM_LEVEL_AMP |

**Time unit conversion:**
- 1 clock cycle = 33.33 µs = 1/30000 s
- Length of 6 = 6 × 33.33 = 200 µs
- Length of 3 = 3 × 33.33 = 100 µs
- 1 delay unit = 33.33/32 ≈ 1.04 µs

```python
# Cathodic phase: 200 µs, amplitude 100
pseg = xp.StimSegment(6, 100, -1)

# Interphase interval: 100 µs, no current
ipi = xp.StimSegment(3, 0, 1, enable=False)

# Anodic phase: 200 µs, amplitude 100
nseg = xp.StimSegment(6, 100, 1)
```

### StimSeq

#### `xipppy.StimSeq(elec, period, repeats, *segments, action=0)`

Defines a complete stimulation sequence (control word + waveform segments).

| Parameter | Type | Description |
|-----------|------|-------------|
| `elec` | `int` | Zero-indexed electrode ID [0, 511] |
| `period` | `int` | Repetition period in 30 kHz clock cycles (33.33 µs). E.g. `1000` = 33.33 ms ≈ **30 Hz** |
| `repeats` | `int` | Number of waveform repetitions [1, 4095] |
| `*segments` | `StimSegment` | Variadic list of segments (NOT a Python list — pass as separate args) |
| `action` | `int` | How the processor handles the command (default `0`) |

**Action values:**

| Value | Constant | Description |
|-------|----------|-------------|
| `0` | `STIM_IMMED` | Execute immediately (clears queued stim). If ongoing, placed after current waveform |
| `1` | `STIM_CURCYC` | Execute after current cycle, blending frequencies |
| `2` | `STIM_ALLCYC` | Queue after current train (max queue depth: 8) |
| `3` | `STIM_TRIGGER` | *Upcoming feature* |
| `4` | `STIM_AT_TIME` | Execute at specified processor time (uses `period` as lower 16 bits of timestamp) |

**WARNING:** If >8 patterns are queued with `STIM_ALLCYC` or `STIM_AT_TIME`, stimulation is **disabled on the entire Front End**.

#### `xipppy.StimSeq.send(seq)`

Send a single stimulation sequence to the Ripple processor.

```python
seq0 = xp.StimSeq(elec, 1000, 30, pseg, ipi, nseg)
xp.StimSeq.send(seq0)
```

#### `xipppy.StimSeq.send_stim_seqs(seq_list)`

Send multiple stimulation sequences for **synchronous multi-channel stimulation**. If total size < 1500 bytes, sequences execute with precise timing synchronization.

| Parameter | Type | Description |
|-----------|------|-------------|
| `seq_list` | `list[StimSeq]` | List of StimSeq objects |

```python
seq_arr = [seq_ch0, seq_ch15]
xp.StimSeq.send_stim_seqs(seq_arr)
```

### Stim Exhaust — *Upcoming Feature*

For Macro+stim Front Ends: pulls stim electrodes through a resistor after stimulation.

#### `xipppy.stim_get_exhaust(elec)`

**Returns:** Current stim exhaust resistor value

#### `xipppy.stim_set_exhaust(elec, res_opt)`

Sets stim exhaust resistor value per-Front End.

---

## 13. Fast Settle

Enables successful recordings within ~1ms of stimulation on neighboring electrodes.

### `xipppy.fast_settle_get_choices(fe, fs_type, max_names=32)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `fe` | `int` | Front End index [0, 15] |
| `fs_type` | `str` | `'stim'` or `'digin'` |

**Returns:** `tuple(current_index, list_of_options)`

```python
xp.fast_settle_get_choices(0, 'stim')
# (1, ['None', 'Any Front End', 'Same Front Port', 'Same Front End'])
```

### `xipppy.fast_settle(fe, fs_type, item=None, duration=None)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `fe` | `int` | Front End index [0, 15] |
| `fs_type` | `str` | `'stim'` or `'digin'` |
| `item` | `int` or `None` | Option index: `0`=none, `1`=any FE, `2`=same port, `3`=same FE. `None` returns current |
| `duration` | `float` or `None` | Duration in ms [0.5, 15.0]. Default 0.5 ms |

```python
xp.fast_settle(0, 'stim', item=2, duration=2)  # same port, 2ms
```

### `xipppy.fast_settle_get_duration(fe, fs_type)`

**Returns:** `float` — current fast settle duration in ms

```python
xp.fast_settle_get_duration(0, 'stim')  # 2.0
```

---

## 14. Impedance

### `xipppy.impedance(elecs)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `elecs` | `list[int]` | Zero-indexed electrode IDs |

**Returns:** `array('f', [...])` — impedance magnitudes in Ohms

```python
xp.impedance([1, 2, 3])
# array('f', [1072831.25, 1037615.625, 1079905.5])
```

---

## 15. Transceiver

### `xipppy.transceiver_enable(front_end, enable=True)`

Enable/disable a Link 32 transceiver.

| Parameter | Type | Description |
|-----------|------|-------------|
| `front_end` | `int` | FE location: A.1=0, A.2=1, ..., D.4=15 |
| `enable` | `bool` | `True` to enable, `False` to disable |

```python
xp.transceiver_enable(6, True)  # enable B.3
```

---

## 16. Sensors & Battery

### `xipppy.sensors.Sensor` Class

| Attribute | Description |
|-----------|-------------|
| `.current` | Current reading |
| `.voltage` | Voltage reading |
| `.power` | Power reading |

### `xipppy.wall_sensor()`

**Returns:** `Sensor` — wall power sensor readings

### `xipppy.vdd_sensor()`

**Returns:** `Sensor` — VDD sensor readings

### `xipppy.internal_battery()`

**Returns:** Internal battery percentage

### `xipppy.sensors.external_battery_detected()`

**Returns:** `bool` — whether external battery is connected

### `xipppy.sensors.external_battery()`

**Returns:** External battery percentage (0 if not connected)

---

## 17. Processor / Button

### `xipppy.button_get(button)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `button` | `int` | `1`=STOP STIM, `2`=EVENT, `3`=F1, `4`=F2 |

**Returns:** `int` — number of presses since last call for that button

---

## 18. Stim Waveform Construction Examples

### Example 1: Basic Biphasic Pulse at 30 Hz for 1 Second

```
Waveform: cathodic (200µs) → interphase (100µs) → anodic (200µs)
```

```python
import xipppy as xp
import time

elec = 0
res = 3  # 10 µA/step for Micro FE

with xp.xipppy_open(use_tcp=True):
    stimList = xp.list_elec('stim')

    if xp.stim_get_res(0) <= 1:
        xp.stim_enable_set(False)
        xp.stim_set_res(elec, res)

    xp.stim_enable_set(True)

    # Cathodic phase: 200 µs (6 cycles), amplitude 100, negative polarity
    pseg = xp.StimSegment(6, 100, -1)

    # Interphase interval: 100 µs (3 cycles), no current
    ipi = xp.StimSegment(3, 0, 1, enable=False)

    # Anodic phase: 200 µs (6 cycles), amplitude 100, positive polarity
    nseg = xp.StimSegment(6, 100, 1)

    # Sequence: electrode 0, period 1000 cycles (33.33ms = 30Hz), 30 repeats
    seq0 = xp.StimSeq(elec, 1000, 30, pseg, ipi, nseg)

    xp.StimSeq.send(seq0)
    time.sleep(0.1)
```

### Example 2: Sub-Clock-Cycle Pulse Width Using `delay` Parameter

Creates a 50 µs biphasic pulse (finer than the 33.33 µs clock cycle).

```python
import xipppy as xp
import time
import math

clock_cycle = 1/30 * 1000  # 33.33 µs
delay_length = 1/30 * 1000 / 32  # ~1.04 µs per delay unit
elec = 0
res = 3

with xp.xipppy_open(use_tcp=True):
    stimList = xp.list_elec('stim')

    if xp.stim_get_res(0) <= 1:
        xp.stim_enable_set(False)
        xp.stim_set_res(elec, res)

    xp.stim_enable_set(True)

    # seg1: 1 clock cycle of cathodic stim
    seg1 = xp.StimSegment(1, 100, -1)

    # seg2: remaining cathodic + transition to off
    cath_remaining = 50 - clock_cycle
    cath_delay = math.floor(cath_remaining / delay_length)
    seg2 = xp.StimSegment(1, 100, -1, enable=False, delay=cath_delay)

    # seg3: interphase interval (2 full clock cycles)
    seg3 = xp.StimSegment(2, 0, 1, enable=False)

    # seg4: transition from off to anodic
    ipi_remaining = 100 - (clock_cycle - cath_delay * delay_length) - 2 * clock_cycle
    ipi_delay = math.floor(ipi_remaining / delay_length)
    seg4 = xp.StimSegment(1, 100, 1, delay=ipi_delay)

    # seg5: finish anodic phase
    anod_remaining = 50 - (clock_cycle - ipi_delay * delay_length)
    anod_delay = math.floor(anod_remaining / delay_length)
    seg5 = xp.StimSegment(1, 100, 1, enable=False, delay=anod_delay)

    seq0 = xp.StimSeq(elec, 1000, 30, seg1, seg2, seg3, seg4, seg5)
    xp.StimSeq.send(seq0)
    time.sleep(0.1)
```

### Example 3: Multi-Channel Synchronous Stimulation

```python
import xipppy as xp
import time

def stimWaveform(stim_channel, pulse_width, stim_mag_steps, stim_res):
    """Create a biphasic cathodic-first single pulse.
    
    Args:
        stim_channel: channel number (zero-indexed)
        pulse_width: width of each phase in clock cycles
        stim_mag_steps: amplitude in steps
        stim_res: resolution index
    """
    xp.stim_enable_set(False)
    time.sleep(0.001)
    xp.stim_set_res(stim_channel, stim_res)
    xp.stim_enable_set(True)

    pseg = xp.StimSegment(pulse_width, stim_mag_steps, -1)
    ipi = xp.StimSegment(round(pulse_width / 2), 0, 1, enable=False)
    nseg = xp.StimSegment(pulse_width, stim_mag_steps, 1)

    seq0 = xp.StimSeq(stim_channel, 1000, 1, pseg, ipi, nseg)
    return seq0

with xp.xipppy_open(use_tcp=True):
    stimList = xp.list_elec('stim')

    seq_arr = []
    elecs = [0, 15]

    for elec in elecs:
        seq_arr.append(stimWaveform(elec, 200, 50, 4))

    # Send all sequences synchronously
    xp.StimSeq.send_stim_seqs(seq_arr)
    time.sleep(0.1)
```

### Example 4: Continuous Data Acquisition

```python
import xipppy as xp
import numpy as np
import matplotlib.pyplot as plt

with xp.xipppy_open():
    fs_clk = 30000
    xp.signal_set(0, 'raw', True)
    elec_0_raw = xp.cont_raw(300, [0], 0)
    t = np.arange(0, 300000 / fs_clk, 1000 / fs_clk, dtype=np.float32)
    plt.plot(t, elec_0_raw[0])
    plt.xlabel('Time (ms)')
    plt.title('Raw Signal for electrode 0')
    plt.show()
```

---

## Key Differences from xippmex (MATLAB)

| Feature | xippmex (MATLAB) | xipppy (Python) |
|---------|------------------|-----------------|
| **Electrode indexing** | 1-based | **0-based** |
| **Open/Close** | `xippmex('open')` / `xippmex('close')` | `xp._open()` / `xp._close()` or `with xp.xipppy_open():` |
| **Context manager** | Not available | `with xp.xipppy_open():` (preferred, reentrant) |
| **Stim sequence** | `xippmex('stimseq', params)` | `xp.StimSeq(elec, period, repeats, *segments)` then `xp.StimSeq.send(seq)` |
| **StimSegment** | Struct-based | `xp.StimSegment(length, amp, pol, ...)` class |
| **Send stim** | `xippmex('stimseq', ...)` | `xp.StimSeq.send(seq)` or `xp.StimSeq.send_stim_seqs([seq1, seq2])` |
| **Digital out** | `xippmex('digout', ...)` | `xp.digout(outputs, values)` |
| **Digital in** | `xippmex('digin')` | `xp.digin(max_events)` |
| **List electrodes** | `xippmex('elec', type)` | `xp.list_elec(type)` |
| **Continuous data** | `xippmex('cont', ...)` | `xp.cont_raw(...)` / `xp.cont_lfp(...)` etc. |
| **Stim resolution** | `xippmex('stim', 'res', ...)` | `xp.stim_set_res(elec, level)` |
| **Stim enable** | `xippmex('stim', 'enable', ...)` | `xp.stim_enable_set(True/False)` |
| **Trial control** | `xippmex('trial', ...)` | `xp.trial(oper=129, status='recording', ...)` |
| **Time** | `xippmex('time')` | `xp.time()` |
| **Fast settle** | `xippmex('fastsettle', ...)` | `xp.fast_settle(fe, fs_type, item, duration)` |
| **Impedance** | `xippmex('impedance', ...)` | `xp.impedance(elecs)` |
| **Error handling** | Return codes | `XippPyException` |

---

## Important Notes

1. **Asynchronous property changes:** `stim_enable_set`, `signal_set`, `filter_set` etc. are asynchronous — the change must propagate across the XIPP network. Poll for the desired value if timing is critical.

2. **Stim safety:** The Ripple processor disables stimulation on an entire Front End if:
   - More than 8 patterns are queued
   - A `STIM_AT_TIME` command is received too early (>2 seconds before target time)

3. **Stim segments are passed as variadic args**, NOT as a Python list:
   ```python
   # CORRECT:
   seq = xp.StimSeq(elec, 1000, 30, seg1, seg2, seg3)
   
   # WRONG:
   seq = xp.StimSeq(elec, 1000, 30, [seg1, seg2, seg3])
   ```

4. **Period/frequency conversion:**
   - Period in clock cycles = (1 / desired_freq_Hz) × 30000
   - 30 Hz → period = 1000
   - 100 Hz → period = 300
   - 200 Hz → period = 150

5. **Current calculation:**
   - Total current = resolution (µA/step) × amplitude (steps)
   - E.g., resolution index 3 on Micro (10 µA/step) × amplitude 100 = 1000 µA = 1 mA
