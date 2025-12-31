import tkinter as tk
from tkinter import ttk, messagebox
import websocket
import numpy as np
from PIL import ImageGrab
import threading
import time
import json


class AmbilightApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 Ambilight Controller (WiFi)")
        self.root.geometry("900x850")  # Increased height for new controls

        self.ws = None
        self.num_leds = 60
        self.led_positions = []
        self.is_running = False
        self.calibration_mode = False
        self.current_led_index = 0
        self.connected = False

        # Capture Settings
        self.capture_mode = tk.StringVar(value="Screen Map")
        self.use_custom_region = tk.BooleanVar(value=False)
        self.region_x = tk.StringVar(value="25")
        self.region_y = tk.StringVar(value="25")
        self.region_w = tk.StringVar(value="50")
        self.region_h = tk.StringVar(value="50")

        self.create_ui()

    def create_ui(self):
        # Connection Frame
        conn_frame = ttk.LabelFrame(self.root, text="WiFi Connection", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="ESP32 IP Address:").grid(row=0, column=0, padx=5)
        self.ip_entry = ttk.Entry(conn_frame, width=20)
        self.ip_entry.insert(0, "127.0.0.1")  # Default IP
        self.ip_entry.grid(row=0, column=1, padx=5)

        ttk.Button(conn_frame, text="Connect", command=self.connect_device).grid(
            row=0, column=2, padx=5
        )
        ttk.Button(conn_frame, text="Disconnect", command=self.disconnect_device).grid(
            row=0, column=3, padx=5
        )

        self.status_label = ttk.Label(
            conn_frame, text="Not Connected", foreground="red"
        )
        self.status_label.grid(row=0, column=4, padx=10)

        self.ip_display = ttk.Label(conn_frame, text="", foreground="blue")
        self.ip_display.grid(row=1, column=0, columnspan=5, pady=5)

        # Calibration Frame
        cal_frame = ttk.LabelFrame(self.root, text="LED Calibration", padding=10)
        cal_frame.pack(fill="both", expand=True, padx=10, pady=5)

        btn_frame = ttk.Frame(cal_frame)
        btn_frame.pack(pady=5)

        ttk.Button(
            btn_frame, text="Start Calibration", command=self.start_calibration
        ).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Test LED Pattern", command=self.test_pattern).pack(
            side="left", padx=5
        )
        ttk.Button(btn_frame, text="Load Config", command=self.load_config).pack(
            side="left", padx=5
        )
        ttk.Button(btn_frame, text="Save Config", command=self.save_config).pack(
            side="left", padx=5
        )

        # Canvas for visual LED mapping
        self.canvas = tk.Canvas(cal_frame, bg="black", height=350)
        self.canvas.pack(fill="both", expand=True, pady=10)
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<Configure>", lambda e: self.draw_led_map())

        self.info_label = ttk.Label(
            cal_frame,
            text="Connect to ESP32 to begin",
            wraplength=800,
            font=("Arial", 10),
        )
        self.info_label.pack(pady=5)

        # Control Frame
        ctrl_frame = ttk.LabelFrame(self.root, text="Ambilight Controls", padding=10)
        ctrl_frame.pack(fill="x", padx=10, pady=5)

        self.start_btn = ttk.Button(
            ctrl_frame, text="▶ Start Ambilight", command=self.start_ambilight
        )
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = ttk.Button(
            ctrl_frame, text="⏹ Stop", command=self.stop_ambilight, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=5)

        ttk.Label(ctrl_frame, text="Brightness:").pack(side="left", padx=10)
        self.brightness_scale = ttk.Scale(
            ctrl_frame,
            from_=10,
            to=255,
            orient="horizontal",
            length=200,
            command=self.update_brightness,
        )
        self.brightness_scale.set(255)
        self.brightness_scale.pack(side="left", padx=5)

        self.brightness_label = ttk.Label(ctrl_frame, text="100%")
        self.brightness_label.pack(side="left", padx=5)

        ttk.Label(ctrl_frame, text="FPS:").pack(side="left", padx=10)
        self.fps_var = tk.StringVar(value="30")
        fps_combo = ttk.Combobox(
            ctrl_frame,
            textvariable=self.fps_var,
            values=["15", "20", "30", "45", "60"],
            width=5,
        )
        fps_combo.pack(side="left", padx=5)

        # Capture Settings Frame
        cap_frame = ttk.LabelFrame(self.root, text="Capture Settings", padding=10)
        cap_frame.pack(fill="x", padx=10, pady=5)

        # Mode Selection
        ttk.Label(cap_frame, text="Mode:").grid(row=0, column=0, padx=5)
        mode_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.capture_mode,
            values=["Screen Map", "Average Color"],
            state="readonly",
            width=15,
        )
        mode_combo.grid(row=0, column=1, padx=5)

        # Custom Region
        tk.Checkbutton(
            cap_frame,
            text="Use Custom Region",
            variable=self.use_custom_region,
            command=self.toggle_region_inputs,
        ).grid(row=0, column=2, padx=15)

        self.reg_frame = ttk.Frame(cap_frame)
        self.reg_frame.grid(row=0, column=3, padx=5)

        # Region Inputs
        validate_cmd = (self.root.register(self.validate_percent), "%P")

        ttk.Label(self.reg_frame, text="X%:").pack(side="left")
        self.ent_x = ttk.Entry(
            self.reg_frame,
            width=4,
            textvariable=self.region_x,
            validate="key",
            validatecommand=validate_cmd,
        )
        self.ent_x.pack(side="left", padx=2)

        ttk.Label(self.reg_frame, text="Y%:").pack(side="left")
        self.ent_y = ttk.Entry(
            self.reg_frame,
            width=4,
            textvariable=self.region_y,
            validate="key",
            validatecommand=validate_cmd,
        )
        self.ent_y.pack(side="left", padx=2)

        ttk.Label(self.reg_frame, text="W%:").pack(side="left")
        self.ent_w = ttk.Entry(
            self.reg_frame,
            width=4,
            textvariable=self.region_w,
            validate="key",
            validatecommand=validate_cmd,
        )
        self.ent_w.pack(side="left", padx=2)

        ttk.Label(self.reg_frame, text="H%:").pack(side="left")
        self.ent_h = ttk.Entry(
            self.reg_frame,
            width=4,
            textvariable=self.region_h,
            validate="key",
            validatecommand=validate_cmd,
        )
        self.ent_h.pack(side="left", padx=2)

        self.toggle_region_inputs()  # Set initial state

        # Status bar
        self.status_bar = ttk.Label(
            self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def validate_percent(self, val):
        if val == "":
            return True
        try:
            v = int(val)
            return 0 <= v <= 100
        except ValueError:
            return False

    def toggle_region_inputs(self):
        state = "normal" if self.use_custom_region.get() else "disabled"
        for widget in self.reg_frame.winfo_children():
            if isinstance(widget, ttk.Entry):
                widget.config(state=state)

    def connect_device(self):
        ip = self.ip_entry.get().strip()
        if not ip:
            messagebox.showwarning("Warning", "Please enter IP address")
            return

        try:
            ws_url = f"ws://{ip}:81"
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_message=self.on_ws_message,
                on_error=self.on_ws_error,
                on_close=self.on_ws_close,
                on_open=self.on_ws_open,
            )

            # Start WebSocket in background thread
            self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
            self.ws_thread.start()

            self.status_label.config(text="Connecting...", foreground="orange")

        except Exception as e:
            messagebox.showerror("Error", f"Connection failed: {e}")
            self.status_label.config(text="Connection Failed", foreground="red")

    def disconnect_device(self):
        if self.ws:
            self.ws.close()
        self.connected = False
        self.status_label.config(text="Not Connected", foreground="red")

    def on_ws_open(self, ws):
        self.connected = True
        self.root.after(
            0, lambda: self.status_label.config(text="Connected ✓", foreground="green")
        )
        self.root.after(
            0,
            lambda: self.ip_display.config(
                text=f"WebSocket: ws://{self.ip_entry.get()}:81"
            ),
        )
        print("WebSocket connected!")

    def on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "info":
                self.num_leds = data.get("ledCount", 60)
                self.initialize_led_positions()
                self.root.after(
                    0,
                    lambda: self.info_label.config(
                        text=f"Connected! Found {self.num_leds} LEDs. Ready to calibrate."
                    ),
                )
                print(f"Received LED count: {self.num_leds}")
        except Exception as e:
            print(f"Message parse error: {e}")

    def on_ws_error(self, ws, error):
        print(f"WebSocket error: {error}")
        self.root.after(0, lambda: messagebox.showerror("WebSocket Error", str(error)))

    def on_ws_close(self, ws, close_status_code, close_msg):
        self.connected = False
        self.root.after(
            0, lambda: self.status_label.config(text="Disconnected", foreground="red")
        )
        print("WebSocket closed")

    def send_json(self, data):
        if self.ws and self.connected:
            try:
                self.ws.send(json.dumps(data))
                return True
            except Exception as e:
                print(f"Send error: {e}")
                return False
        return False

    def send_binary(self, data):
        if self.ws and self.connected:
            try:
                self.ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                return True
            except Exception as e:
                print(f"Send error: {e}")
                return False
        return False

    def initialize_led_positions(self):
        """Initialize LED positions with a default grid layout.

        LEDs can be placed anywhere on the screen - this is just a default
        starting point. Users will calibrate the actual positions.
        Works with any LED arrangement: strip, matrix, spiral, freeform, etc.
        """
        self.led_positions = []

        # Create a simple default layout (grid pattern)
        # User will override these positions during calibration
        cols = int(np.ceil(np.sqrt(self.num_leds)))
        rows = int(np.ceil(self.num_leds / cols))

        for i in range(self.num_leds):
            row = i // cols
            col = i % cols

            # Normalize to 0-1 range
            x = col / max(cols - 1, 1) if cols > 1 else 0.5
            y = row / max(rows - 1, 1) if rows > 1 else 0.5

            self.led_positions.append({"x": x, "y": y})

        self.draw_led_map()

    def draw_led_map(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 1 or h <= 1:
            return

        margin = 40

        # Draw screen rectangle
        self.canvas.create_rectangle(
            margin, margin, w - margin, h - margin, outline="gray", width=3, dash=(5, 5)
        )

        # Draw corner labels
        self.canvas.create_text(
            margin - 20, margin - 20, text="TOP-LEFT", fill="gray", font=("Arial", 8)
        )
        self.canvas.create_text(
            w - margin + 20,
            margin - 20,
            text="TOP-RIGHT",
            fill="gray",
            font=("Arial", 8),
        )
        self.canvas.create_text(
            margin - 20,
            h - margin + 20,
            text="BOTTOM-LEFT",
            fill="gray",
            font=("Arial", 8),
        )
        self.canvas.create_text(
            w - margin + 20,
            h - margin + 20,
            text="BOTTOM-RIGHT",
            fill="gray",
            font=("Arial", 8),
        )

        # Draw LEDs
        for i, led in enumerate(self.led_positions):
            x = margin + led["x"] * (w - 2 * margin)
            y = margin + led["y"] * (h - 2 * margin)

            # Color based on state
            if self.calibration_mode and i == self.current_led_index:
                color = "yellow"
                size = 8
            elif i < self.current_led_index and self.calibration_mode:
                color = "green"
                size = 5
            else:
                color = "cyan"
                size = 5

            self.canvas.create_oval(
                x - size,
                y - size,
                x + size,
                y + size,
                fill=color,
                outline="white",
                width=2,
            )

            # Label every 10th LED
            if i % 10 == 0 or (self.calibration_mode and i == self.current_led_index):
                self.canvas.create_text(
                    x, y - 20, text=str(i), fill="white", font=("Arial", 9, "bold")
                )

    def start_calibration(self):
        if not self.connected:
            messagebox.showwarning("Warning", "Please connect to ESP32 first")
            return

        self.calibration_mode = True
        self.current_led_index = 0

        self.send_json({"cmd": "calibrate_start"})
        self.send_json({"cmd": "highlight", "led": 0})

        self.info_label.config(
            text=f"🎯 Calibrating LED 0/{self.num_leds}\n"
            f"The LED should be blinking WHITE on your strip.\n"
            f"Click on the canvas where this LED is physically located.",
            foreground="orange",
        )

        self.draw_led_map()

    def canvas_click(self, event):
        """Handle click on canvas to set LED position.

        Supports any LED layout - click anywhere on the canvas to position
        the current LED. Works for strips, matrices, spirals, or freeform.
        """
        if not self.calibration_mode:
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        margin = 40

        # Convert click to normalized position (0.0 to 1.0)
        x = max(0, min(1, (event.x - margin) / (w - 2 * margin)))
        y = max(0, min(1, (event.y - margin) / (h - 2 * margin)))

        # Store raw x/y coordinates - no edge snapping
        # This allows LEDs to be placed anywhere on the screen
        self.led_positions[self.current_led_index] = {"x": x, "y": y}

        self.current_led_index += 1

        if self.current_led_index < self.num_leds:
            self.send_json({"cmd": "highlight", "led": self.current_led_index})
            self.info_label.config(
                text=f"🎯 Calibrating LED {self.current_led_index}/{self.num_leds}\n"
                f"Click where this LED is located on your screen.",
                foreground="orange",
            )
            self.draw_led_map()
        else:
            self.finish_calibration()

    def finish_calibration(self):
        self.calibration_mode = False

        # Send mapping to ESP32 as x/y coordinates (scaled to 0-255)
        mapping = [
            {"x": int(led["x"] * 255), "y": int(led["y"] * 255)}
            for led in self.led_positions
        ]

        self.send_json({"cmd": "save_map", "mapping": mapping})
        self.send_json({"cmd": "calibrate_end"})

        self.info_label.config(
            text="✅ Calibration complete! Configuration saved to ESP32.\n"
            "You can now start the Ambilight effect.",
            foreground="green",
        )

        self.draw_led_map()
        messagebox.showinfo("Success", "Calibration complete!")

    def test_pattern(self):
        if self.connected:
            self.send_json({"cmd": "test_pattern"})
            self.status_bar.config(text="Running test pattern...")
            self.root.after(2000, lambda: self.status_bar.config(text="Ready"))

    def update_brightness(self, value):
        brightness = int(float(value))
        percent = int((brightness / 255) * 100)

        # Guard: brightness_label may not exist during initial UI setup
        if hasattr(self, "brightness_label"):
            self.brightness_label.config(text=f"{percent}%")

        if self.connected:
            self.send_json({"cmd": "brightness", "value": brightness})

    def save_config(self):
        config = {
            "num_leds": self.num_leds,
            "led_positions": self.led_positions,
            "esp32_ip": self.ip_entry.get(),
        }
        with open("ambilight_config.json", "w") as f:
            json.dump(config, f, indent=2)
        messagebox.showinfo("Success", "Configuration saved to ambilight_config.json")

    def load_config(self):
        try:
            with open("ambilight_config.json", "r") as f:
                config = json.load(f)
            self.num_leds = config["num_leds"]
            self.led_positions = config["led_positions"]
            if "esp32_ip" in config:
                self.ip_entry.delete(0, tk.END)
                self.ip_entry.insert(0, config["esp32_ip"])
            self.draw_led_map()
            messagebox.showinfo("Success", "Configuration loaded")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {e}")

    def start_ambilight(self):
        if not self.connected:
            messagebox.showwarning("Warning", "Please connect to ESP32 first")
            return

        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_bar.config(text="Ambilight running...")

        # Start capture thread
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()

    def stop_ambilight(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_bar.config(text="Ambilight stopped")

        # Clear LEDs
        if self.connected:
            self.send_json({"cmd": "clear"})

    def capture_loop(self):
        fps = int(self.fps_var.get())
        delay = 1.0 / fps
        frame_count = 0

        while self.is_running:
            try:
                # Calculate capture region
                bbox = None
                if self.use_custom_region.get():
                    try:
                        # Get full screen size
                        full_screen = (
                            ImageGrab.grab()
                        )  # Very fast call just for size if needed
                        # Or better, use winfo_screenwidth/height but that's per monitor.
                        # ImageGrab.grab().size is reliable for primary monitor or all monitors depending on OS.
                        # For simplicity, let's grab full and crop, or calc bbox from full size.
                        sw, sh = full_screen.size

                        rx = int(int(self.region_x.get()) / 100 * sw)
                        ry = int(int(self.region_y.get()) / 100 * sh)
                        rw = int(int(self.region_w.get()) / 100 * sw)
                        rh = int(int(self.region_h.get()) / 100 * sh)

                        # Clamp
                        rx = max(0, min(rx, sw - 1))
                        ry = max(0, min(ry, sh - 1))
                        rw = max(1, min(rw, sw - rx))
                        rh = max(1, min(rh, sh - ry))

                        bbox = (rx, ry, rx + rw, ry + rh)
                    except Exception as e:
                        print(f"Region calc error: {e}")
                        bbox = None

                # Capture screen (or region)
                screen = ImageGrab.grab(bbox=bbox)

                # Resize for performance (keep aspect ratio somewhat or just fixed small size)
                # 100x100 is plenty for lighting info
                screen = screen.resize((100, 100))
                pixels = np.array(screen)

                h, w = pixels.shape[:2]
                brightness = int(self.brightness_scale.get())

                led_colors = bytearray()
                log_colors = []

                mode = self.capture_mode.get()

                if mode == "Average Color":
                    # Calculate average color of the captured region
                    avg_color = np.mean(pixels, axis=(0, 1)).astype(int)
                    r_raw, g_raw, b_raw = avg_color[0], avg_color[1], avg_color[2]

                    # Apply brightness
                    r = int(r_raw * brightness / 255)
                    g = int(g_raw * brightness / 255)
                    b = int(b_raw * brightness / 255)

                    # Set same color for all LEDs
                    for i in range(self.num_leds):
                        led_colors.extend([r, g, b])

                    if frame_count % 30 == 0:
                        log_colors.append(f"ALL:({r},{g},{b})")

                else:  # Screen Map
                    for i, led in enumerate(self.led_positions):
                        # Map normalized LED positions (0-1) to the CAPTURED image dimensions
                        x = int(led["x"] * (w - 1))
                        y = int(led["y"] * (h - 1))

                        # Sample color
                        color = pixels[y, x]
                        r_raw, g_raw, b_raw = (
                            int(color[0]),
                            int(color[1]),
                            int(color[2]),
                        )

                        # Apply brightness
                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)

                        led_colors.extend([r, g, b])

                        if i < 5 and frame_count % 30 == 0:
                            log_colors.append(f"LED{i}:({r},{g},{b})")

                # Log every 30 frames
                frame_count += 1
                if frame_count % 30 == 0:
                    print(
                        f"[Frame {frame_count}] Mode: {mode} | Sending: {', '.join(log_colors)}..."
                    )

                # Send binary data
                self.send_binary(bytes(led_colors))

                time.sleep(delay)

            except Exception as e:
                print(f"Capture error: {e}")
                time.sleep(0.1)


if __name__ == "__main__":
    root = tk.Tk()
    app = AmbilightApp(root)
    root.mainloop()
