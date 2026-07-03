# System Architecture

## Introduction

OpenEdge-S3 is a modular **System-on-Module (SoM)** designed to simplify the development of long-range IoT and edge computing systems.

Rather than functioning as a traditional development board, OpenEdge-S3 is intended to be directly integrated onto a custom motherboard using **castellated edges** or a **board-to-board connector**. The module encapsulates the complex portions of hardware design—including wireless communication, GNSS positioning, onboard storage, USB connectivity, and firmware support—allowing developers to focus on application-specific hardware.

By combining processing, positioning, storage, and communication into a single reusable module, OpenEdge-S3 reduces hardware development time while providing a scalable platform suitable for both prototyping and production deployments.

The architecture is designed to support a wide range of applications, including industrial automation, environmental monitoring, smart agriculture, asset tracking, remote telemetry, and distributed edge computing.

---

## Design Goals

The architecture of OpenEdge-S3 is guided by the following principles.

### Modular Integration

The module is designed as a reusable hardware building block that integrates directly onto a custom motherboard. By encapsulating the RF, GNSS, storage, and processing subsystems into a single module, developers can rapidly build products without redesigning complex circuitry for every project.

### Production Ready

The hardware is developed with manufacturing and long-term deployment in mind. Standardized interfaces, compact integration, and simplified motherboard requirements make the module suitable for both prototyping and commercial products.

### Long-Range Connectivity

OpenEdge-S3 provides reliable long-range wireless communication through LoRa while simultaneously supporting Wi-Fi and Bluetooth for local networking, device provisioning, diagnostics, and firmware updates.

### Edge Computing

Instead of acting solely as a communication device, the module performs local data processing, filtering, aggregation, and storage. Processing data at the edge reduces network traffic, improves response time, and enables autonomous operation in remote environments.

### Hardware Expansion

The module exposes standardized communication interfaces and GPIOs, allowing the host motherboard to extend functionality with additional peripherals while maintaining firmware compatibility across future hardware revisions.

### Open Development

The software ecosystem is designed to support both **ESP-IDF** and **Arduino IDE**, enabling professional firmware development alongside rapid prototyping and educational use.

---

## System Overview

OpenEdge-S3 combines multiple hardware subsystems into a compact System-on-Module that serves as the communication and processing core of an embedded system.

Rather than integrating sensors, displays, power electronics, or application-specific hardware directly onto the module, OpenEdge-S3 provides the common infrastructure required by connected embedded devices. This modular approach allows a single communication platform to be reused across multiple products while reducing development time and simplifying hardware design.

The module is organized into five primary functional subsystems:

- **Processing** – ESP32-S3 application processor responsible for system control, edge computing, and peripheral management.
- **Wireless Communication** – LoRa for long-range communication together with Wi-Fi and Bluetooth for local connectivity.
- **Positioning** – Multi-constellation GNSS receiver providing accurate location and timing information.
- **Storage** – External QSPI Flash and MicroSD support for firmware, data logging, and offline operation.
- **Expansion Interface** – Standardized hardware interface allowing the host motherboard to access communication buses, GPIOs, and future expansion capabilities.

The host motherboard contains the application-specific electronics such as sensors, displays, industrial interfaces, actuators, battery management, or custom circuitry, while OpenEdge-S3 provides a reusable computing and communication platform.

This separation enables multiple products to share the same firmware ecosystem and communication architecture while requiring only application-specific motherboard designs.

---

## High-Level System Architecture

```text
                     +--------------------------------------+
                     |          Host Motherboard            |
                     |--------------------------------------|
                     | Sensors                              |
                     | Displays                             |
                     | Industrial Interfaces                |
                     | Motor Drivers                        |
                     | Battery Management                   |
                     | Application Hardware                 |
                     +------------------+-------------------+
                                        │
                           OpenEdge Expansion Interface
                                        │
        ==========================================================
        ||                OpenEdge-S3 System-on-Module          ||
        ==========================================================
                                        │
        -----------------------------------------------------------
        │               │               │              │
        │               │               │              │
   ESP32-S3         LoRa Radio      GNSS Receiver   Storage
        │               │               │              │
 Wi-Fi / BLE        Long Range       Positioning   Flash + SD
        │
 GPIO • SPI • I²C • UART • USB • PWM • ADC
```
---

