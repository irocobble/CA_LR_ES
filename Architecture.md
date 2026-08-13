# System Architecture

## Introduction

The **Acoustic Sensing Node** is a single-board embedded platform for acoustic
search-and-rescue. It is designed to be physically inserted into rubble or
debris, detect human distress signals, estimate the direction those signals came
from, and relay a GPS-tagged alert over a long-range radio link — fully
self-contained, with no cable, no network, and no operator nearby.

All subsystems — microphone array, edge inference, positioning, inertial sensing,
radio, and power — are consolidated onto one PCB. Integration over modularity is
deliberate: a unit that gets thrown or dropped into debris has fewer failure
points with fewer interconnects.

---

## Design Goals

### Direction, Not Just Detection

The node's headline output is a **bearing**, not a boolean. A rescue team acting
on "survivor at bearing 42° from node 3" can dig in a specific place; a team
acting on "sound detected" cannot. This drove the choice of a six-microphone
phase-coherent array and a processor with hardware array-processing support.

### Hardware-Accelerated Acoustic Processing

The Kendryte K210's **APU (Audio Processing Unit)** implements microphone-array
processing in silicon: sound source localization, beamforming, and voice
detection, backed by a dedicated FFT accelerator. Doing direction-of-arrival in
hardware rather than in a DSP loop keeps the compute budget available for
classification.

### Split of Concerns Across Two Processors

The K210 owns the acoustic path end-to-end — it is the I2S master for the mic
array and runs beamforming and classification. The ESP32-S3 owns everything else
— radio, positioning, inertial sensing, USB and power. Neither processor
duplicates the other's work, and the link between them carries results only.

### Cross-Modal False-Positive Rejection

A 9-axis IMU sits alongside the acoustic front end. Tapping and banging produce
both an acoustic signature and a mechanical impact signature; requiring both
substantially reduces false alarms from wind, settling debris, and machinery.

### Field Deployability

GNSS positioning and timestamping, LoRa reporting, onboard power regulation, and
a ruggedized enclosure allow the node to operate unattended in an outdoor,
uncontrolled environment.

---

## System Overview

The schematic is organized into five functional blocks:

| Block | Contents |
|---|---|
| **ESP32** | ESP32-S3 MCU, 40 MHz crystal, W25Q128 flash, decoupling |
| **K210 Module** | Sipeed M1 module, CP2102N USB-UART bridge, boot/reset |
| **Communication** | SX1262 LoRa, MAX-M10S GNSS, CH334F USB hub |
| **Other** | 6× INMP441 microphone array, MPU-9250 IMU, status LEDs |
| **USB + Power** | USB-C receptacle, ESD, 12 MHz crystal, AP63203 buck |

```text
        ┌──────────────────────────────────────────────────────────┐
        │                   ACOUSTIC SENSING NODE                  │
        │                                                          │
        │   ┌──────────────┐                                       │
        │   │ 6x INMP441   │                                       │
        │   │ mic array    │   SCK_MIC · WS · SD_1 · SD_2 · SD_3   │
        │   └──────┬───────┘                                       │
        │          │ I2S (K210 = master)                           │
        │          ▼                                               │
        │   ┌──────────────────────┐                               │
        │   │  K210 / Sipeed M1    │◀── CP2102N ◀── USB hub        │
        │   │  · APU beamforming   │      (ISP)         ▲          │
        │   │  · DoA estimation    │                    │          │
        │   │  · classification    │                    │          │
        │   └──────────┬───────────┘                    │          │
        │              │ QSPI ×6 (results only)         │          │
        │              ▼                                │          │
        │   ┌──────────────────────┐                    │          │
        │   │     ESP32-S3         │◀── native USB ─────┤          │
        │   │  · alert formatting  │                    │          │
        │   │  · system control    │              ┌─────┴──────┐   │
        │   └──┬────────┬───────┬──┘              │  CH334F    │   │
        │      │ SPI    │ UART  │ I2C             │  USB hub   │   │
        │      ▼        ▼       ▼                 └─────┬──────┘   │
        │  ┌───────┐ ┌──────┐ ┌──────────┐              │          │
        │  │SX1262 │ │MAX-  │ │MPU-9250  │        ┌─────┴──────┐   │
        │  │ LoRa  │ │M10S  │ │  IMU     │        │  USB-C     │   │
        │  └───┬───┘ └──┬───┘ └──────────┘        │  + ESD     │   │
        │      │        │                         └─────┬──────┘   │
        │     J4       J1                               │          │
        │   (ANT)    (GNSS)                        AP63203 buck    │
        │                                          5V → 3V3        │
        └──────────────────────────────────────────────────────────┘
```

---

# Hardware Architecture

## Processing Subsystem

### K210 / Sipeed M1 — Acoustic Processor

**Responsibilities**

- I2S master for the six-microphone array — drives `SCK_MIC` and `WS`, reads
  three data lines
- Beamforming and direction-of-arrival estimation via the APU
- Spectrogram generation and acoustic event classification
- Reporting results to the ESP32-S3 over QSPI

The module accepts **5 V** and generates its own internal 1.8 V and 3.3 V rails.
Pins 53 and 54 are regulator *outputs*, not inputs.

