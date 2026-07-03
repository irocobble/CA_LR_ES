# ESP32-S3 LoRa Edge Node

A compact, modular **ESP32-S3 based edge computing platform** designed for long-range IoT deployments. The board combines **LoRa communication, GNSS positioning, onboard storage, and hardware expansion** to provide a flexible foundation for remote sensing, asset tracking, and distributed edge applications.

---

## Features

* ESP32-S3 Dual-Core MCU
* Long-range LoRa Communication
* u-blox MAX-M10S GNSS Receiver
* 128 Mbit External QSPI Flash
* MicroSD Card Support
* USB Type-C Power & Programming
* 3.3 V Regulated Power Supply
* GPIO Expansion Interface
* USB ESD Protection

---

## Hardware

| Component  | Device                                           |
| ---------- | ------------------------------------------------ |
| MCU        | ESP32-S3                                         |
| LoRa       | SX1262 *(planned migration to certified module)* |
| GNSS       | u-blox MAX-M10S                                  |
| Flash      | W25Q128JVSIQ                                     |
| Storage    | MicroSD Card                                     |
| USB        | USB Type-C                                       |
| Protection | USBLC6-4SC6                                      |
| Regulator  | LM1117-3.3                                       |

---

## Repository Structure

```text
.
├── Schematic/
├── PCB/
├── Libraries/
├── Datasheets/
├── Firmware/
├── BOM/
├── README.md
└── architecture.md
```

---

## Project Status

### Hardware

* [x] System Design
* [x] Complete Schematic
* [x] Power Supply
* [x] USB-C Interface
* [x] ESP32-S3 Integration
* [x] LoRa Integration
* [x] GNSS Integration
* [x] External Flash
* [x] MicroSD Interface
* [x] GPIO Expansion

### PCB

* [ ] PCB Layout
* [ ] RF Optimization
* [ ] Design Rule Check
* [ ] Fabrication
* [ ] Assembly
* [ ] Bring-up & Validation

### Firmware

* [ ] ESP-IDF Project
* [ ] LoRa Driver
* [ ] GNSS Driver
* [ ] Storage Drivers
* [ ] USB Support
* [ ] Edge Computing Framework

---

## Applications

* Smart Agriculture
* Environmental Monitoring
* Asset Tracking
* Industrial IoT
* Wildlife Monitoring
* Remote Sensor Networks
* Edge Computing

---

## Documentation

Detailed information about the hardware architecture, communication interfaces, firmware structure, and expansion system can be found in **architecture.md**.

---

## Roadmap

* Migrate to a certified LoRa module
* Add battery charging and power monitoring
* Improve RF performance
* Add onboard debugging interface
* Expand firmware libraries for ESP-IDF and Arduino

---

## License

This project is currently under active development.
