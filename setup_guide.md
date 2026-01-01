# ESP32 Multi-Mode Ambilight Setup Guide

## Features

This ambilight system supports **three connection modes**:
- **USB Serial** - Direct wired connection, fastest response
- **WebSocket (WiFi)** - Wireless via your home network
- **Bluetooth Classic** - Wireless without WiFi dependency

All settings are configurable through the **ESP32's internal web portal**.

---

## Hardware Requirements

### Components
- **ESP32 Dev Board** (ESP32-WROOM-32 or similar)
- **WS2812B LED Strip** (16-300 LEDs)
- **5V Power Supply** (60mA per LED × LED count)
- USB cable for programming and USB mode

### Wiring
```
ESP32 GPIO 5     →  LED Strip Data In
Power Supply 5V  →  LED Strip 5V
Power Supply GND →  LED Strip GND + ESP32 GND (shared ground!)
```

> ⚠️ **Important:** Always connect common ground between ESP32, power supply, and LED strip.

---

## Installation

### Step 1: ESP32 Firmware

**Required Arduino Libraries:**
1. Open Arduino IDE
2. Install ESP32 board support (Tools → Board → Boards Manager → ESP32)
3. Install these libraries via Library Manager:
   - **FastLED** (by Daniel Garcia)
   - **WebSockets** (by Markus Sattler) 
   - **ArduinoJson** (by Benoit Blanchon)
   - **ESPAsyncWebServer** - Install from [GitHub](https://github.com/me-no-dev/ESPAsyncWebServer)
   - **AsyncTCP** - Install from [GitHub](https://github.com/me-no-dev/AsyncTCP)

**Upload Firmware:**
1. Open `esp32_ambilight.ino` in Arduino IDE
2. Select Board: **Tools → Board → ESP32 Dev Module**
3. Select correct COM port
4. Click **Upload**

### Step 2: Python Application

```bash
pip install -r requirements.txt
```

**Required packages:**
- `websocket-client` - WebSocket communication
- `numpy` - Image processing
- `Pillow` - Screen capture
- `pyserial` - USB serial communication

**Optional for Bluetooth:**
```bash
pip install PyBluez
```
Note: PyBluez requires Visual C++ Build Tools on Windows.

---

## Initial Configuration

### Connect to ESP32 Access Point

1. Power on ESP32
2. On your phone/PC, connect to WiFi: **"ESP32-Ambilight"** (open network)
3. Open browser: `http://192.168.4.1`

### Configure Settings

The web interface shows:

**WiFi Settings:**
- Enter your home WiFi SSID and password
- Optionally set an AP password for security

**LED Settings:**
- Set the number of LEDs in your strip
- Adjust default brightness

**Connection Modes:**
- Enable/disable USB Serial
- Enable/disable WebSocket (WiFi)
- Enable/disable Bluetooth
- Set Bluetooth device name

Click **Save Configuration** and restart when prompted.

---

## Using the Application

### Start the Controller

```bash
python ambilight_controller.py
```

### Connection Modes

**USB Mode:**
1. Connect ESP32 to PC via USB
2. Select "USB" from mode dropdown
3. Choose COM port and click Connect

**WebSocket Mode:**
1. Ensure ESP32 is connected to WiFi (check Serial Monitor for IP)
2. Select "WebSocket" from mode dropdown
3. Enter ESP32 IP address and click Connect

**Bluetooth Mode:**
1. Enable Bluetooth on ESP32 via web config
2. Pair ESP32 with your PC in Windows Bluetooth settings
3. Select "Bluetooth" and enter device name
4. Click Connect (may take a few seconds to scan)

### LED Calibration

1. Click **Start Calibration**
2. LED 0 will blink white on your strip
3. Click on the canvas where that LED is positioned relative to your screen
4. Repeat for all LEDs
5. Calibration auto-saves

### Capture Modes

| Mode | Description |
|------|-------------|
| **Screen Map** | Each LED samples its calibrated screen position |
| **Average Color** | All LEDs show the average screen color |
| **Dominant Color** | Most vibrant/saturated color on screen |
| **Edge Sampling** | Samples from screen edges (for perimeter strips) |
| **Quadrant Colors** | Divides screen into 4 zones |
| **Most Vibrant** | Single most saturated pixel color |
| **Warm Bias** | Average color shifted warmer (+red, -blue) |
| **Cool Bias** | Average color shifted cooler (-red, +blue) |

---

## Troubleshooting

### Can't connect to ESP32 AP
- Ensure ESP32 is powered
- Check Serial Monitor (115200 baud) for startup messages
- Try resetting ESP32

### Wrong LED colors
- Check `COLOR_ORDER` in firmware (GRB for most WS2812B)
- Re-run calibration for Screen Map mode

### WebSocket won't connect
- Verify ESP32 is on the same network
- Check IP address in Serial Monitor
- Ping the ESP32: `ping 192.168.x.x`

### Bluetooth won't pair
- Make sure Bluetooth is enabled in web config
- Remove and re-pair device in Windows Bluetooth settings
- Try specific Bluetooth name match

### LEDs flickering
- Add 1000µF capacitor across power supply
- Use shorter data wire
- Add 330Ω resistor between GPIO 5 and LED data

---

## File Reference

| File | Purpose |
|------|---------|
| `esp32_ambilight.ino` | ESP32 firmware (upload to device) |
| `ambilight_controller.py` | Python desktop application |
| `simulator.py` | Test simulator (no hardware needed) |
| `requirements.txt` | Python dependencies |
| `ambilight_config.json` | Saved calibration (auto-generated) |

---

## Power Calculations

| LED Count | Max Power | Recommended PSU |
|-----------|-----------|-----------------|
| 16 LEDs | 1A @ 5V | 5V 2A |
| 60 LEDs | 3.6A @ 5V | 5V 5A |
| 100 LEDs | 6A @ 5V | 5V 8A |
| 150 LEDs | 9A @ 5V | 5V 10A |

Typical usage is ~40% of maximum.

---

## Quick Test Without Hardware

Run the simulator:
```bash
python simulator.py
```

Then run the controller and connect to `127.0.0.1` using WebSocket mode.