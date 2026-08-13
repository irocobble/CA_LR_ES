# Team Members

1. Aninda Majumdar — https://github.com/irocobble
2. Subham Basak
3. Kartik Kajra

---

# Acoustic Sensing Node

A **single-board, field-deployable acoustic sensor node** for search-and-rescue.
The node is designed to be inserted into rubble or debris, listen for human
distress signals (tapping, banging, shouting), estimate the **direction** the
sound came from, and relay a GPS-tagged alert over LoRa to a rescue coordination
dashboard.

Everything the node needs — sensing, edge inference, positioning, radio and power
— sits on one PCB. Search-and-rescue is a race against time, and the hardest part
is knowing where to dig. Rather than sending people into unstable rubble to
listen, this sends the ears in first.

---

## Architecture Overview

A **single board** carrying two processors with a clean split of duties:

| Processor | Role |
|---|---|
| **Kendryte K210** (Sipeed M1 module) | Acoustic capture and inference — mic array I2S master, beamforming, direction-of-arrival, classification |
| **ESP32-S3** | System host — LoRa control, GNSS, IMU, USB, power sequencing, alert formatting |

The K210 was selected because its **APU (Audio Processing Unit) implements
microphone-array processing in hardware** — sound source localization,
beamforming, and voice activity detection, with a dedicated FFT accelerator. For
a node buried in rubble, a *bearing* to the survivor is worth far more to a
rescue team than a binary "sound detected" flag, and the K210 produces that
without burning CPU cycles.

```
                    ┌─────────────────────────────────────────┐
                    │        Acoustic Sensing Node            │
                    ├─────────────────────────────────────────┤
                    │                                         │
  6x INMP441  ──I2S──▶  K210 / Sipeed M1                      │
  mic array         │   · APU beamforming + DoA               │
                    │   · event classification                │
                    │            │                            │
                    │          QSPI (6-wire)                  │
                    │            ▼                            │
                    │      ESP32-S3                           │
                    │   · alert formatting + LoRa control     │
                    │   · GNSS, IMU, USB, power               │
                    │      │        │        │                │
                    └──────┼────────┼────────┼────────────────┘
                           │        │        │
                        SX1262   MAX-M10S  MPU-9250
                        (LoRa)    (GNSS)    (IMU)
                           │        │
                          J4       J1        ← U.FL / coax
```

The IMU is deliberate: accelerometer data cross-validates acoustic
tapping/banging detections. An impact signature and a sound signature together
reduce false positives in a way an acoustic-only pipeline cannot.

---

## Hardware Summary

All parts below are present in `Accoustic_Sensing_Node.kicad_sch`.

| Function | Device | Ref |
|---|---|---|
| Inference / acoustic front end | Sipeed M1 (Kendryte K210) | U9 |
| Host MCU | ESP32-S3 (bare die, QFN56) | U8 |
| Microphone array | 6× INMP441 I2S MEMS | U1, U2, U5, U6, U15, U16 |
| LoRa transceiver | SX1262 | U3 |
| GNSS receiver | u-blox MAX-M10S | U4 |
| IMU | MPU-9250 (9-axis) | U10 |
| External flash | W25Q128JVS (128 Mbit QSPI) | U7 |
| USB hub | CH334F | CH334F1 |
| USB-UART bridge (K210 ISP) | CP2102N | U11 |
| USB ESD protection | USBLC6-4SC6 | U13 |
| Buck regulator (5V → 3V3) | AP63203WU | U14 |
| LDO (1V8) | LM1117MP-1.8 | U12 |
| USB connector | USB Type-C receptacle | J3 |
| Antenna connectors | Coaxial ×3 — GNSS, Wi-Fi, LoRa | J1, J2, J4 |

---

## Microphone Array

Six INMP441 digital I2S MEMS microphones on **three data lines**, using the
L/R tri-state pairing trick: each data line carries two mics, one strapped to the
left slot and one to the right slot of the same stereo frame.

| Data line | Left mic (L/R → GND) | Right mic (L/R → 3V3) |
|---|---|---|
| `SD_1` | U1 | U2 |
| `SD_2` | U5 | U6 |
| `SD_3` | U15 | U16 |

All six share `SCK_MIC` (bit clock) and `WS` (word select), both driven by the
K210 as I2S master. Because every microphone samples off the same bit clock, all
six channels are **phase-coherent** — which is exactly what direction-of-arrival
estimation requires, and the reason digital MEMS mics were chosen over an analog
array behind a multiplexed ADC.

This topology was breadboard-validated before being committed to the schematic.

---

## Inter-Processor Link — QSPI

The K210 reports results to the ESP32-S3 over a 6-wire quad SPI bus, ESD-protected
on every line (D15–D20).

