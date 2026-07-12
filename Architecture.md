# System Architecture

## Introduction

The **Acoustic Doppler Sensing Edge Node** is a modular, stacked embedded platform designed for long-range acoustic sensing, sound-source localization, and onboard classification.

Rather than existing as a single monolithic board, the system is split across two physically stacked PCBs connected by a 21-pin castellated edge interface: a **4-layer compute board** carrying the ESP32-S3 and GNSS, and a **2-layer sensing/RF board** carrying the ESP32-C3, a 4-microphone array, and the LoRa radio. This split isolates the expensive 4-layer stackup to only the subsystem that needs it (the S3 and its high-speed peripherals), while the sensing and RF hardware live on a cheaper 2-layer board.

The architecture is designed to support acoustic monitoring applications where a node must detect a sound event, estimate its direction of arrival, classify it, and report it over a long-range link — autonomously and in the field.

---

## Design Goals

### Staged Development

The system is built incrementally across four stages — compute/GNSS hardware, sensing/RF hardware, a Doppler-based direction-of-arrival (DoA) library, and an onboard classifier — so that hardware bring-up and algorithm development can proceed in parallel without blocking each other.

### Cost-Optimized Stacking

By separating the S3's high-speed subsystem (which requires a 4-layer board for signal integrity) from the mic array and LoRa RF section (which can run on 2 layers), the overall bill of materials and fabrication cost is reduced without compromising the compute board's performance.

### Long-Range Reporting

The SX1262 LoRa radio, physically located on the sensing board, is driven directly by the ESP32-S3 across the castellated edge — giving the compute board full control of the radio while keeping the RF section physically close to the antenna and away from the S3's high-speed QSPI/PSRAM traces.

### Edge Intelligence

All acoustic preprocessing, Doppler DoA computation, and classification run locally on the ESP32-S3. The ESP32-C3 is intentionally kept "dumb" — its only job is sampling the 4-mic array and streaming raw data to the S3. This keeps the compute-heavy work on the board with the horsepower (S3 + PSRAM) and keeps the C3 board simple and cheap.

### Field Deployability

GNSS positioning and timestamping, MicroSD logging, battery support, and a 3D-printed enclosure (Stage 3) are included so the node can operate autonomously and unattended in outdoor/remote environments.

### Open Development

Firmware for both the S3 and C3 targets ESP-IDF, keeping the two boards' firmware in the same build ecosystem despite running different roles.

---

## System Overview

The platform is organized into two physical boards, each owning a distinct set of subsystems:

**Board 1 — Compute & GNSS (4-layer)**
- ESP32-S3 application processor — preprocessing, Doppler DoA, classification, LoRa control
- u-blox MAX-M10S GNSS receiver
- LED status indicators
- External QSPI flash + Quad PSRAM
- USB Type-C (power & programming)
- 3.3V regulation, USB ESD protection

**Board 2 — Sensing & RF (2-layer)**
- ESP32-C3 — dedicated mic ADC sampling only, no onboard DSP
- 4x microphone array (analog front end)
- SX1262 LoRa transceiver (RF hardware only; controlled by the S3 over the castellated edge)
- MicroSD card storage
- USB Type-C + USB hub (shared programming access for both S3 and C3)
- Battery support

The two boards communicate exclusively through the **21-pin castellated edge connector**, which carries mic data (QSPI), LoRa control (SPI + IRQ/reset), and shared power/GPIO.

---

## High-Level System Architecture

```text
                     +-----------------------------------------------+
                     |          Board 1 — 4-Layer (Compute)          |
                     |-----------------------------------------------|
                     | ESP32-S3                                      |
                     | u-blox MAX-M10S GNSS                          |
                     | LED Indicators                                |
                     | QSPI Flash + PSRAM                            |
                     | USB Type-C (power/program)                    |
                     |    → Preprocessing, Doppler DoA,              |
                     |      Classification, LoRa Control             |
                     +----------------------+------------------------+
                                            │
                              21-Pin Castellated Edge
                       (J1: Power/Free · J2: QSPI · J3: LoRa)
                                            │
                     +----------------------+------------------------+
                     |          Board 2 — 2-Layer (Sensing/RF)       |
                     |-----------------------------------------------|
                     | ESP32-C3 (mic ADC only)                       |
                     | 4x Microphone Array                           |
                     | SX1262 LoRa Transceiver                       |
                     | MicroSD Storage                               |
                     | USB Hub (S3 + C3 programming)                 |
                     | Battery Support                               |
                     +-----------------------------------------------+
```

---

# Hardware Architecture

## Processing Subsystem

### ESP32-S3 (Board 1 — Primary Compute)

The ESP32-S3 is the system's central processor, responsible for all computation, decision-making, and communication.

**Responsibilities**
- Reading mic-array data streamed from the ESP32-C3 over the castellated edge QSPI link
- Running the Doppler DoA library (Stage 3) to estimate sound source direction
- Running the onboard classifier (Stage 4) to flag human vs. non-human sound events
- Driving the SX1262 LoRa radio (physically on Board 2) over SPI + IRQ/reset lines
- Reading GNSS position/time from the MAX-M10S for event timestamping and geotagging
- Managing external QSPI flash and PSRAM for buffering/model storage
- Driving LED status indicators

