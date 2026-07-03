# ESP32-S3 LoRa + GPS Development Board

An ESP32-S3 based IoT development board designed for long-range wireless communication, GPS positioning, and edge computing applications. The board integrates a LoRa transceiver, GNSS receiver, external QSPI Flash, MicroSD card support, USB-C connectivity, and expansion headers, making it suitable for asset tracking, environmental monitoring, smart agriculture, and other IoT applications.

---

## Features

- ESP32-S3 MCU
- SX1262 LoRa Transceiver
- u-blox MAX-M10S GPS Module
- External 128 Mbit QSPI Flash Memory
- MicroSD Card Interface
- USB Type-C Power & Programming
- 3.3V LDO Power Supply
- GPS Coaxial Antenna Connector
- Status LEDs
- Expansion Header for GPIOs
- ESD Protection on USB Interface

---

## Hardware Overview

| Block | Description |
|--------|-------------|
| MCU | ESP32-S3 microcontroller |
| LoRa | SX1262 transceiver for long-range communication |
| GPS | MAX-M10S GNSS receiver |
| Storage | External W25Q128 Flash + MicroSD Card |
| USB | USB Type-C with ESD protection |
| Power | 3.3V LDO regulator |
| Indicators | Power and LoRa status LEDs |
| Expansion | GPIO breakout connector |

---

## Project Structure

```
.
├── Schematic
|──├── PCB
   ├── Libraries
   ├── Datasheets
   ├── BOM
   └── README.md
```

---

## Main Components

| Component | Part Number |
|------------|-------------|
| MCU | ESP32-S3 |
| LoRa | SX1262IMLTRT |
| GPS | MAX-M10S |
| Flash Memory | W25Q128JVSIQ |
| Voltage Regulator | LM1117-3.3 |
| USB ESD Protection | USBLC6-4SC6 |
| USB Connector | USB Type-C |
| Fuse | 1A Polyfuse |

---

## Current Status

### Hardware

- [x] System Architecture
- [x] Component Selection
- [x] Complete Schematic
- [x] Power Supply Design
- [x] USB-C Interface
- [x] ESP32-S3 Integration
- [x] GPS Integration
- [x] External Flash Integration
- [x] MicroSD Interface
- [x] GPIO Expansion Header
- [x] LED Indicators

### PCB

- [ ] LoRa Module Migration
- [ ] PCB Layout
- [ ] RF Layout Optimization
- [ ] Design Rule Check (DRC)
- [ ] PCB Manufacturing
- [ ] Assembly
- [ ] Bring-up Testing

### Firmware

- [ ] ESP-IDF Project
- [ ] LoRa Driver
- [ ] GPS Driver
- [ ] Flash Driver
- [ ] SD Card Driver
- [ ] USB Support
- [ ] Testing

---

## Design Notes

### LoRa

The current design uses the **SX1262 bare transceiver**. It is recommended to migrate to a **pre-certified LoRa module** to simplify RF design and improve communication range.

### GPS

The MAX-M10S uses an external active/passive antenna through a coaxial connector with the recommended RF matching network.

### Storage

The board supports:

- External QSPI Flash
- MicroSD Card

for firmware storage and local data logging.

---

## Planned Improvements

- Replace SX1262 IC with a certified LoRa module
- Improve RF performance
- Add debugging interface
- Add battery charging circuitry
- Add power monitoring
- Add RTC backup supply
- Improve antenna matching network

---

## Applications

- IoT Gateway
- Smart Agriculture
- Environmental Monitoring
- Asset Tracking
- Wildlife Monitoring
- Industrial Sensor Nodes
- Smart City Infrastructure

---

## Software

Planned development environment:

- ESP-IDF
- FreeRTOS
- VS Code
- KiCad 9

---

## License

This project is currently under development.
