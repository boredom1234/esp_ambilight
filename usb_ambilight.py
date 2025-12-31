"""
USB Ambilight Controller - For Arduino Nano
Captures screen colors and sends them to Arduino Nano via USB serial.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import numpy as np
from PIL import ImageGrab
import threading
import time
import json


class USBAmbilightApp:
    def __init__(self, root):
        self.root = root
        self.root.title("USB Ambilight Controller (Arduino Nano)")
        self.root.geometry("900x850")

        self.serial_port = None
        self.num_leds = 16
        self.led_positions = []
        self.is_running = False
        self.calibration_mode = False
        self.current_led_index = 0
        self.connected = False

        # Sync bytes for binary protocol (prevents false frame detection)
        self.MAGIC_BYTE_1 = 0xAD
        self.MAGIC_BYTE_2 = 0xDA

        # Capture Settings
        self.capture_mode = tk.StringVar(value="Screen Map")
        self.use_custom_region = tk.BooleanVar(value=False)
        self.region_x = tk.StringVar(value="25")
        self.region_y = tk.StringVar(value="25")
        self.region_w = tk.StringVar(value="50")
        self.region_h = tk.StringVar(value="50")

        self.create_ui()
        self.refresh_ports()
        
        # Previous LED colors for smoothing
        self.prev_colors = None

    def create_ui(self):
        # Connection Frame
        conn_frame = ttk.LabelFrame(self.root, text="USB Connection", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="COM Port:").grid(row=0, column=0, padx=5)
        self.port_combo = ttk.Combobox(conn_frame, width=15, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=5)

        ttk.Button(conn_frame, text="🔄", width=3, command=self.refresh_ports).grid(
            row=0, column=2, padx=2
        )
        ttk.Button(conn_frame, text="Connect", command=self.connect_device).grid(
            row=0, column=3, padx=5
        )
        ttk.Button(conn_frame, text="Disconnect", command=self.disconnect_device).grid(
            row=0, column=4, padx=5
        )

        self.status_label = ttk.Label(
            conn_frame, text="Not Connected", foreground="red"
        )
        self.status_label.grid(row=0, column=5, padx=10)

        self.port_display = ttk.Label(conn_frame, text="", foreground="blue")
        self.port_display.grid(row=1, column=0, columnspan=6, pady=5)

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
            text="Connect to Arduino to begin",
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

        # Smoothing slider
        ttk.Label(ctrl_frame, text="Smooth:").pack(side="left", padx=10)
        self.smooth_scale = ttk.Scale(
            ctrl_frame,
            from_=0,
            to=95,
            orient="horizontal",
            length=100,
            command=self.update_smooth_label,
        )
        self.smooth_scale.set(50)
        self.smooth_scale.pack(side="left", padx=5)

        self.smooth_label = ttk.Label(ctrl_frame, text="50%")
        self.smooth_label.pack(side="left", padx=5)

        # Capture Settings Frame
        cap_frame = ttk.LabelFrame(self.root, text="Capture Settings", padding=10)
        cap_frame.pack(fill="x", padx=10, pady=5)

        # Mode Selection
        ttk.Label(cap_frame, text="Mode:").grid(row=0, column=0, padx=5)
        mode_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.capture_mode,
            values=["Screen Map", "Average Color", "Dominant Color", 
                    "Edge Sampling", "Quadrant Colors", "Most Vibrant",
                    "Warm Bias", "Cool Bias"],
            state="readonly",
            width=18,
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

        self.toggle_region_inputs()

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

    def refresh_ports(self):
        """Refresh available COM ports."""
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            self.port_combo.set(ports[0])

    def connect_device(self):
        port = self.port_combo.get()
        if not port:
            messagebox.showwarning("Warning", "Please select a COM port")
            return

        try:
            self.serial_port = serial.Serial(port, 115200, timeout=1)
            time.sleep(2)  # Wait for Arduino reset

            # Clear any startup messages
            self.serial_port.reset_input_buffer()

            # Request info
            self.send_json({"cmd": "info"})
            time.sleep(0.5)

            # Read response
            if self.serial_port.in_waiting:
                response = self.serial_port.readline().decode().strip()
                try:
                    data = json.loads(response)
                    if data.get("type") in ["info", "ready"]:
                        self.num_leds = data.get("ledCount", 16)
                except:
                    pass

            self.connected = True
            self.status_label.config(text="Connected ✓", foreground="green")
            self.port_display.config(text=f"Port: {port} @ 115200 baud")
            self.initialize_led_positions()
            self.info_label.config(
                text=f"Connected! Found {self.num_leds} LEDs. Ready to calibrate."
            )
            print(f"Connected to {port}")

        except Exception as e:
            messagebox.showerror("Error", f"Connection failed: {e}")
            self.status_label.config(text="Connection Failed", foreground="red")

    def disconnect_device(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None
        self.connected = False
        self.status_label.config(text="Not Connected", foreground="red")
        self.port_display.config(text="")

    def send_json(self, data):
        if self.serial_port and self.connected:
            try:
                self.serial_port.write((json.dumps(data) + "\n").encode())
                return True
            except Exception as e:
                print(f"Send error: {e}")
                return False
        return False

    def send_binary(self, data):
        """Send binary LED data with sync bytes and checksum."""
        if self.serial_port and self.connected:
            try:
                # Calculate XOR checksum of RGB data
                checksum = 0
                for b in data:
                    checksum ^= b
                
                # Frame format: 0xAD + 0xDA + RGB data + checksum
                frame = bytes([self.MAGIC_BYTE_1, self.MAGIC_BYTE_2]) + data + bytes([checksum])
                self.serial_port.write(frame)
                return True
            except Exception as e:
                print(f"Send error: {e}")
                return False
        return False

    def initialize_led_positions(self):
        """Initialize LED positions with a default grid layout."""
        self.led_positions = []

        cols = int(np.ceil(np.sqrt(self.num_leds)))
        rows = int(np.ceil(self.num_leds / cols))

        for i in range(self.num_leds):
            row = i // cols
            col = i % cols

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
            w - margin + 20, margin - 20, text="TOP-RIGHT", fill="gray", font=("Arial", 8)
        )
        self.canvas.create_text(
            margin - 20, h - margin + 20, text="BOTTOM-LEFT", fill="gray", font=("Arial", 8)
        )
        self.canvas.create_text(
            w - margin + 20, h - margin + 20, text="BOTTOM-RIGHT", fill="gray", font=("Arial", 8)
        )

        # Draw LEDs
        for i, led in enumerate(self.led_positions):
            x = margin + led["x"] * (w - 2 * margin)
            y = margin + led["y"] * (h - 2 * margin)

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
                x - size, y - size, x + size, y + size,
                fill=color, outline="white", width=2,
            )

            # Label every LED for 16-LED strip
            self.canvas.create_text(
                x, y - 15, text=str(i), fill="white", font=("Arial", 9, "bold")
            )

    def start_calibration(self):
        if not self.connected:
            messagebox.showwarning("Warning", "Please connect to Arduino first")
            return

        self.calibration_mode = True
        self.current_led_index = 0

        # Highlight first LED
        self.highlight_led(0)

        self.info_label.config(
            text=f"🎯 Calibrating LED 0/{self.num_leds}\n"
            f"The LED should be blinking WHITE on your strip.\n"
            f"Click on the canvas where this LED is physically located.",
            foreground="orange",
        )

        self.draw_led_map()

    def highlight_led(self, index):
        """Highlight a specific LED for calibration."""
        # Send a frame with only the target LED lit
        led_data = bytearray(self.num_leds * 3)
        if 0 <= index < self.num_leds:
            idx = index * 3
            led_data[idx] = 255      # R
            led_data[idx + 1] = 255  # G
            led_data[idx + 2] = 255  # B
        self.send_binary(bytes(led_data))

    def canvas_click(self, event):
        if not self.calibration_mode:
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        margin = 40

        x = max(0, min(1, (event.x - margin) / (w - 2 * margin)))
        y = max(0, min(1, (event.y - margin) / (h - 2 * margin)))

        self.led_positions[self.current_led_index] = {"x": x, "y": y}
        self.current_led_index += 1

        if self.current_led_index < self.num_leds:
            self.highlight_led(self.current_led_index)
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

        # Clear LEDs
        self.send_json({"cmd": "clear"})

        self.info_label.config(
            text="✅ Calibration complete! Configuration saved.\n"
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

        if hasattr(self, "brightness_label"):
            self.brightness_label.config(text=f"{percent}%")

        if self.connected:
            self.send_json({"cmd": "brightness", "value": brightness})

    def update_smooth_label(self, value):
        smooth = int(float(value))
        if hasattr(self, "smooth_label"):
            self.smooth_label.config(text=f"{smooth}%")

    def save_config(self):
        config = {
            "num_leds": self.num_leds,
            "led_positions": self.led_positions,
            "com_port": self.port_combo.get(),
        }
        with open("usb_ambilight_config.json", "w") as f:
            json.dump(config, f, indent=2)
        messagebox.showinfo("Success", "Configuration saved to usb_ambilight_config.json")

    def load_config(self):
        try:
            with open("usb_ambilight_config.json", "r") as f:
                config = json.load(f)
            self.num_leds = config["num_leds"]
            self.led_positions = config["led_positions"]
            if "com_port" in config:
                ports = list(self.port_combo["values"])
                if config["com_port"] in ports:
                    self.port_combo.set(config["com_port"])
            self.draw_led_map()
            messagebox.showinfo("Success", "Configuration loaded")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {e}")

    def start_ambilight(self):
        if not self.connected:
            messagebox.showwarning("Warning", "Please connect to Arduino first")
            return

        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_bar.config(text="Ambilight running...")

        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()

    def stop_ambilight(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_bar.config(text="Ambilight stopped")
        
        # Reset smoothing state
        self.prev_colors = None

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
                        full_screen = ImageGrab.grab()
                        sw, sh = full_screen.size

                        rx = int(int(self.region_x.get()) / 100 * sw)
                        ry = int(int(self.region_y.get()) / 100 * sh)
                        rw = int(int(self.region_w.get()) / 100 * sw)
                        rh = int(int(self.region_h.get()) / 100 * sh)

                        rx = max(0, min(rx, sw - 1))
                        ry = max(0, min(ry, sh - 1))
                        rw = max(1, min(rw, sw - rx))
                        rh = max(1, min(rh, sh - ry))

                        bbox = (rx, ry, rx + rw, ry + rh)
                    except Exception as e:
                        print(f"Region calc error: {e}")
                        bbox = None

                screen = ImageGrab.grab(bbox=bbox)
                screen = screen.resize((100, 100))
                pixels = np.array(screen)

                h, w = pixels.shape[:2]
                brightness = int(self.brightness_scale.get())

                led_colors = bytearray()
                log_colors = []

                mode = self.capture_mode.get()

                if mode == "Average Color":
                    avg_color = np.mean(pixels, axis=(0, 1)).astype(int)
                    r_raw, g_raw, b_raw = avg_color[0], avg_color[1], avg_color[2]

                    # Black threshold - if screen is very dark, turn off LEDs
                    if r_raw + g_raw + b_raw < 15:
                        r, g, b = 0, 0, 0
                    else:
                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)

                    for i in range(self.num_leds):
                        led_colors.extend([r, g, b])

                    if frame_count % 30 == 0:
                        log_colors.append(f"ALL:({r},{g},{b})")

                elif mode == "Dominant Color":
                    flat_pixels = pixels.reshape(-1, 3)

                    max_vals = np.max(flat_pixels, axis=1)
                    min_vals = np.min(flat_pixels, axis=1)

                    with np.errstate(divide='ignore', invalid='ignore'):
                        saturation = np.where(
                            max_vals > 0,
                            ((max_vals - min_vals) * 255) / max_vals,
                            0
                        ).astype(np.uint8)

                    brightness_vals = max_vals
                    colorful_mask = (saturation > 50) & (brightness_vals > 30) & (brightness_vals < 240)

                    if np.any(colorful_mask):
                        colorful_pixels = flat_pixels[colorful_mask]
                        colorful_saturations = saturation[colorful_mask]

                        weights = colorful_saturations.astype(float) / 255.0
                        weighted_sum = np.sum(colorful_pixels * weights[:, np.newaxis], axis=0)
                        total_weight = np.sum(weights)

                        if total_weight > 0:
                            dominant = (weighted_sum / total_weight).astype(int)
                        else:
                            dominant = np.mean(colorful_pixels, axis=0).astype(int)

                        r_raw, g_raw, b_raw = dominant[0], dominant[1], dominant[2]
                    else:
                        # No colorful pixels - use average (likely dark/black screen)
                        avg_color = np.mean(flat_pixels, axis=0).astype(int)
                        r_raw, g_raw, b_raw = avg_color[0], avg_color[1], avg_color[2]

                    # Black threshold - if color is very dark, turn off LEDs
                    if r_raw + g_raw + b_raw < 15:
                        r, g, b = 0, 0, 0
                    else:
                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)

                    for i in range(self.num_leds):
                        led_colors.extend([r, g, b])

                    if frame_count % 30 == 0:
                        log_colors.append(f"DOMINANT:({r},{g},{b})")

                elif mode == "Edge Sampling":
                    # Sample from screen edges - 4 LEDs per side
                    # LED layout: 0-3=top, 4-7=right, 8-11=bottom, 12-15=left
                    edge_width = 10  # Pixels from edge to sample
                    
                    for i in range(self.num_leds):
                        if i < 4:  # Top edge (left to right)
                            x_start = int((i / 4) * w)
                            x_end = int(((i + 1) / 4) * w)
                            region = pixels[0:edge_width, x_start:x_end]
                        elif i < 8:  # Right edge (top to bottom)
                            y_start = int(((i - 4) / 4) * h)
                            y_end = int(((i - 3) / 4) * h)
                            region = pixels[y_start:y_end, w-edge_width:w]
                        elif i < 12:  # Bottom edge (right to left)
                            x_start = int(((11 - i) / 4) * w)
                            x_end = int(((12 - i) / 4) * w)
                            region = pixels[h-edge_width:h, x_start:x_end]
                        else:  # Left edge (bottom to top)
                            y_start = int(((15 - i) / 4) * h)
                            y_end = int(((16 - i) / 4) * h)
                            region = pixels[y_start:y_end, 0:edge_width]
                        
                        avg = np.mean(region, axis=(0, 1)).astype(int)
                        r_raw, g_raw, b_raw = avg[0], avg[1], avg[2]
                        
                        if r_raw + g_raw + b_raw < 15:
                            r, g, b = 0, 0, 0
                        else:
                            r = int(r_raw * brightness / 255)
                            g = int(g_raw * brightness / 255)
                            b = int(b_raw * brightness / 255)
                        
                        led_colors.extend([r, g, b])
                    
                    if frame_count % 30 == 0:
                        log_colors.append(f"EDGE:sampling")

                elif mode == "Quadrant Colors":
                    # 4 quadrants, 4 LEDs each
                    # LEDs 0-3=top-left, 4-7=top-right, 8-11=bottom-left, 12-15=bottom-right
                    quadrants = [
                        pixels[0:h//2, 0:w//2],      # Top-left
                        pixels[0:h//2, w//2:w],      # Top-right
                        pixels[h//2:h, 0:w//2],      # Bottom-left
                        pixels[h//2:h, w//2:w],      # Bottom-right
                    ]
                    
                    for q_idx, quad in enumerate(quadrants):
                        avg = np.mean(quad, axis=(0, 1)).astype(int)
                        r_raw, g_raw, b_raw = avg[0], avg[1], avg[2]
                        
                        if r_raw + g_raw + b_raw < 15:
                            r, g, b = 0, 0, 0
                        else:
                            r = int(r_raw * brightness / 255)
                            g = int(g_raw * brightness / 255)
                            b = int(b_raw * brightness / 255)
                        
                        # 4 LEDs per quadrant
                        for _ in range(4):
                            led_colors.extend([r, g, b])
                    
                    if frame_count % 30 == 0:
                        log_colors.append(f"QUAD:4zones")

                elif mode == "Most Vibrant":
                    # Find the most saturated pixel
                    flat_pixels = pixels.reshape(-1, 3)
                    max_vals = np.max(flat_pixels, axis=1)
                    min_vals = np.min(flat_pixels, axis=1)
                    
                    with np.errstate(divide='ignore', invalid='ignore'):
                        saturation = np.where(
                            max_vals > 0,
                            (max_vals - min_vals) / max_vals,
                            0
                        )
                    
                    # Find index of most saturated pixel
                    max_sat_idx = np.argmax(saturation)
                    most_vibrant = flat_pixels[max_sat_idx]
                    r_raw, g_raw, b_raw = int(most_vibrant[0]), int(most_vibrant[1]), int(most_vibrant[2])
                    
                    if r_raw + g_raw + b_raw < 15:
                        r, g, b = 0, 0, 0
                    else:
                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)
                    
                    for i in range(self.num_leds):
                        led_colors.extend([r, g, b])
                    
                    if frame_count % 30 == 0:
                        log_colors.append(f"VIBRANT:({r},{g},{b})")

                elif mode == "Warm Bias":
                    # Average color shifted warmer (more red, less blue)
                    avg_color = np.mean(pixels, axis=(0, 1)).astype(int)
                    r_raw = min(255, int(avg_color[0] * 1.3))  # +30% red
                    g_raw = avg_color[1]
                    b_raw = max(0, int(avg_color[2] * 0.7))    # -30% blue
                    
                    if r_raw + g_raw + b_raw < 15:
                        r, g, b = 0, 0, 0
                    else:
                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)
                    
                    for i in range(self.num_leds):
                        led_colors.extend([r, g, b])
                    
                    if frame_count % 30 == 0:
                        log_colors.append(f"WARM:({r},{g},{b})")

                elif mode == "Cool Bias":
                    # Average color shifted cooler (more blue, less red)
                    avg_color = np.mean(pixels, axis=(0, 1)).astype(int)
                    r_raw = max(0, int(avg_color[0] * 0.7))    # -30% red
                    g_raw = avg_color[1]
                    b_raw = min(255, int(avg_color[2] * 1.3))  # +30% blue
                    
                    if r_raw + g_raw + b_raw < 15:
                        r, g, b = 0, 0, 0
                    else:
                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)
                    
                    for i in range(self.num_leds):
                        led_colors.extend([r, g, b])
                    
                    if frame_count % 30 == 0:
                        log_colors.append(f"COOL:({r},{g},{b})")

                else:  # Screen Map
                    for i, led in enumerate(self.led_positions):
                        x = int(led["x"] * (w - 1))
                        y = int(led["y"] * (h - 1))

                        color = pixels[y, x]
                        r_raw, g_raw, b_raw = int(color[0]), int(color[1]), int(color[2])

                        r = int(r_raw * brightness / 255)
                        g = int(g_raw * brightness / 255)
                        b = int(b_raw * brightness / 255)

                        led_colors.extend([r, g, b])

                        if i < 5 and frame_count % 30 == 0:
                            log_colors.append(f"LED{i}:({r},{g},{b})")

                frame_count += 1
                if frame_count % 30 == 0:
                    print(f"[Frame {frame_count}] Mode: {mode} | Sending: {', '.join(log_colors)}...")

                # Apply smoothing - blend with previous colors
                smooth_factor = self.smooth_scale.get() / 100.0  # 0.0 to 0.95
                
                if self.prev_colors is not None and len(self.prev_colors) == len(led_colors):
                    # Blend: new_color = prev * smooth + current * (1 - smooth)
                    smoothed = bytearray(len(led_colors))
                    for i in range(len(led_colors)):
                        smoothed[i] = int(
                            self.prev_colors[i] * smooth_factor + 
                            led_colors[i] * (1 - smooth_factor)
                        )
                    led_colors = smoothed
                
                # Store for next frame
                self.prev_colors = bytearray(led_colors)

                # Send binary data
                self.send_binary(bytes(led_colors))

                time.sleep(delay)

            except Exception as e:
                print(f"Capture error: {e}")
                time.sleep(0.1)


if __name__ == "__main__":
    root = tk.Tk()
    app = USBAmbilightApp(root)
    root.mainloop()