### ESP32-C3 (Board 2 — Mic ADC Front End)

The ESP32-C3 performs one job only: sampling the 4-microphone array and streaming the digitized data to the ESP32-S3. It runs no DSP, DoA computation, or classification — all of that is deferred to the S3 to keep this board's firmware and hardware minimal.

**Responsibilities**
- ADC sampling of the 4-mic analog front end
- Streaming sampled mic data to the S3 over the castellated-edge QSPI link
- Basic housekeeping (e.g. USB hub arbitration for shared programming access)

---

## Wireless Communication Subsystem

Long-range communication is provided by an SX1262 LoRa transceiver, physically located on Board 2 but fully controlled by the ESP32-S3 on Board 1 via the J3 bank of the castellated edge (SCK, MISO, MOSI, NSS, BUSY, DIO1, RESET).

**Current Hardware**
- SX1262 LoRa Transceiver

**Planned Hardware**
- Migration to a pre-certified LoRa module to simplify RF design and improve regulatory compliance

**Responsibilities**
- Packet transmission of localization/classification results
- RSSI / link quality monitoring
- Channel activity detection

---

## Acoustic Sensing Subsystem

### Microphone Array (Board 2)

Four analog microphones form a fixed-geometry array. Inter-mic timing and amplitude differences are the raw input to the Stage 3 Doppler DoA library.

### Mic ADC Path (ESP32-C3)

The C3 samples all 4 mic channels and streams the digitized data to the S3 over QSPI (J2 bank: GPIO35/34/37/36/33, plus GPIO21 as clock/strobe). No filtering or DSP is performed on the C3 — raw samples are passed upstream.

### Doppler DoA Library (Stage 3, runs on S3)

A firmware library that consumes the streamed 4-channel mic data and estimates the direction of a sound source using Doppler-effect analysis (inter-mic timing/frequency shift). This is the core sensing algorithm of the platform.

### Classification (Stage 4, runs on S3)

A lightweight human/non-human classifier consumes the DoA stage's output to flag and localize detections in real time, before results are queued for LoRa transmission.

---

## Positioning Subsystem

Positioning is provided by the onboard u-blox MAX-M10S GNSS receiver on Board 1, communicating with the ESP32-S3 over UART.

**Available Data**
- Latitude / Longitude / Altitude
- UTC Time
- Ground Speed
- Satellite Information
- Fix Status

GNSS time is used to timestamp detected acoustic events; position is used to geotag them before transmission over LoRa.

---

## Storage Subsystem

**External QSPI Flash + PSRAM (Board 1)**
Used for firmware assets, configuration, OTA images, and buffering during Doppler DoA / classification processing.

**MicroSD Storage (Board 2)**
Used for logging raw or preprocessed acoustic events, GNSS tracks, and diagnostic data, enabling offline operation when LoRa connectivity is degraded or unavailable.

---

## USB Interface

Both boards expose USB Type-C. Board 2 includes a USB hub so a single physical connection can be used to program/debug both the ESP32-S3 (Board 1) and ESP32-C3 (Board 2) once stacked, without requiring separate cabling to each board.

**Functions**
- Firmware programming (S3 and C3)
- USB serial communication / debugging
- Power input

---

## Power Management

The stacked assembly is powered from 5V via USB Type-C (with battery support added on Board 2). An onboard 3.3V regulator supplies all digital subsystems across both boards — ESP32-S3, ESP32-C3, LoRa transceiver, GNSS receiver, flash, and mic front end — with power delivered across the castellated edge (J1 bank).

Decoupling capacitors are placed locally at each subsystem on both boards to maintain supply stability, particularly important given the mixed digital/RF/analog nature of the stack.

**Planned for future revisions**
- Battery charging management
- Battery level monitoring
- RTC backup supply
- Low-power / duty-cycled operating modes for extended field deployment

---

## Board Interconnect — 21-Pin Castellated Edge

The castellated edge is the sole electrical link between Board 1 and Board 2, organized into three logical 7-pin banks:

**J1 — Power / Free**: +3V3, GND, and 4 general-purpose lines (GPIO2, GPIO3, GPIO10, GPIO38, GPIO15) reserved for future expansion or board-specific control signals.

**J2 — QSPI (Mic Data)**: 5 data/strobe lines (GPIO35, GPIO34, GPIO37, GPIO36, GPIO33) plus GPIO21, carrying the streamed mic-array data from the C3 to the S3.

**J3 — LoRa Control**: Full SX1262 control interface — SCK, MISO, MOSI, NSS (GPIO4), BUSY (GPIO9), DIO1 (IRQ), and RESET — allowing the S3 to fully drive the radio despite it being physically located on Board 2.

This interconnect is the primary integration risk in the design and will require signal integrity validation (particularly for the QSPI mic link and LoRa SPI) once both boards are fabricated and stacked.

---

## Mechanical — Stage 3 Enclosure

In parallel with the Doppler DoA firmware work, Stage 3 includes development of a custom 3D-printed enclosure for the stacked board assembly, intended to protect the mic array's acoustic ports and the GNSS/LoRa antennas while allowing sound to reach the microphones with minimal attenuation or reflection.
