"""
ESP32 Ambilight Simulator

This simulates the ESP32 WebSocket server and displays LEDs in a visual window.
Use this to test the app.py without actual hardware.

Run this FIRST, then run app.py and connect to: 127.0.0.1
"""

import asyncio
import websockets
import json
import tkinter as tk
from threading import Thread
import struct

# Configuration
LAYOUT_MODE = "U_SHAPE"  # Options: "GRID", "U_SHAPE"

# Grid Config
LED_ROWS = 4
LED_COLS = 4

# U-Shape Config (Room Walls)
LEDS_LEFT = 20
LEDS_BOTTOM = 30
LEDS_RIGHT = 20

if LAYOUT_MODE == "GRID":
    NUM_LEDS = LED_ROWS * LED_COLS
else:
    NUM_LEDS = LEDS_LEFT + LEDS_BOTTOM + LEDS_RIGHT

WS_PORT = 81


class LEDSimulator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"ESP32 LED Simulator ({LAYOUT_MODE})")
        self.root.geometry("800x600")
        self.root.configure(bg="#1a1a1a")

        # Status label
        self.status = tk.Label(
            self.root,
            text="🔴 Waiting for connection on ws://127.0.0.1:81",
            font=("Arial", 12),
            bg="#1a1a1a",
            fg="#ff6666",
        )
        self.status.pack(pady=10)

        # LED canvas
        self.canvas = tk.Canvas(self.root, bg="#2a2a2a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=20, pady=10)

        # LED state
        self.led_colors = [(0, 0, 0)] * NUM_LEDS
        self.led_ovals = []
        self.calibration_mode = False
        self.highlight_led = -1
        self.brightness = 255

        # Draw initial LEDs
        self.root.after(100, self.draw_leds)

        # Info label
        info = tk.Label(
            self.root,
            text=f"Simulating {NUM_LEDS} LEDs ({LAYOUT_MODE}) | Connect app.py to 127.0.0.1",
            font=("Arial", 10),
            bg="#1a1a1a",
            fg="#888888",
        )
        info.pack(pady=5)

    def draw_leds(self):
        self.canvas.delete("all")
        self.led_ovals = []

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w < 10 or h < 10:
            self.root.after(100, self.draw_leds)
            return

        led_size = 15  # Default size

        locations = []  # List of (x, y) tuples

        if LAYOUT_MODE == "GRID":
            cols = LED_COLS
            rows = LED_ROWS
            cell_w = w / (cols + 1)
            cell_h = h / (rows + 1)
            led_size = min(cell_w, cell_h) * 0.35

            for i in range(NUM_LEDS):
                row = i // cols
                col = i % cols
                locations.append((cell_w * (col + 1), cell_h * (row + 1)))

        elif LAYOUT_MODE == "U_SHAPE":
            # Margin from edge
            m = 50

            # Left Wall (Top -> Bottom)
            # x is fixed at m, y varies
            h_draw = h - 2 * m
            w_draw = w - 2 * m

            # 1. Left Wall
            if LEDS_LEFT > 0:
                step = h_draw / (LEDS_LEFT + 1) if LEDS_LEFT > 0 else 0
                for i in range(LEDS_LEFT):
                    x = m
                    y = m + step * (i + 1)  # Start from top
                    locations.append((x, y))

            # 2. Bottom Wall (Left -> Right)
            if LEDS_BOTTOM > 0:
                step = w_draw / (LEDS_BOTTOM + 1) if LEDS_BOTTOM > 0 else 0
                for i in range(LEDS_BOTTOM):
                    x = m + step * (i + 1)
                    y = h - m
                    locations.append((x, y))

            # 3. Right Wall (Bottom -> Top)
            if LEDS_RIGHT > 0:
                step = h_draw / (LEDS_RIGHT + 1) if LEDS_RIGHT > 0 else 0
                for i in range(LEDS_RIGHT):
                    x = w - m
                    y = h - m - step * (i + 1)  # Start from bottom
                    locations.append((x, y))

        # Render Loop
        for i, (x, y) in enumerate(locations):
            # Get color
            if self.calibration_mode and i == self.highlight_led:
                # Blinking white during calibration
                import time

                if int(time.time() * 2) % 2 == 0:
                    color = "#ffffff"
                else:
                    color = "#333333"
            else:
                if i < len(self.led_colors):
                    r, g, b = self.led_colors[i]
                else:
                    r, g, b = 0, 0, 0

                # Apply brightness
                r = int(r * self.brightness / 255)
                g = int(g * self.brightness / 255)
                b = int(b * self.brightness / 255)
                color = f"#{r:02x}{g:02x}{b:02x}"

            # Draw LED with glow effect
            if color != "#000000":
                # Outer glow
                self.canvas.create_oval(
                    x - led_size * 1.5,
                    y - led_size * 1.5,
                    x + led_size * 1.5,
                    y + led_size * 1.5,
                    fill="",
                    outline=color,
                    width=2,
                )

            # Main LED
            oval = self.canvas.create_oval(
                x - led_size,
                y - led_size,
                x + led_size,
                y + led_size,
                fill=color,
                outline="#444444",
                width=1,
            )
            self.led_ovals.append(oval)

            # LED number (only every 5th or corners to avoid clutter)
            if i % 5 == 0 or i == 0 or i == NUM_LEDS - 1:
                self.canvas.create_text(
                    x,
                    y,
                    text=str(i),
                    fill="#888888" if color == "#000000" else "#000000",
                    font=("Arial", 8),
                )

        # Redraw periodically (for calibration blink)
        self.root.after(100, self.draw_leds)

    def set_led_colors(self, colors):
        """Update LED colors from binary data"""
        self.led_colors = colors[:NUM_LEDS]

    def set_calibration(self, mode, led=-1):
        self.calibration_mode = mode
        self.highlight_led = led

    def set_brightness(self, value):
        self.brightness = value

    def set_connected(self, connected):
        if connected:
            self.status.config(
                text="🟢 Connected! Receiving data from app.py", fg="#66ff66"
            )
        else:
            self.status.config(
                text="🔴 Waiting for connection on ws://127.0.0.1:81", fg="#ff6666"
            )

    def test_pattern(self):
        """Run a test pattern"""
        import time

        for i in range(NUM_LEDS):
            self.led_colors = [(0, 0, 0)] * NUM_LEDS
            self.led_colors[i] = (255, 0, 0)
            time.sleep(0.03)
        self.led_colors = [(0, 0, 0)] * NUM_LEDS

    def run(self):
        self.root.mainloop()


# Global simulator reference
simulator = None


async def handle_client(websocket):
    """Handle WebSocket connections"""
    print(f"Client connected: {websocket.remote_address}")
    simulator.set_connected(True)

    # Send initial info (like real ESP32)
    info = {"type": "info", "ledCount": NUM_LEDS}
    await websocket.send(json.dumps(info))

    try:
        async for message in websocket:
            # Handle text (JSON commands)
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    cmd = data.get("cmd", "")

                    if cmd == "calibrate_start":
                        simulator.set_calibration(True)
                        await websocket.send('{"type":"ack","cmd":"calibrate_start"}')
                        print("Calibration started")

                    elif cmd == "calibrate_end":
                        simulator.set_calibration(False)
                        await websocket.send('{"type":"ack","cmd":"calibrate_end"}')
                        print("Calibration ended")

                    elif cmd == "highlight":
                        led = data.get("led", 0)
                        simulator.set_calibration(True, led)
                        print(f"Highlighting LED {led}")

                    elif cmd == "save_map":
                        mapping = data.get("mapping", [])
                        print(f"Received mapping for {len(mapping)} LEDs")
                        for i, m in enumerate(mapping[:5]):
                            print(f"  LED {i}: x={m.get('x', 0)}, y={m.get('y', 0)}")
                        if len(mapping) > 5:
                            print(f"  ... and {len(mapping) - 5} more")
                        await websocket.send('{"type":"ack","cmd":"save_map"}')

                    elif cmd == "test_pattern":
                        print("Running test pattern")
                        await websocket.send('{"type":"ack","cmd":"test_pattern"}')
                        # Run in thread to not block
                        Thread(target=simulator.test_pattern, daemon=True).start()

                    elif cmd == "brightness":
                        value = data.get("value", 255)
                        simulator.set_brightness(value)
                        print(f"Brightness set to {value}")

                    elif cmd == "clear":
                        simulator.led_colors = [(0, 0, 0)] * NUM_LEDS
                        print("LEDs cleared")

                except json.JSONDecodeError:
                    print(f"Invalid JSON: {message}")

            # Handle binary (LED color data)
            else:
                if len(message) >= NUM_LEDS * 3:
                    colors = []
                    for i in range(NUM_LEDS):
                        r = message[i * 3]
                        g = message[i * 3 + 1]
                        b = message[i * 3 + 2]
                        colors.append((r, g, b))
                    simulator.set_led_colors(colors)

                    # Log received colors periodically
                    if not hasattr(handle_client, "frame_count"):
                        handle_client.frame_count = 0
                    handle_client.frame_count += 1

                    if handle_client.frame_count % 30 == 0:
                        sample = [
                            f"LED{i}:({c[0]},{c[1]},{c[2]})"
                            for i, c in enumerate(colors[:5])
                        ]
                        print(
                            f"[Simulator Frame {handle_client.frame_count}] Received: {', '.join(sample)}..."
                        )

    except websockets.ConnectionClosed:
        print("Client disconnected")
    finally:
        simulator.set_connected(False)


async def start_server():
    """Start WebSocket server"""
    print(f"Starting WebSocket server on ws://127.0.0.1:{WS_PORT}")
    print("Run app.py and connect to IP: 127.0.0.1")
    print("-" * 50)

    async with websockets.serve(handle_client, "127.0.0.1", WS_PORT):
        await asyncio.Future()  # Run forever


def run_server():
    """Run server in asyncio event loop"""
    asyncio.run(start_server())


if __name__ == "__main__":
    # Create simulator UI
    simulator = LEDSimulator()

    # Start WebSocket server in background thread
    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()

    # Run Tkinter main loop
    simulator.run()
