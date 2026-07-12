#Team Members
1] Aninda Majumdar(Git-https://github.com/irocobble)
2] Subham Basak(Git-)
3] Kartik Kajra(Git-)
# Acoustic Doppler Sensing Edge Node

A **staged, stackable edge computing platform** for long-range acoustic sensing and localization. The system splits compute, RF, and sensing across two interconnected boards, moving from a single monolithic node toward a modular, cost-optimized architecture with onboard sound-source localization and classification.

---

## Architecture Overview

The platform is built in four development stages, moving from hardware bring-up to onboard intelligence:

| Stage | Description | Status |
|---|---|---|
| **1** | 4-layer board — ESP32-S3, GPS, LED indicators | In development |
| **2** | 2-layer board — ESP32-C3, 4-mic array, SX1262 LoRa | In development |
| **3** | Doppler-effect DSP library for sound source direction-of-arrival | Planned |
| **4** | Onboard human/non-human sound classifier | Planned |

**Board 1** and **Board 2** are not independent nodes — Board 1 is soldered directly onto Board 2 as a stacked daughterboard, connected via a 21-pin castellated edge interface. Board 2 provides sensing (mic array) and RF (LoRa), while Board 1 provides compute (S3), GNSS, and status indication. The ESP32-S3 owns all preprocessing, Doppler DoA computation, classification, and LoRa communication; the ESP32-C3 exists solely to manage ADC conversion of the 4-mic array.

```
┌──────────────────────────────┐
│   Board 1 (4-layer, top)     │
│   ESP32-S3 · GPS · LEDs      │
│   → preprocessing, DoA,      │
│     classification, LoRa TX  │
└──────────────┬───────────────┘
               │ 21-pin castellated edge
┌──────────────┴────────────────┐
│   Board 2 (2-layer, bottom)   │
│   ESP32-C3 · 4x Mic Array ·   │
│   SX1262 LoRa                 │
│   → mic ADC sampling only     │
└───────────────────────────────┘
```

---

## Stage 1 — Compute & GNSS Board (4-layer)

* ESP32-S3 dual-core MCU — runs preprocessing, Doppler localization, and classification
* u-blox MAX-M10S GNSS receiver
* LED status indicators (ESP, LoRa TX/RX, GPS, power)
* 128 Mbit external QSPI flash
* External Quad PSRAM
* USB Type-C power & programming
* 3.3V regulated power supply
* USB ESD protection

## Stage 2 — Sensing & RF Board (2-layer)

* ESP32-C3 — dedicated to ADC conversion of the mic array only, no onboard DSP
* 4x microphone array (analog front end)
* MicroSD card support
* USB Type-C power & programming
* USB HUB For S3 and C3 Programming
* Battery Support
* SX1262 LoRa transceiver (RF section on this board; driven over SPI by the S3 on Board 1)
* Cost-reduced 2-layer stackup

## Stage 3 — Doppler DoA Library & 3D printing the case

*Firmware library consuming the 4-mic array stream to estimate sound source direction using Doppler-effect analysis (inter-mic timing/frequency shift). Runs on the ESP32-S3.
*To develop a Custom case for enclosing the PCB

## Stage 4 — Onboard Classification

Lightweight human vs. non-human sound classifier running on the S3, consuming the output of the Doppler DoA stage to flag and localize detections in real time.

---

## Board Interconnect — 21-Pin Castellated Edge

Board 1 and Board 2 connect through a 21-pin castellated edge, grouped into three 7-pin banks:

**J1 — Power / Free**
| Pin | Signal |
|---|---|
| 1 | +3V3 |
| 2 | GPIO2 |
| 3 | GPIO3 |
| 4 | GPIO10 |
| 5 | GPIO38 |
| 6 | GPIO15 |
| 7 | GND |

**J2 — QSPI (Mic Data, C3 → S3)**
| Pin | Signal |
|---|---|
| 1 | GPIO35 |
| 2 | GPIO34 |
| 3 | GPIO37 |
| 4 | GPIO36 |
| 5 | GPIO33 |
| 6 | GPIO21 |
| 7 | +3V3 / GND |

**J3 — LoRa (SX1262 Control, S3 → Board 2)**
| Pin | Signal |
|---|---|
| 1 | SCK |
| 2 | MISO |
| 3 | MOSI |
| 4 | NSS (GPIO4) |
| 5 | BUSY (GPIO9) |
| 6 | DIO1 |
| 7 | RESET |

---

## Hardware Summary

| Component | Device | Board |
|---|---|---|
| MCU (Compute) | ESP32-S3 | Board 1 |
| MCU (ADC) | ESP32-C3 | Board 2 |
| GNSS | u-blox MAX-M10S | Board 1 |
| LoRa | SX1262 *(planned migration to certified module)* | Board 2 |
| Mic Array | 4x analog microphones | Board 2 |
| Flash | W25Q128JVSIQ (128 Mbit QSPI) | Board 1 |
| Storage | MicroSD card | Board 1 |
| USB | USB Type-C | Board 1 |
| USB Protection | USBLC6-4SC6 | Board 1 |
| Regulator | LM1117-3.3 | Board 1 |

---

## Repository Structure

```text
.
├── PCB/
│   ├── Board1_4Layer/
│   └── Board2_2Layer/
├── Libraries/
│   └── DopplerDoA/
├── Datasheets/
├── Firmware/
│   ├── Board1_S3/
│   └── Board2_C3/
├── BOM/
├── README.md
├── Future_Plan.md
└── architecture.md
```

---

## Project Status

### Hardware — Board 1 (4-layer)
- [x] System design
- [x] Complete schematic
- [x] Power supply
- [x] USB-C interface
- [x] ESP32-S3 integration
- [x] GNSS integration
- [x] External flash
- [x] MicroSD interface
- [x] Castellated edge connector (21-pin)
- [ ] PCB layout
- [ ] Design rule check
- [ ] Fabrication

### Hardware — Board 2 (2-layer)
- [x] ESP32-C3 integration
- [x] LoRa (SX1262) integration
- [ ] 4-mic array front-end schematic
- [ ] Castellated edge connector (21-pin)
- [ ] PCB layout
- [ ] RF optimization
- [ ] Design rule check
- [ ] Fabrication

### Firmware
- [ ] ESP-IDF project scaffolding (S3 + C3)
- [ ] Mic ADC driver (C3)
- [ ] QSPI inter-board data link
- [ ] LoRa driver (S3 → SX1262 over castellated edge)
- [ ] GNSS driver
- [ ] Storage drivers
- [ ] Doppler DoA library (Stage 3)
- [ ] Classification model integration (Stage 4)

---

## Applications

* Wildlife / bioacoustic monitoring
* Perimeter and intrusion detection
* Search and rescue acoustic triangulation
* Environmental noise source mapping
* Remote sensor networks with human-presence detection

---

## Documentation

Detailed hardware architecture, board interconnect signal mapping, firmware structure, and the DoA/classification pipeline are documented in **architecture.md**.

---

## Roadmap

* Finalize Board 2 mic array front-end
* Migrate SX1262 to a certified LoRa module
* Route and fabricate both boards
* Validate castellated edge interconnect (signal integrity across QSPI + LoRa SPI)
* Implement and benchmark Doppler DoA library
* Train and deploy onboard classification model
* Add battery charging and power monitoring
* Add onboard debugging interface

---

## License

This project is currently under active development.