The K210's FPIOA (Field Programmable IO Array) maps any peripheral function to
any of IO0–IO47 in firmware, so pin assignment was chosen for layout convenience
rather than fixed silicon constraints. Pins 56–71 (LCD_D0–7, DVP_D0–7) are
dedicated interfaces outside the FPIOA and are unused in this design.

### ESP32-S3 — System Host

**Responsibilities**

- Receiving detection results from the K210 over QSPI
- Driving the SX1262 LoRa radio over SPI
- Reading GNSS position and time from the MAX-M10S over UART
- Reading the MPU-9250 IMU over I2C for impact cross-validation
- Formatting and queueing GPS-tagged alerts for transmission
- USB enumeration, status indication, and power sequencing

Programming and debug use the **native USB Serial/JTAG controller** on GPIO19/20,
reached through port 1 of the CH334F hub. No external bridge is required for the
S3.

---

## Acoustic Sensing Subsystem

### Microphone Array

Six INMP441 digital I2S MEMS microphones in a fixed-geometry array. Each
microphone's `L/R` pin selects which half of a stereo frame it drives, allowing
two microphones to share one data line:

| Data line | K210 pin | Left (L/R → GND) | Right (L/R → 3V3) |
|---|---|---|---|
| `SD_1` | IO24 | U1 | U2 |
| `SD_2` | IO25 | U5 | U6 |
| `SD_3` | IO26 | U15 | U16 |

| Clock | K210 pin |
|---|---|
| `WS` (word select) | IO23 |
| `SCK_MIC` (bit clock) | IO27 |

All six microphones sample off a single shared bit clock, so every channel is
**phase-coherent by construction**. This is the property direction-of-arrival
estimation depends on, and it is why digital MEMS microphones were selected over
an analog array behind a multiplexed SAR ADC — a multiplexed converter samples
channels sequentially, injecting inter-channel skew directly into the timing
measurement the algorithm is trying to recover.

Each microphone has a local 0.1 µF decoupling capacitor and a 10 k pull-up on
`EN`.

### Direction-of-Arrival

Direction is recovered from **time difference of arrival** across the array: a
wavefront reaches each microphone at a slightly different instant, and the
inter-microphone delays determine the source angle. The K210's APU implements
this in hardware.

### Classification

A lightweight classifier distinguishes human distress signatures (tapping,
banging, shouting) from background noise, consuming spectrogram features
generated on the K210. The `gray8` library produces 8-bit palette-indexed
spectrogram images for training-data capture and offline inspection; the dB
window (`g8_scale_init`) is the dominant readability parameter.

---

## Wireless Communication Subsystem

An SX1262 LoRa transceiver driven by the ESP32-S3 over SPI.

| Signal | ESP32-S3 | SX1262 |
|---|---|---|
| `NSS` | GPIO4 | pin 15 |
| `SCK` | GPIO5 | pin 12 |
| `MOSI` | GPIO6 | pin 14 |
| `MISO` | GPIO7 | pin 13 |
| `BUSY` | GPIO9 | pin 10 |
| `DIO1` (IRQ) | GPIO10 | pin 6 |

The antenna path leaves via `ANT` (pin 1) through a matching network to coaxial
connector J4.

**Payload constraints.** LoRa is a low-bandwidth link. Only derived results cross
it — event class, confidence, bearing, GNSS fix and timestamp. Spectrograms and
raw audio are never transmitted; a full spectrogram BMP is roughly 51 KB and is
suitable only for local storage.

**Planned:** migration to a pre-certified LoRa module to simplify RF design and
regulatory compliance.

---

## Positioning Subsystem

A u-blox MAX-M10S GNSS receiver on UART to the ESP32-S3.

| Signal | GNSS | ESP32-S3 |
|---|---|---|
| GNSS TXD | pin 2 | GPIO16 |
| GNSS RXD | pin 3 | GPIO15 |

An active-antenna bias-T (L1 27 nH, R8 10 Ω) feeds `VCC_RF` from the antenna
node at coaxial connector J1.

**Available data:** latitude, longitude, altitude, UTC time, ground speed,
satellite count, fix status. GNSS time timestamps detected events; position
geotags them before transmission.

---

## Inertial Subsystem

An MPU-9250 9-axis IMU (accelerometer, gyroscope, magnetometer) in I2C mode —
`CS` is strapped to 3V3, with 2.2 k pull-ups on SDA and SCL.

Its role is cross-modal validation: an impact signature coincident with an
acoustic detection is far stronger evidence of a survivor tapping than either
alone. It also detects the node's own settling or displacement within the rubble.

---

## Storage Subsystem

A W25Q128JVS 128 Mbit QSPI flash on the ESP32-S3's dedicated SPI0 pins, with
series termination resistors (R15–R19) on the bus. Used for firmware,
configuration, OTA images, and model storage.

---

## USB Interface

A single USB Type-C receptacle feeds a CH334F hub, giving both processors
programming access over one physical connection without opening the enclosure.