# Hardware Architecture

OpenEdge-S3 is designed around a modular hardware architecture where each subsystem performs a dedicated function while remaining tightly integrated through the ESP32-S3.

Instead of exposing a bare microcontroller, the module combines wireless communication, positioning, storage, power management, and peripheral interfaces into a single reusable System-on-Module. This significantly reduces motherboard complexity while maintaining flexibility for application-specific designs.

The hardware architecture consists of the following functional blocks:

- Processing Subsystem
- Wireless Communication Subsystem
- Positioning Subsystem
- Storage Subsystem
- USB Interface
- Power Management
- Expansion Interface

Each subsystem is described below.

---

## Processing Subsystem

The ESP32-S3 serves as the primary application processor and system controller.

It is responsible for coordinating all onboard peripherals while simultaneously providing wireless networking and sufficient processing power for edge computing applications.

### Responsibilities

- Application execution
- Peripheral management
- Communication scheduling
- Local data processing
- Sensor fusion
- Wireless networking
- Storage management
- Power management

### Available Peripherals

- Wi-Fi
- Bluetooth Low Energy (BLE)
- USB
- SPI
- I²C
- UART
- PWM
- ADC
- DMA
- GPIO
- Interrupt Controller

The ESP32-S3 also provides sufficient computational capability to perform local analytics before transmitting information over the LoRa network.

---

## Wireless Communication Subsystem

Long-range communication is provided through the LoRa transceiver connected via SPI.

The communication subsystem is designed to operate independently of the application hardware, allowing developers to reuse the same wireless platform across multiple products.

### Current Hardware

- SX1262 LoRa Transceiver

### Planned Hardware

Future revisions will migrate to a pre-certified LoRa module to simplify RF design, improve compliance, and reduce PCB complexity.

### Responsibilities

- Packet Transmission
- Packet Reception
- RSSI Measurement
- Link Quality Monitoring
- Channel Activity Detection
- Network Communication

The communication layer is intentionally isolated from the application layer, allowing future firmware libraries to support custom protocols without requiring hardware modifications.

---

## Positioning Subsystem

Positioning is provided through the onboard u-blox MAX-M10S multi-constellation GNSS receiver.

Besides geographical positioning, GNSS also provides an accurate UTC time reference that can be used for timestamping, synchronization, and scheduled communication.

### Available Data

- Latitude
- Longitude
- Altitude
- UTC Time
- Ground Speed
- Satellite Information
- Fix Status

Communication with the ESP32-S3 is performed through a dedicated UART interface.

---

## Storage Subsystem

OpenEdge-S3 provides both high-speed onboard storage and removable storage for flexible data management.

### External QSPI Flash

The onboard flash memory is intended for persistent system storage.

Typical applications include:

- Firmware Assets
- Configuration Files
- OTA Firmware Images
- Persistent Data
- Edge Computing Cache

### MicroSD Storage

The MicroSD interface provides removable mass storage for large datasets.

Typical applications include:

- Sensor Logging
- GPS Track Recording
- Environmental Data
- Offline Operation
- Diagnostic Logs

Combining both storage options enables the module to continue operating even when network connectivity is unavailable.

---

## USB Interface

USB Type-C provides a modern interface for development, firmware updates, debugging, and power delivery.

Integrated ESD protection improves system robustness during development and field deployment.

### Functions

- Firmware Programming
- USB Serial Communication
- Device Debugging
- Power Input

Future firmware revisions may also expose additional USB device classes depending on application requirements.

---

## Power Management

The module is powered from a 5 V input supplied either by USB Type-C or the host motherboard.

An onboard 3.3 V regulator powers all digital subsystems including the ESP32-S3, LoRa transceiver, GNSS receiver, flash memory, and supporting peripherals.

The power architecture incorporates dedicated decoupling capacitors positioned near each subsystem to improve supply stability and reduce high-frequency noise.

Future hardware revisions are expected to introduce:

- Battery Charging
- Battery Monitoring
- RTC Backup Supply
- Low-Power Operating Modes
- Power Path Management
