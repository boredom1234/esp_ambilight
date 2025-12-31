# ESP32 WiFi Ambilight Setup Guide

## 🚀 Quick Start

### Hardware Needed
- ESP32 Dev Board
- WS2812B LED Strip (30-100 LEDs recommended)
- 5V Power Supply (60mA per LED × LED count)
- WiFi Network (2.4GHz)

### Wiring
```
ESP32 GPIO 5     →  LED Strip Data In
Power Supply 5V  →  LED Strip 5V + ESP32 VIN (if using external power)
Power Supply GND →  LED Strip GND + ESP32 GND (MUST share common ground!)
```

**⚠️ Important:** 
- Always connect common ground between ESP32, power supply, and LED strip
- For 60+ LEDs, use external 5V power (USB won't provide enough current)

---

## 📦 Software Installation

### Step 1: ESP32 Firmware

**Required Arduino Libraries:**
1. Open Arduino IDE
2. Go to: **Tools → Manage Libraries**
3. Install these libraries:
   - **FastLED** (by Daniel Garcia)
   - **WebSockets** (by Markus Sattler)
   - **ESPAsyncWebServer** (by me-no-dev) - Install from [GitHub](https://github.com/me-no-dev/ESPAsyncWebServer)
   - **AsyncTCP** (by me-no-dev) - Install from [GitHub](https://github.com/me-no-dev/AsyncTCP)
   - **ArduinoJson** (by Benoit Blanchon)

**Configure and Upload:**
1. Open the ESP32 firmware code
2. **CRITICAL:** Update these lines with your WiFi credentials:
   ```cpp
   const char* ssid = "YOUR_WIFI_SSID";      // Your WiFi name
   const char* password = "YOUR_WIFI_PASSWORD"; // Your WiFi password
   ```
3. Set `NUM_LEDS` to match your strip length
4. Select Board: **Tools → Board → ESP32 Dev Module**
5. Select correct COM port
6. Click **Upload**

**Find ESP32 IP Address:**
- After upload, open Serial Monitor (115200 baud)
- ESP32 will display its IP address (e.g., `192.168.1.100`)
- **Write this down!** You'll need it for the desktop app

**If WiFi Fails:**
- ESP32 creates its own AP: "ESP32-Ambilight" (password: 12345678)
- Connect to this AP and use IP: `192.168.4.1`

---

### Step 2: Desktop Application

**Install Python Requirements:**
```bash
pip install websocket-client pillow numpy
```

**For Different Operating Systems:**

**Windows:**
```bash
pip install websocket-client pillow numpy
```

**macOS:**
```bash
pip3 install websocket-client pillow numpy
```

**Linux:**
```bash
sudo apt-get install python3-tk python3-pil.imagetk
pip3 install websocket-client pillow numpy
```

---

## 🎯 Complete Setup Process

### Phase 1: Physical Installation

1. **Plan LED Layout:**
   - Start LED strip from bottom-left or top-left of monitor
   - Run clockwise or counter-clockwise around perimeter
   - Keep LEDs evenly spaced
   - Remember where LED #0 starts!

2. **Mount LED Strip:**
   - Use 3M tape or mounting clips
   - Keep strip as close to screen edge as possible
   - Point LEDs toward the wall (not at you!)
   - Cable management for power/data wires

3. **Connect Power:**
   - Calculate power: (LED count × 60mA) = Total mA needed
   - Example: 60 LEDs × 60mA = 3600mA = 3.6A minimum
   - Use 5V power supply with adequate amperage
   - Double-check polarity!

### Phase 2: Software Connection

1. **Run Python App:**
   ```bash
   python ambilight_app.py
   ```

2. **Connect to ESP32:**
   - Enter the IP address from Serial Monitor
   - Click **"Connect"**
   - Status should turn green: "Connected ✓"
   - App automatically detects LED count

3. **Verify Connection:**
   - Click **"Test LED Pattern"**
   - LEDs should light up sequentially in RED
   - If nothing happens, check wiring and IP address

### Phase 3: Calibration (Most Important!)

This maps each physical LED to its screen position.

1. **Start Calibration:**
   - Click **"Start Calibration"**
   - LED #0 will blink WHITE on your strip

2. **Map Each LED:**
   - Look at your physical LED strip
   - Find the blinking LED
   - On the canvas, click where that LED is positioned relative to your screen
   - The next LED will start blinking
   - Repeat for all LEDs

3. **Tips for Accurate Mapping:**
   - Take your time - accuracy matters!
   - Click precisely where LED physically sits
   - The canvas represents your screen edges
   - If you make a mistake, restart calibration

4. **Save Configuration:**
   - Calibration auto-saves to ESP32
   - Click **"Save Config"** to backup locally
   - Configuration persists even after power off

### Phase 4: Run Ambilight!

1. **Adjust Settings:**
   - Set brightness (100% for maximum effect)
   - Choose FPS (30 FPS = smooth, 60 FPS = ultra-smooth)

2. **Start:**
   - Click **"▶ Start Ambilight"**
   - LEDs should immediately mirror screen colors!

3. **Fine-tune:**
   - Adjust brightness for comfort
   - Lower FPS if you experience lag

---

## 🎨 Usage Tips

### Best Practices

**LED Count:**
- **30-40 LEDs:** Good for 24" monitors
- **50-60 LEDs:** Perfect for 27" monitors  
- **80-100 LEDs:** Great for 32"+ monitors or TVs
- More LEDs = smoother color transitions

**Brightness:**
- Start at 50-70% for comfortable viewing
- 100% can be intense in dark rooms
- Lower brightness reduces power consumption

**Frame Rate:**
- **30 FPS:** Perfect balance (recommended)
- **60 FPS:** Ultra-smooth for fast action
- **20 FPS:** Low-end PCs or many LEDs

**Room Setup:**
- Best in dim/dark rooms
- Position monitor 10-20cm from wall
- White/light walls work best for reflection

### Performance Optimization

**If experiencing lag:**
1. Lower FPS (30 → 20)
2. Reduce LED count in firmware
3. Close background applications
4. Use wired connection instead of WiFi (if ESP32 supports Ethernet)

**If colors are wrong:**
1. Re-run calibration (be more precise)
2. Check `COLOR_ORDER` in firmware (try GRB, RGB, or BGR)
3. Verify LED type matches (`WS2812B`)

---

## 🔧 Troubleshooting

### Connection Issues

**Can't connect to ESP32:**
- Verify ESP32 is powered and WiFi LED is on
- Check IP address in Serial Monitor
- Ping ESP32: `ping 192.168.1.100`
- Ensure PC and ESP32 on same WiFi network
- Try opening browser: `http://ESP32_IP` (should show status page)

**Connection drops:**
- ESP32 too far from router (move closer)
- WiFi interference (change router channel)
- Weak power supply (use quality adapter)

### LED Issues

**LEDs don't light up:**
- Check power supply voltage (must be 5V)
- Verify data pin connection (GPIO 5)
- Test with Serial Monitor (should see "FastLED initialized")
- Check LED strip direction (data flows one way)

**Wrong colors:**
- Change `COLOR_ORDER` in firmware:
  - If RED shows as GREEN → use `GRB`
  - If RED shows as BLUE → use `BRG`
  - If GREEN shows as RED → use `RGB`

**Flickering/glitches:**
- Add 1000µF capacitor across power supply
- Use shorter/thicker wire for data line
- Add 330Ω resistor between GPIO and LED data pin

### Calibration Issues

**LEDs in wrong positions:**
- Carefully re-run calibration
- Ensure you're clicking the correct screen edge
- Verify LED strip starts where you think (LED #0)

**Can't see blinking LED:**
- Increase brightness on LED strip
- Check room lighting (easier to see in dark)
- LED might be defective (skip and note position)

---

## 📱 Mobile Control (Future)

Want to control from your phone? You can:
1. Build simple Android/iOS app using WebSocket
2. Use Home Assistant with ESPHome integration
3. Create web interface (already has built-in web server!)

The ESP32 web server at `http://ESP32_IP` shows current status. You can expand this to full remote control!

---

## 🔒 Security Note

ESP32 WebSocket has NO authentication by default. If security is important:
- Use strong WiFi password
- Don't expose ESP32 to internet
- Add authentication to WebSocket (advanced)

---

## ⚡ Power Calculations

**Example for 60 LEDs:**
- Maximum power: 60 × 60mA = 3.6A @ 5V = 18W
- Typical usage: ~40% = 7.2W
- Recommended PSU: 5V 5A (25W) with headroom

**Always use properly rated power supplies!**

---

## 🎉 Enjoy Your Ambilight!

You now have a professional-grade ambient lighting system that rivals commercial solutions like:
- Philips Hue Sync
- Govee Envisual
- Nanoleaf 4D

For a fraction of the cost! 🌈

**Need help?** Check Serial Monitor for debug info or re-run calibration if colors don't match perfectly.