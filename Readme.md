# Team Members

1. Aninda Majumdar — https://github.com/irocobble
2. Subham Basak
3. Kartik Kajra

---

# Acoustic Sensing Node

A **two-board, field-deployable acoustic sensor node** for search-and-rescue.
The node is placed into rubble or debris, listens for human distress signals
(tapping, banging, shouting), estimates the **direction** the sound came from,
and relays a GPS-tagged alert over LoRa to a rescue coordination dashboard.

Search-and-rescue is a race against time, and the hardest part is knowing where
to dig. Rather than sending people into unstable rubble to listen, this sends the
ears in first.

---

## Architecture Overview

The system is split across **two boards** that solder together at an 11-pin
castellated edge. The split is deliberate and is the main cost lever in the
design — see [Why Two Boards](#why-two-boards).

| Board | Layers | Size | Carries |
|---|---|---|---|
| **Board 1 — Mic Array** | 2 | Ø100 mm | 8× INMP441, Kendryte K210 (Sipeed M1), 12 V→5 V and 12 V→3V3 regulation |
| **Board 2 — Main** | 4 | 50 × 75 mm | ESP32, SX1262 LoRa, MAX-M10S GNSS, MPU-9250 IMU, USB hub + 2 bridges, 12 V→3V3 |

| Processor | Role |
|---|---|
| **Kendryte K210** (Sipeed M1) | Acoustic capture and inference — I2S master for the array, beamforming, direction-of-arrival, classification |
| **ESP32** (QFN-48) | System host — LoRa control, GNSS, IMU, USB, alert formatting |

The K210 was selected because its **APU implements microphone-array processing in
hardware** — beamforming across 16 directions and sound-source localisation, with
a dedicated FFT unit. Just as importantly, a single K210 I2S peripheral carries
**four data lines from one clock generator**, so all eight microphones are
phase-coherent by construction with no synchronisation to configure.

```
                 ┌──────────────────────────────────────────┐
                 │  BOARD 1 — Mic Array  (2-layer, Ø100 mm) │
   8× INMP441 ───┤                                          │
   circular      │  I2S: 4 data lines, 1 shared clock       │
   array r=45mm  │            ↓                             │
                 │  K210 / Sipeed M1                        │
                 │   · APU beamforming + DOA                │
                 │   · event classification                 │
                 └───────────────┬──────────────────────────┘
                                 │  11-pin castellated edge
                                 │  12 V · mic bus · 2× UART · boot/reset
                 ┌───────────────┴──────────────────────────┐
                 │  BOARD 2 — Main  (4-layer, 50 × 75 mm)   │
                 │  ESP32 ── SX1262 LoRa      → U.FL        │
                 │        ── MAX-M10S GNSS    → U.FL        │
                 │        ── MPU-9250 IMU                   │
                 │        ── Wi-Fi/BLE        → U.FL        │
                 │  USB-C → CH334F hub → 2× CP2102N         │
                 └──────────────────────────────────────────┘
```

The IMU is deliberate: accelerometer data cross-validates acoustic
tapping/banging detections. An impact signature and a sound signature together
reduce false positives in a way an acoustic-only pipeline cannot.

---

## Why Two Boards

The two halves of the design have **opposite requirements**:

| | Mic array | Main electronics |
|---|---|---|
| Size driver | **Acoustics** — radius fixed by physics | **Density** — as small as routing allows |
| Area | Ø100 mm (7 850 mm²) | 50 × 75 mm (3 750 mm²) |
| Signals | 6 slow I2S lines, no RF | 3 RF chains, USB, high pin density |
| Layers needed | **2** | **4** |

On one board, the *entire* area would have to be 4-layer — because the LoRa,
GNSS and Wi-Fi feeds need a solid ground reference and 50 Ω controlled impedance.
That means paying 4-layer prices for 7 850 mm² whose only job is holding
microphones 45 mm from the centre.

PCB price scales with **area × layer count**. Taking a 4-layer board at roughly
2.2× the per-area cost of 2-layer (confirm against the actual quote — the ratio
is the assumption, not the method):

| Approach | Area × rate | Relative cost |
|---|---|---|
| Monolithic — one 4-layer board ~100 × 100 mm | 10 000 × 2.2 | **22 000** |
| **Split** — 2-layer Ø100 + 4-layer 50 × 75 | (7 850 × 1) + (3 750 × 2.2) | **16 100** |

**≈ 25–30 % off bare PCB cost**, and the gap widens as the array radius grows.

Benefits that don't appear on the invoice:

- **Cheap iteration** — array geometry is the least-certain part of the design.
  Re-spinning the mic board costs a 2-layer board, not a 4-layer one.
- **Contained failure** — a damaged K210 scraps one cheap board, not the node.
- **Assembly yield** — every fine-pitch part (QFN-48 at 0.35 mm, QFN-28, QFN-24)
  is on the small board.
- **RF isolation, free** — three antennas physically separated from eight
  microphone traces.

**The trade:** the castellated interface is a new failure point and can't be
probed once assembled, which is why only slow signals cross it — no RF, no
high-speed bus.

---

## Hardware Summary

### Board 1 — Mic Array (`Board1_2layers/`)

| Function | Device | Ref |
|---|---|---|
| Inference / acoustic front end | Sipeed M1 (Kendryte K210) | U12 |
| Microphone array | 8× INMP441 I2S MEMS | U1–U6, U15, U16 |
| Buck 12 V → 5 V (K210) | AP63205WU | U14 |
| Buck 12 V → 3V3 (mics) | AP63203WU | U10 |
| Power input | JST-XH 2-pin, polarised | J1 |
| Board interface | 11-pin castellated, 3.2 mm pitch | J3 |

### Board 2 — Main (`board2_4layers/`)

| Function | Device | Ref |
|---|---|---|
| Host MCU | ESP32 (QFN-48, 5×5 mm) | U7 |
| External SPI flash | 4 Mbyte SPI NOR | U6 |
| LoRa transceiver | SX1262 module | U1 |
| GNSS receiver | u-blox MAX-M10S | U3 |
| IMU | MPU-9250 (9-axis) | U4 |
| USB hub | CH334F | CH334F1 |
| USB-UART bridges (ESP32 + K210 ISP) | 2× CP2102N | U8, U9 |
| USB ESD protection | USBLC6-4SC6 | U2 |
| Buck 12 V → 3V3 | AP63203WU | U5 |
| Antennas | 3× U.FL — LoRa, GNSS, Wi-Fi | J2, J4, J5 |
| USB connector | USB Type-C receptacle | J1 |

---

## Microphone Array

Eight INMP441 digital I2S MEMS microphones on a uniform circle, **r = 45.00 mm**,
45° apart, each rotated to face the centre so every microphone has identical
trace geometry to the hub.

Two microphones share each data line via the INMP441's `L/R` strap — one on the
left slot, one on the right slot of the same stereo frame.

| Data line | K210 pin | Mics | Angles |
|---|---|---|---|
| `SD_1` | IO20 | U1, U2 | 90°, 45° |
| `SD_2` | IO21 | U5, U6 | 0°, 315° |
| `SD_3` | IO22 | U15, U16 | 270°, 225° |
| `SD_4` | IO23 | U3, U4 | 180°, 135° |
| `SCK_MIC` | IO18 | shared bit clock | |
| `WS` | IO19 | shared word select | |

All eight sample off the same bit clock, so every channel is **phase-coherent** —
exactly what direction-of-arrival estimation requires, and the reason digital
MEMS mics were chosen over an analog array behind a multiplexed ADC.

Every K210 IO carries a **200 Ω series resistor and an ESD diode**, per Sipeed's
requirement for the M1 module.

> **Acoustic ports face down.** Each mic sits over a 1.05 mm hole through the
> PCB, so sound enters from the **bottom** face. Enclosure openings go there.

**Spatial aliasing:** adjacent spacing is `2r·sin(22.5°) = 34.4 mm`, giving
`f_alias = c / (2 × 0.0344) ≈ 5.0 kHz`. DOA correlation is band-limited below
that.

---

## Board-to-Board Interface

11 positions, 3.2 mm pitch, castellated. Board 2 solders onto Board 1 — no
connector, no cable.

| Pin | Net | Direction | Purpose |
|---|---|---|---|
| 1 | `+12V` | → B2 | Unregulated; each board regulates locally |
| 2 | `SCK_MIC` | K210 → ESP32 | Mic bit clock |
| 3 | `WS` | K210 → ESP32 | Mic word select |
| 4 | `SD1` | mics → ESP32 | One mic pair for the ESP32 wake detector |
| 5 | `UART_TX` | ESP32 → K210 | Command link |
| 6 | `UART_RX` | K210 → ESP32 | Results: class, confidence, bearing |
| 7 | `RX` | K210 → CP2102N | K210 ISP transmit |
| 8 | `TX` | CP2102N → K210 | K210 ISP receive |
| 9 | `Boot` | CP2102N → K210 | IO16 boot-mode select |
| 10 | `RST` | CP2102N → K210 | Reset (1.8 V domain) |
| 11 | `GND` | — | Return |

`TX` and `RX` are named **from the USB bridge's point of view** on both boards.

**No raw audio crosses the interface.** The mic array feeds the K210 directly;
the link carries only derived results.

---

## Power

```
12 V in (JST-XH, polarised)
   │
   ├── Board 1 ── AP63205 ──▶ +5V   → Sipeed M1 (K210)
   │           └─ AP63203 ──▶ +3V3  → 8× INMP441
   │
   └── via J3 pin 1 ──▶ Board 2 ── AP63203 ──▶ +3V3
                                     → ESP32, SX1262, MAX-M10S, MPU-9250, flash
```

Only 12 V and GND cross the interface, so there is no IR drop on a regulated rail
and no shared ground bounce between the analog and RF sections.

**Estimated ≈ 3.3 W total (~320 mA @ 12 V)**, dominated by the K210 at ~1.5 W.

---

## Repository Structure

```text
.
├── Board1_2layers/Mic+MPU/       # KiCad 9 — mic array board (2-layer)
│   ├── Mic.pretty/               # INMP441 footprint (incl. acoustic port)
│   └── Connectors.pretty/        # 11-pin edge footprint
├── board2_4layers/               # KiCad 9 — main board (4-layer)
│   └── LORA.pretty/ RF_GPS.pretty/ USB_HUB.pretty/ SMD_PAD.pretty/
├── Open_Source_Design/           # reference designs consulted
│   ├── Sipeed_M1/                # Maixduino schematic
│   └── USB_HUB/                  # CH334F reference
├── Firmware/
│   ├── K210/                     # kendryte-standalone-sdk
│   │   ├── Recorder/             # calibration + raw streaming
│   │   └── Detector/             # on-device VAD + DOA
│   └── ESP32/                    # LoRa, GNSS, IMU, alert path
├── Backend/
│   ├── capstone.py               # central entry point
│   ├── api_server.py             # /api/detections, /api/state, /api/health
│   ├── detector_monitor.py
│   └── dashboard.html            # radial polar display
├── Tools/
│   └── schcheck.py               # S-expression schematic checker
├── Datasheets/
├── BOM/
└── README.md
```

> The `Mic+MPU` folder name is historical — the MPU-9250 lives on Board 2.

---

## Project Status

### Hardware — Board 1 (2-layer)

- [x] Schematic complete, clean
- [x] 8-mic circular array placed at exact geometry (r = 45.000 mm, spread 0.000)
- [x] K210 module — power, ISP, boot/reset, 1.8 V RST domain handled
- [x] Per-IO 200 Ω series + ESD, per Sipeed requirement
- [x] 12 V → 5 V and 12 V → 3V3 regulation
- [x] PCB layout complete — all nets routed, no islands
- [ ] Fabrication

### Hardware — Board 2 (4-layer)

- [x] Schematic complete
- [x] ESP32 core — crystal, flash, straps, boot/reset, CAP network
- [x] LoRa, GNSS (active-antenna bias-T), IMU
- [x] USB-C → hub → 2× CP2102N, auto boot/reset for both processors
- [x] PCB layout — GND on F.Cu/B.Cu/In1.Cu, +3V3 on In2.Cu
- [x] DRC clean
- [ ] Fabrication

### Firmware

- [x] Two CMake projects: `-DPROJ=Recorder` (calibration/streaming) and
      `-DPROJ=Detector` (on-device VAD + DOA)
- [x] Working 4-mic DOA on the K210 prototype
- [x] Voice/non-voice classifier — logistic regression on 3 features
      (harmonicity, voice_band_ratio, SNR dB), running on-device as a dot product
      via generated `vad3_model.h` (`FEATURE_VERSION=4`)
- [ ] 8-channel capture on the new array
- [ ] Re-calibrate DOA constants for the circular geometry (see Open Items)
- [ ] APU hardware beamforming bring-up
- [ ] LoRa / GNSS / IMU drivers on the ESP32
- [ ] Acoustic + impact fusion

### Backend

- [x] `api_server.py` — ThreadingHTTPServer, HTTP/1.1 keep-alive
- [x] Endpoints: `/api/detections`, `/api/state`, `/api/health`, `/api/threshold`
- [x] `dashboard.html` — radial polar display
- [x] 15-second detection-window cap (hotspot lag fix)
- [ ] Multi-node triangulation view

---

## Open Items

| # | Item | Board | Impact |
|---|---|---|---|
| 1 | No-connect flags on U8.10 / U9.10 (CP2102N `NC` pins) | 2 | Cosmetic — clean ERC |
| 2 | U6 BOM value still names the original flash part | 2 | Update to the fitted part |
| 3 | DOA constants are for the old 40 mm square array | — | `DOA_SPACING_MM`, `SAMPLE_SHIFT` and both lag biases must be **re-measured** on the circular array |
| 4 | Channel→angle map is not in ring order | — | `SD_4`→D0, `SD_3`→D1, `SD_2`→D2, `SD_1`→D3. Build the table, confirm with a tap test |
| 5 | Battery operation | — | Node runs from 12 V only; boost + load switch planned |
| 6 | One M2.5 mounting hole per board | both | Add more before the enclosure is fixed |
| 7 | Enclosure | — | Acoustic ports on the bottom face, antenna clearance, drop survival |

---

## Firmware Notes

- **UART direction:** the ESP32 transmits on GPIO5, so the K210 must configure
  **IO31 as RX and IO30 as TX**.
- **Sub-sample interpolation is mandatory.** Maximum arrival-time difference
  across the array is `2r/c = 262 µs` — only ~4 samples at 16 kHz. Fit a parabola
  through the correlation peak and its neighbours, or bearings will quantise to
  roughly 15° steps.
- **GPIO12 on the ESP32 must stay unconnected** — it straps VDD_SDIO, and high at
  reset selects 1.8 V for the flash rail.
- **K210 bank voltages:** IO0–IO35 are 3.3 V, IO36–IO47 are 1.8 V. Every signal
  used is IO35 or below.
- **Flash mode:** start in DIO; QIO needs chip-specific quad-enable handling.
- **Measure, don't assume** — every DOA constant on the previous build was
  established by measurement, not from spec sheets. The same applies here.

---

## Applications

**Primary:** search-and-rescue — locating survivors under collapsed structures,
where multiple nodes across a site turn a large uncertain search area into a
short list of places worth digging.

**Secondary:** perimeter and intrusion detection · wildlife and bioacoustic
monitoring · environmental noise source mapping · remote sensor networks with
human-presence detection.

---

## Roadmap

* Fabricate and assemble both boards
* Bring up 8-channel capture and re-calibrate DOA on the circular array
* Evaluate the K210 APU hardware beamformer against software GCC-PHAT
* Port the classifier to the 8-mic feature front end
* LoRa / GNSS / IMU integration and the end-to-end alert path
* Multi-node triangulation on the dashboard
* Battery: boost + load switch so the K210 can be duty-cycled
* Custom enclosure — acoustic ports, antenna clearance, drop survival

---

## References

- Knapp & Carter (1976), *The Generalized Correlation Method for Estimation of
  Time Delay* — the GCC-PHAT foundation
- DiBiase (2000), SRP-PHAT — steered response power for reverberant environments
- Sipeed M1 Datasheet v1.12 — module pinout, bank voltages, RST constraints
- Espressif ESP32 Hardware Design Guidelines

---

## License

Under active development.
