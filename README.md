# ESP32 Ambilight Controller

A high-performance, multi-mode ambient lighting system developed for ESP32 microcontrollers and WS2812B LED strips. This project provides a complete solution for synchronizing computer screen visuals with background lighting, featuring a robust Python desktop client and efficient ESP32 firmware.

## Key Features

- **Dual Connection Architecture**:
  - **USB Serial**: Low-latency wired connection for high-speed synchronization.
  - **WebSocket via WiFi**: Wireless operation for flexible setup and deployment.
- **Advanced Screen Capture & Processing**:
  - **Screen Mapping**: Individual LED-to-screen-region mapping for precise spatial lighting.
  - **Intelligent color modes**: Dominant Color, Average Color, Most Vibrant, Warm/Cool Bias, and more.
  - **Smoothing Algorithms**: Adjustable color transition smoothing for a pleasing visual experience.
- **Comprehensive Configuration**:
  - **On-board Web Portal**: Configure WiFi, LED count, brightness, and startup settings directly on the ESP32.
  - **Desktop GUI**: Modern, dark-themed user interface (using `ttkbootstrap`) for real-time control and calibration.
- **Interactive Calibration**: Visual point-and-click calibration tool to map LEDs to physical screen locations.
- **System Integration**: Supports multi-monitor setups and custom capture regions.

## Hardware Requirements

- **Microcontroller**: ESP32 Development Board (e.g., ESP32-WROOM-32).
- **LEDs**: WS2812B (Neopixel) addressable LED strip.
- **Power Supply**: 5V external power supply adequate for the number of LEDs (recommended 60mA per LED max).
- **Connections**:
  - Data line resistor (330Ω) recommended.
  - Power filtering capacitor (1000µF) recommended.

## Software Prerequisites

### Firmware

- Arduino IDE
- **Required Libraries**:
  - FastLED
  - WebSockets (Markus Sattler)
  - ArduinoJson
  - ESPAsyncWebServer & AsyncTCP

### Desktop Client

- Python 3.8 or higher
- Required Python packages (see `requirements.txt`):
  - `tkinter` / `ttkbootstrap`
  - `numpy`
  - `Pillow`
  - `pyserial`
  - `websocket-client`

## Installation

### 1. Firmware Setup (ESP32)

1.  Open `esp32_ambilight.ino` in the Arduino IDE.
2.  Install the required libraries via the Library Manager or ZIP import.
3.  Configure your hardware settings in the code if necessary (default Data Pin: GPIO 5).
4.  Upload the sketch to your ESP32 board.
5.  **Initial Config**: Connect to the ESP32's WiFi Access Point (`ESP32-Ambilight`) and navigate to `http://192.168.4.1` to configure your home WiFi and LED settings.

### 2. Desktop Client Setup

1.  Clone this repository or download the source code.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

### Running the Controller

Launch the application using Python:

```bash
python main.py
```

### Connection Modes

- **USB Mode**: Connect the ESP32 to your PC. Select "USB" in the application, choose the appropriate COM port, and click **Connect**.
- **WebSocket Mode**: Ensure the ESP32 and your PC are on the same network. Select "WebSocket", enter the ESP32's IP address (displayed in the Serial Monitor or Web Portal), and click **Connect**.

### Calibration

1.  Mount your LED strip on your monitor.
2.  In the application, go to the **Calibration** section.
3.  Click **Start Calibration**. The application will light up individual LEDs.
4.  Click the corresponding location on the screen canvas to map the LED to that position.
5.  Save your configuration.

## Project Structure

- `main.py`: Application entry point.
- `gui.py`: Main user interface implementation.
- `esp32_ambilight.ino`: ESP32 firmware source code.
- `connection_manager.py`: Handles Serial and WebSocket communications.
- `image_processor.py`: Core logic for screen capture and color calculation.
- `effects.py`: Built-in lighting effects engine.
- `simulator.py`: Software simulator for testing without hardware.

## License

This project is open-source and available under the **MIT License**.