| Signal | ESP32-S3 | K210 |
|---|---|---|
| `QSPI_CLK` | GPIO37 | IO36 |
| `QSPI_CS` | GPIO38 | IO37 |
| `QSPI_D0` | GPIO33 | IO32 |
| `QSPI_D1` | GPIO34 | IO33 |
| `QSPI_D2` | GPIO35 | IO34 |
| `QSPI_D3` | GPIO36 | IO35 |

**No raw audio crosses this link.** The mic array feeds the K210 directly, so the
bus carries only derived results — event class, confidence, and bearing.

---

## Repository Structure

```text
.
├── Accoustic_Sensing_Node/     # KiCad 9 project (schematic + layout)
├── Assets/
│   ├── symbols/                # custom symbols (INMP441, Sipeed M1, SX1262, MAX-M10S)
│   ├── footprints/
│   └── 3dmodels/
├── Open_Source_Design/         # reference designs consulted
│   ├── Sipeed_M1/              # Maixduino schematic
│   ├── USB_HUB/                # CH334F reference
│   └── ESP_32_S3_2Layer/
├── Firmware/
│   ├── ESP32_S3/               # ESP-IDF — LoRa, GNSS, IMU, alert path
│   └── K210/                   # standalone SDK — I2S capture, APU, inference
├── Libraries/
│   └── gray8/                  # spectrogram → BMP8 library (portable C99)
├── Datasheets/
├── BOM/
├── README.md
└── architecture.md
```

---

## Project Status

### Schematic

- [x] Power tree — USB-C input, 2 A polyfuse, reverse-blocking, 5V → 3V3 buck
- [x] ESP32-S3 core — crystal, decoupling, boot/reset, SPI flash
- [x] USB — Type-C, ESD, CH334F hub, native-USB path to ESP32-S3
- [x] Microphone array — 6× INMP441, L/R pairing, wired to K210
- [x] K210 module — 5V and GND connected
- [x] QSPI inter-processor link with ESD protection
- [x] LoRa SPI control interface + antenna connector
- [x] GNSS UART to host
- [ ] Open electrical items — see errata below
- [ ] ERC clean

### Layout / Fabrication

- [ ] PCB layout
- [ ] RF sections — 50 Ω controlled impedance for LoRa and GNSS
- [ ] Design rule check
- [ ] Fabrication

### Firmware

- [ ] K210 I2S multi-channel capture (master, 3 data lines)
- [ ] K210 APU beamforming / DoA bring-up
- [ ] QSPI link — K210 master, ESP32-S3 SPI Slave HD
- [ ] LoRa driver
- [ ] GNSS driver
- [ ] IMU driver + acoustic/impact fusion
- [ ] Event classifier

---

## Known Open Items (Errata)

Tracked honestly — these are outstanding in the current schematic and must be
closed before layout.

| # | Item | Impact |
|---|---|---|
| 1 | K210 pins 53/54 (1V8 / 3V3 **outputs**) tied to rails already driven by U12 and U14 | **Dual-source conflict** — two regulators output-to-output |
| 2 | No battery or charging block; `+5V` is USB-only | K210 cannot run untethered |
| 3 | `RST` net shared between LoRa reset, K210 reset and CP2102N RTS | No independent reset control |
| 4 | CP2102N TXD / RXD / DTR dangling | K210 cannot be flashed |
| 5 | GNSS `RF_IN` (U4.11) unconnected — bias-T feeds VCC_RF only | No GNSS fix |
| 6 | LoRa DIO2 / DIO3 driving status LEDs | DIO2 is RF-switch control, DIO3 is TCXO supply on most SX1262 modules |
| 7 | LoRa `TX_EN` / `RX_EN` no-connect | External PA/LNA cannot be steered |
| 8 | IMU I2C never reaches the ESP32-S3; `AD0` and `INT` floating | IMU unusable |
| 9 | ~52 K210 pins without no-connect flags | ERC output unreadable |
| 10 | GNSS UART nets still named `C+` / `C-` | Misleading; collides with USB naming |

---

## Applications

**Primary:** search-and-rescue — locating survivors trapped under collapsed
structures, where multiple nodes scattered across a site turn a large uncertain
search area into a short list of places worth digging.

**Secondary:**

- Perimeter and intrusion detection
- Wildlife / bioacoustic monitoring
- Environmental noise source mapping
- Remote sensor networks with human-presence detection

---

## Roadmap

* Close the errata list and reach a clean ERC
* Add battery charging, protection, and voltage monitoring
* Add 5V boost + load switch so the K210 can be duty-cycled on battery
* Migrate the SX1262 to a pre-certified LoRa module to simplify RF and compliance
* Route and fabricate the board
* Bring up K210 I2S capture and APU beamforming
* Train and deploy the acoustic event classifier
* Custom 3D-printed enclosure — acoustic ports, antenna clearance, drop survival

---

## License

Under active development.