| Hub port | Endpoint | Purpose |
|---|---|---|
| Port 1 | ESP32-S3 GPIO19/20 | Native USB — flash, serial, JTAG |
| Port 4 | CP2102N | Serial ISP for the K210 |

The K210 has no USB peripheral; it boots over serial, which is why the CP2102N
bridge exists. Type-C CC1/CC2 carry 5.1 k sink resistors; a USBLC6-4SC6 provides
ESD protection on the upstream data pair.

---

## Power Management

```text
USB-C VBUS ──▶ F2 (2 A polyfuse) ──▶ FB2 ──▶ D11 ──▶ +5V
                                    (ferrite)  (Schottky,
                                                reverse block)
                                                    │
                        ┌───────────────────────────┤
                        ▼                           ▼
                 AP63203WU buck              K210 module (5V in)
                        │
                        ▼
                      +3V3 ──▶ ESP32-S3, SX1262, MAX-M10S,
                               MPU-9250, flash, mic array
```

Local decoupling is placed at every subsystem, which matters given the mixed
digital/RF/analog nature of the board.

**Planned for the next revision**

- Li-Po connector, charge IC and protection, OR-ed into the system rail at D11
- Battery voltage monitoring via a divider into **ADC1** (GPIO1–GPIO10 only;
  ADC2 is unusable while Wi-Fi is active)
- 5 V boost from battery plus a load switch gating the K210, enabling duty-cycled
  operation — the K210 draws roughly 300–400 mW under load, so gating it is the
  difference between half a day and weeks of runtime
- RTC backup supply for GNSS warm starts

---

## Inter-Processor Link — QSPI

A six-wire quad SPI bus between the K210 and the ESP32-S3, ESD-protected on every
line (D15–D20).

| Signal | ESP32-S3 GPIO | S3 pin | K210 IO | M1 pin |
|---|---|---|---|---|
| `QSPI_CLK` | GPIO37 | 42 | IO36 | 37 |
| `QSPI_CS` | GPIO38 | 43 | IO37 | 38 |
| `QSPI_D0` | GPIO33 | 38 | IO32 | 33 |
| `QSPI_D1` | GPIO34 | 39 | IO33 | 34 |
| `QSPI_D2` | GPIO35 | 40 | IO34 | 35 |
| `QSPI_D3` | GPIO36 | 41 | IO35 | 36 |

**Direction: K210 is master, ESP32-S3 is slave.** The K210's SPI slave peripheral
supports standard mode only — it has no quad slave mode — so making the ESP32 the
master would silently degrade the link to a single data line. On the ESP32-S3
this requires **SPI Slave HD** (half-duplex) mode, which does support quad lines.
A handshake line should be added so the slave can signal data-ready rather than
being polled blind.

GPIO33–37 are the ESP32-S3's `SPIIO4`–`SPIIO7`/`SPIDQS` pins. They are free on a
bare die and on quad-PSRAM variants, but are consumed internally on **octal**
PSRAM variants (R8). The chosen variant must be confirmed before layout. Because
these pins belong to the flash/PSRAM controller, the link is driven by **SPI2
through the GPIO matrix**, not by the flash peripheral.

**No raw audio crosses this link.** The microphone array feeds the K210 directly,
so the bus carries only derived results.

---

## Known Open Items

Outstanding in the current schematic; all must be closed before layout.

| # | Item |
|---|---|
| 1 | K210 pins 53/54 (1V8 and 3V3 module **outputs**) are tied to rails already driven by U12 and U14 — two regulators output-to-output on each rail |
| 2 | No battery or charging block; `+5V` is USB-only, so the K210 cannot run untethered |
| 3 | The `RST` net is shared between LoRa reset, K210 reset and CP2102N RTS — no independent reset control, and opening a serial port resets the radio |
| 4 | CP2102N TXD / RXD / DTR are unconnected — the K210 cannot be flashed |
| 5 | GNSS `RF_IN` (U4.11) is unconnected; the bias-T feeds `VCC_RF` but no DC-blocked path reaches the receiver input |
| 6 | LoRa DIO2/DIO3 drive status LEDs — on most SX1262 modules DIO2 is RF-switch control and DIO3 is the TCXO supply |
| 7 | LoRa `TX_EN` / `RX_EN` are no-connect |
| 8 | IMU SDA/SCL never reach the ESP32-S3; `AD0` (address select) and `INT` are floating |
| 9 | Approximately 52 K210 pins lack no-connect flags, making ERC output unusable |
| 10 | GNSS UART nets are still named `C+` / `C-`, colliding with USB-C naming conventions |

---

## Mechanical

A custom enclosure protects the assembly during insertion into rubble while
allowing sound to reach the microphone ports with minimal attenuation or
reflection.

**Requirements**

- Acoustic ports aligned to all six microphone locations, sealed against dust and
  water without damping the acoustic path
- Clearance and keep-out for the GNSS and LoRa antennas
- Impact tolerance — the node is thrown or dropped into debris
- Ingress protection appropriate to a wet, dusty, unstable environment
- Mounting holes and board retention that survive shock loading

Mounting holes, mic port cutouts, and antenna keep-out regions must be placed
during PCB layout, not retrofitted afterwards.
