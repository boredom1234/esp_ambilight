"""
ESP32 Multi-Mode Ambilight Controller (Lite Version)

Unified Python application that captures screen colors and sends them to
ESP32 via USB Serial or WebSocket.

Features:
- 8 capture modes (Screen Map, Average, Dominant, Edge, Quadrant, Vibrant, Warm/Cool Bias)
- Color smoothing
- LED calibration
- Configuration persistence
- Multiple connection modes

Requirements:
- pip install websocket-client pyserial numpy Pillow
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import json
import struct
import numpy as np
from PIL import ImageGrab

# Optional imports with graceful fallback
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("Warning: pyserial not installed. USB mode disabled.")

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("Warning: websocket-client not installed. WebSocket mode disabled.")


# ============================================================================
# CONNECTION MANAGER - Abstract layer for all connection types
# ============================================================================

class ConnectionManager:
    """Manages connections to ESP32 via USB or WebSocket."""
    
    MAGIC_BYTE_1 = 0xAD
    MAGIC_BYTE_2 = 0xDA
    
    def __init__(self):
        self.mode = None  # 'usb', 'websocket'
        self.connected = False
        
        # Connection objects
        self.serial_port = None
        self.ws = None
        self.ws_thread = None
        
        # Callbacks
        self.on_connected = None
        self.on_disconnected = None
        self.on_message = None
        self.on_error = None
        
        # Device info received from ESP32
        self.led_count = 60
        
    def connect_usb(self, port: str, baud: int = 115200) -> bool:
        """Connect via USB Serial."""
        if not SERIAL_AVAILABLE:
            self._error("pyserial not installed")
            return False
            
        try:
            self.serial_port = serial.Serial(port, baud, timeout=1)
            time.sleep(2)  # Wait for Arduino reset
            self.serial_port.reset_input_buffer()
            
            # Mark as connected first so send_command works
            self.mode = 'usb'
            self.connected = True
            
            # Request device info with retry
            for attempt in range(3):
                print(f"[USB] Requesting device info (attempt {attempt + 1}/3)...")
                self.serial_port.write((json.dumps({"cmd": "info"}) + "\n").encode())
                time.sleep(0.5)
                
                # Try to read response
                if self.serial_port.in_waiting:
                    response = self.serial_port.readline().decode().strip()
                    print(f"[USB] Response: {response}")
                    if response.startswith('{'):
                        self._handle_message(response)
                        break
            
            if self.on_connected:
                self.on_connected('usb', port)
            
            print(f"[USB] Connected! LED count: {self.led_count}")
            return True
            
        except Exception as e:
            self._error(f"USB connection failed: {e}")
            self.connected = False
            self.mode = None
            return False
    
    def connect_websocket(self, ip: str, port: int = 81) -> bool:
        """Connect via WebSocket."""
        if not WEBSOCKET_AVAILABLE:
            self._error("websocket-client not installed")
            return False
            
        try:
            ws_url = f"ws://{ip}:{port}"
            
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_message=self._ws_on_message,
                on_error=self._ws_on_error,
                on_close=self._ws_on_close,
                on_open=self._ws_on_open,
            )
            
            # Run WebSocket in background thread with keep-alive pings
            self.ws_thread = threading.Thread(
                target=lambda: self.ws.run_forever(ping_interval=5, ping_timeout=3),
                daemon=True
            )
            self.ws_thread.start()
            
            # Wait for connection with proper timeout
            timeout = 5.0
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            if not self.connected:
                self._error("WebSocket connection timeout")
                return False
            return True
            
        except Exception as e:
            self._error(f"WebSocket connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from current connection."""
        self.connected = False
        
        if self.mode == 'usb' and self.serial_port:
            try:
                self.serial_port.close()
            except:
                pass
            self.serial_port = None
            
        elif self.mode == 'websocket' and self.ws:
            try:
                self.ws.close()
            except:
                pass
            self.ws = None
        
        self.mode = None
        
        if self.on_disconnected:
            self.on_disconnected()
    
    def send_command(self, cmd: dict) -> bool:
        """Send JSON command to device."""
        if not self.connected:
            return False
            
        try:
            data = json.dumps(cmd)
            
            if self.mode == 'usb':
                self.serial_port.write((data + "\n").encode())
                
            elif self.mode == 'websocket':
                self.ws.send(data)
                
            return True
            
        except Exception as e:
            print(f"Send command error: {e}")
            return False
    
    def send_colors(self, rgb_data: bytes) -> bool:
        """Send LED color data to device."""
        if not self.connected:
            return False
            
        try:
            if self.mode == 'websocket':
                # WebSocket uses raw binary (has its own integrity check)
                self.ws.send(rgb_data, opcode=websocket.ABNF.OPCODE_BINARY)
                
            else:
                # USB uses framed protocol with checksum
                checksum = 0
                for b in rgb_data:
                    checksum ^= b
                
                frame = bytes([self.MAGIC_BYTE_1, self.MAGIC_BYTE_2]) + rgb_data + bytes([checksum])
                
                if self.mode == 'usb':
                    self.serial_port.write(frame)
                    
            return True
            
        except Exception as e:
            print(f"Send colors error: {e}")
            return False
    
    # WebSocket callbacks
    def _ws_on_open(self, ws):
        self.mode = 'websocket'
        self.connected = True
        print(f"[WS] Connection opened, waiting for device info...")
        if self.on_connected:
            self.on_connected('websocket', '')
    
    def _ws_on_message(self, ws, message):
        self._handle_message(message)
    
    def _ws_on_error(self, ws, error):
        self._error(str(error))
    
    def _ws_on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        if self.on_disconnected:
            self.on_disconnected()
    
    def _handle_message(self, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")
            
            if msg_type in ["info", "ready"]:
                self.led_count = data.get("ledCount", 60)
                print(f"[WS] Device info received: {self.led_count} LEDs")
                
            if self.on_message:
                self.on_message(data)
                
        except json.JSONDecodeError:
            pass
    
    def _error(self, msg):
        print(f"Connection error: {msg}")
        if self.on_error:
            self.on_error(msg)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

class AmbilightController:
    """Main application window."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 Ambilight Controller")
        self.root.geometry("950x850")
        
        # Connection manager
        self.conn = ConnectionManager()
        self.conn.on_connected = self._on_connected
        self.conn.on_disconnected = self._on_disconnected
        self.conn.on_message = self._on_message
        self.conn.on_error = self._on_error
        
        # State
        self.num_leds = 60
        self.led_positions = []
        self.is_running = False
        self.calibration_mode = False
        self.current_led_index = 0
        self.prev_colors = None
        
        # Thread safety lock
        self._lock = threading.Lock()
        
        # Screen size cache for custom region
        self._screen_size = None
        
        # Capture settings
        self.capture_mode = tk.StringVar(value="Screen Map")
        self.use_custom_region = tk.BooleanVar(value=False)
        self.region_x = tk.StringVar(value="25")
        self.region_y = tk.StringVar(value="25")
        self.region_w = tk.StringVar(value="50")
        self.region_h = tk.StringVar(value="50")
        
        # Connection mode
        self.connection_mode = tk.StringVar(value="USB")
        
        self.create_ui()
        self.refresh_ports()
    
    def create_ui(self):
        """Build the user interface."""
        
        # ===== Connection Frame =====
        conn_frame = ttk.LabelFrame(self.root, text="Connection", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)
        
        # Mode selection
        mode_frame = ttk.Frame(conn_frame)
        mode_frame.pack(fill="x", pady=5)
        
        ttk.Label(mode_frame, text="Mode:").pack(side="left", padx=5)
        
        modes = []
        if SERIAL_AVAILABLE:
            modes.append("USB")
        if WEBSOCKET_AVAILABLE:
            modes.append("WebSocket")
        
        if not modes:
            modes = ["USB"]  # Fallback
            
        self.mode_combo = ttk.Combobox(
            mode_frame, textvariable=self.connection_mode,
            values=modes, state="readonly", width=12
        )
        self.mode_combo.pack(side="left", padx=5)
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)
        
        # USB settings
        self.usb_frame = ttk.Frame(conn_frame)
        ttk.Label(self.usb_frame, text="COM Port:").pack(side="left", padx=5)
        self.port_combo = ttk.Combobox(self.usb_frame, width=12, state="readonly")
        self.port_combo.pack(side="left", padx=5)
        ttk.Button(self.usb_frame, text="🔄", width=3, command=self.refresh_ports).pack(side="left", padx=2)
        
        # WebSocket settings
        self.ws_frame = ttk.Frame(conn_frame)
        ttk.Label(self.ws_frame, text="IP Address:").pack(side="left", padx=5)
        self.ip_entry = ttk.Entry(self.ws_frame, width=15)
        self.ip_entry.insert(0, "192.168.4.1")
        self.ip_entry.pack(side="left", padx=5)
        
        # Connect/Disconnect buttons
        btn_frame = ttk.Frame(conn_frame)
        btn_frame.pack(fill="x", pady=5)
        
        ttk.Button(btn_frame, text="Connect", command=self.connect_device).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Disconnect", command=self.disconnect_device).pack(side="left", padx=5)
        
        self.status_label = ttk.Label(btn_frame, text="Not Connected", foreground="red")
        self.status_label.pack(side="left", padx=20)
        
        # Manual LED count override
        led_frame = ttk.Frame(conn_frame)
        led_frame.pack(fill="x", pady=5)
        ttk.Label(led_frame, text="LED Count:").pack(side="left", padx=5)
        self.led_count_var = tk.StringVar(value="60")
        self.led_count_entry = ttk.Entry(led_frame, width=6, textvariable=self.led_count_var)
        self.led_count_entry.pack(side="left", padx=5)
        ttk.Button(led_frame, text="Apply", command=self.apply_led_count).pack(side="left", padx=5)
        self.led_count_label = ttk.Label(led_frame, text="(synced from device)", foreground="gray")
        self.led_count_label.pack(side="left", padx=5)
        
        # Show USB frame by default
        self._on_mode_change(None)
        
        # ===== Calibration Frame =====
        cal_frame = ttk.LabelFrame(self.root, text="LED Calibration", padding=10)
        cal_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        cal_btn_frame = ttk.Frame(cal_frame)
        cal_btn_frame.pack(pady=5)
        
        ttk.Button(cal_btn_frame, text="Start Calibration", command=self.start_calibration).pack(side="left", padx=5)
        ttk.Button(cal_btn_frame, text="Test LED Pattern", command=self.test_pattern).pack(side="left", padx=5)
        ttk.Button(cal_btn_frame, text="Load Config", command=self.load_config).pack(side="left", padx=5)
        ttk.Button(cal_btn_frame, text="Save Config", command=self.save_config).pack(side="left", padx=5)
        
        # Canvas for LED mapping visualization
        self.canvas = tk.Canvas(cal_frame, bg="black", height=300)
        self.canvas.pack(fill="both", expand=True, pady=10)
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<Configure>", lambda e: self.draw_led_map())
        
        self.info_label = ttk.Label(
            cal_frame, text="Connect to device to begin",
            wraplength=800, font=("Arial", 10)
        )
        self.info_label.pack(pady=5)
        
        # ===== Controls Frame =====
        ctrl_frame = ttk.LabelFrame(self.root, text="Ambilight Controls", padding=10)
        ctrl_frame.pack(fill="x", padx=10, pady=5)
        
        # Start/Stop
        self.start_btn = ttk.Button(ctrl_frame, text="▶ Start Ambilight", command=self.start_ambilight)
        self.start_btn.pack(side="left", padx=5)
        
        self.stop_btn = ttk.Button(ctrl_frame, text="⏹ Stop", command=self.stop_ambilight, state="disabled")
        self.stop_btn.pack(side="left", padx=5)
        
        # Brightness
        ttk.Label(ctrl_frame, text="Brightness:").pack(side="left", padx=10)
        self.brightness_scale = ttk.Scale(
            ctrl_frame, from_=10, to=255, orient="horizontal",
            length=150, command=self.update_brightness
        )
        self.brightness_scale.set(255)
        self.brightness_scale.pack(side="left", padx=5)
        
        self.brightness_label = ttk.Label(ctrl_frame, text="100%")
        self.brightness_label.pack(side="left", padx=5)
        
        # FPS
        ttk.Label(ctrl_frame, text="FPS:").pack(side="left", padx=10)
        self.fps_var = tk.StringVar(value="30")
        fps_combo = ttk.Combobox(
            ctrl_frame, textvariable=self.fps_var,
            values=["15", "20", "30", "45", "60"], width=5
        )
        fps_combo.pack(side="left", padx=5)
        
        # Smoothing
        ttk.Label(ctrl_frame, text="Smooth:").pack(side="left", padx=10)
        self.smooth_scale = ttk.Scale(
            ctrl_frame, from_=0, to=95, orient="horizontal",
            length=80, command=self.update_smooth_label
        )
        self.smooth_scale.set(50)
        self.smooth_scale.pack(side="left", padx=5)
        
        self.smooth_label = ttk.Label(ctrl_frame, text="50%")
        self.smooth_label.pack(side="left", padx=5)
        
        # ===== Capture Settings Frame =====
        cap_frame = ttk.LabelFrame(self.root, text="Capture Settings", padding=10)
        cap_frame.pack(fill="x", padx=10, pady=5)
        
        # Mode selection
        ttk.Label(cap_frame, text="Mode:").grid(row=0, column=0, padx=5)
        mode_combo = ttk.Combobox(
            cap_frame, textvariable=self.capture_mode,
            values=[
                "Screen Map", "Average Color", "Dominant Color",
                "Edge Sampling", "Quadrant Colors", "Most Vibrant",
                "Warm Bias", "Cool Bias"
            ],
            state="readonly", width=18
        )
        mode_combo.grid(row=0, column=1, padx=5)
        
        # Custom region
        tk.Checkbutton(
            cap_frame, text="Use Custom Region",
            variable=self.use_custom_region,
            command=self.toggle_region_inputs
        ).grid(row=0, column=2, padx=15)
        
        self.reg_frame = ttk.Frame(cap_frame)
        self.reg_frame.grid(row=0, column=3, padx=5)
        
        validate_cmd = (self.root.register(self.validate_percent), "%P")
        
        ttk.Label(self.reg_frame, text="X%:").pack(side="left")
        self.ent_x = ttk.Entry(self.reg_frame, width=4, textvariable=self.region_x, validate="key", validatecommand=validate_cmd)
        self.ent_x.pack(side="left", padx=2)
        
        ttk.Label(self.reg_frame, text="Y%:").pack(side="left")
        self.ent_y = ttk.Entry(self.reg_frame, width=4, textvariable=self.region_y, validate="key", validatecommand=validate_cmd)
        self.ent_y.pack(side="left", padx=2)
        
        ttk.Label(self.reg_frame, text="W%:").pack(side="left")
        self.ent_w = ttk.Entry(self.reg_frame, width=4, textvariable=self.region_w, validate="key", validatecommand=validate_cmd)
        self.ent_w.pack(side="left", padx=2)
        
        ttk.Label(self.reg_frame, text="H%:").pack(side="left")
        self.ent_h = ttk.Entry(self.reg_frame, width=4, textvariable=self.region_h, validate="key", validatecommand=validate_cmd)
        self.ent_h.pack(side="left", padx=2)
        
        self.toggle_region_inputs()
        
        # ===== Status Bar =====
        self.status_bar = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    # ===== UI Helper Methods =====
    
    def _on_mode_change(self, event):
        """Handle connection mode change."""
        mode = self.connection_mode.get()
        
        # Hide all frames
        self.usb_frame.pack_forget()
        self.ws_frame.pack_forget()
        
        # Show appropriate frame
        if mode == "USB":
            self.usb_frame.pack(fill="x", pady=5)
        elif mode == "WebSocket":
            self.ws_frame.pack(fill="x", pady=5)
    
    def refresh_ports(self):
        """Refresh available COM ports."""
        if SERIAL_AVAILABLE:
            ports = [port.device for port in serial.tools.list_ports.comports()]
            self.port_combo["values"] = ports
            if ports:
                self.port_combo.set(ports[0])
    
    def validate_percent(self, val):
        """Validate percentage input (0-100)."""
        if val == "":
            return True
        try:
            v = int(val)
            return 0 <= v <= 100
        except ValueError:
            return False
    
    def toggle_region_inputs(self):
        """Enable/disable region inputs based on checkbox."""
        state = "normal" if self.use_custom_region.get() else "disabled"
        for widget in self.reg_frame.winfo_children():
            if isinstance(widget, ttk.Entry):
                widget.config(state=state)
    
    def update_brightness(self, value):
        """Handle brightness slider change."""
        brightness = int(float(value))
        percent = int((brightness / 255) * 100)
        
        if hasattr(self, 'brightness_label'):
            self.brightness_label.config(text=f"{percent}%")
        
        if self.conn.connected:
            self.conn.send_command({"cmd": "brightness", "value": brightness})
    
    def update_smooth_label(self, value):
        """Update smoothing label."""
        smooth = int(float(value))
        if hasattr(self, 'smooth_label'):
            self.smooth_label.config(text=f"{smooth}%")
    
    def apply_led_count(self):
        """Apply manual LED count override."""
        try:
            new_count = int(self.led_count_var.get())
            if 1 <= new_count <= 300:
                self.num_leds = new_count
                self.initialize_led_positions()
                self.led_count_label.config(text=f"(manually set to {new_count})", foreground="orange")
                print(f"[App] LED count manually set to {new_count}")
                messagebox.showinfo("Success", f"LED count set to {new_count}")
            else:
                messagebox.showerror("Error", "LED count must be 1-300")
        except ValueError:
            messagebox.showerror("Error", "Invalid LED count")
    
    # ===== Connection Methods =====
    
    def connect_device(self):
        """Connect to device based on selected mode."""
        mode = self.connection_mode.get()
        
        if mode == "USB":
            port = self.port_combo.get()
            if not port:
                messagebox.showwarning("Warning", "Please select a COM port")
                return
            
            self.status_label.config(text="Connecting...", foreground="orange")
            self.root.update()
            
            if self.conn.connect_usb(port):
                self.status_label.config(text=f"Connected (USB: {port})", foreground="green")
            else:
                self.status_label.config(text="Connection Failed", foreground="red")
                
        elif mode == "WebSocket":
            ip = self.ip_entry.get().strip()
            if not ip:
                messagebox.showwarning("Warning", "Please enter IP address")
                return
            
            self.status_label.config(text="Connecting...", foreground="orange")
            self.root.update()
            
            if self.conn.connect_websocket(ip):
                self.status_label.config(text=f"Connected (WS: {ip})", foreground="green")
            else:
                self.status_label.config(text="Connection Failed", foreground="red")
    
    def disconnect_device(self):
        """Disconnect from device."""
        self.conn.disconnect()
        self.status_label.config(text="Not Connected", foreground="red")
    
    def _on_connected(self, mode, details):
        """Callback when connection established."""
        self.num_leds = self.conn.led_count
        self.initialize_led_positions()
        
        def update_ui():
            self.info_label.config(text=f"Connected! Found {self.num_leds} LEDs. Ready to calibrate.")
            if hasattr(self, 'led_count_var'):
                self.led_count_var.set(str(self.num_leds))
            if hasattr(self, 'led_count_label'):
                self.led_count_label.config(text=f"(synced: {self.num_leds})", foreground="green")
        self.root.after(0, update_ui)
    
    def _on_disconnected(self):
        """Callback when disconnected."""
        self.root.after(0, lambda: self.status_label.config(
            text="Disconnected", foreground="red"
        ))
    
    def _on_message(self, data):
        """Handle message from device."""
        if data.get("type") == "info":
            new_led_count = data.get("ledCount", 60)
            if new_led_count != self.num_leds:
                print(f"[App] Updating LED count: {self.num_leds} -> {new_led_count}")
                self.num_leds = new_led_count
                self.initialize_led_positions()
            
            # Update UI on the main thread
            def update_ui():
                if hasattr(self, 'led_count_var'):
                    self.led_count_var.set(str(new_led_count))
                if hasattr(self, 'led_count_label'):
                    self.led_count_label.config(text=f"(synced: {new_led_count})", foreground="green")
            self.root.after(0, update_ui)
    
    def _on_error(self, error):
        """Handle connection error."""
        self.root.after(0, lambda: messagebox.showerror("Connection Error", error))
    
    # ===== LED Calibration =====
    
    def initialize_led_positions(self):
        """Initialize LED positions with default grid layout."""
        # Trim excess positions if num_leds decreased
        if len(self.led_positions) > self.num_leds:
            self.led_positions = self.led_positions[:self.num_leds]
        
        # Add positions for any missing LEDs using grid layout
        cols = int(np.ceil(np.sqrt(self.num_leds)))
        rows = int(np.ceil(self.num_leds / cols))
        
        for i in range(len(self.led_positions), self.num_leds):
            row = i // cols
            col = i % cols
            
            x = col / max(cols - 1, 1) if cols > 1 else 0.5
            y = row / max(rows - 1, 1) if rows > 1 else 0.5
            
            self.led_positions.append({"x": x, "y": y})
        
        self.draw_led_map()
    
    def draw_led_map(self):
        """Draw LED positions on canvas."""
        self.canvas.delete("all")
        
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        
        if w <= 1 or h <= 1:
            return
        
        margin = 40
        
        # Draw screen rectangle
        self.canvas.create_rectangle(
            margin, margin, w - margin, h - margin,
            outline="gray", width=3, dash=(5, 5)
        )
        
        # Draw corner labels
        self.canvas.create_text(margin - 20, margin - 20, text="TOP-LEFT", fill="gray", font=("Arial", 8))
        self.canvas.create_text(w - margin + 20, margin - 20, text="TOP-RIGHT", fill="gray", font=("Arial", 8))
        self.canvas.create_text(margin - 20, h - margin + 20, text="BOTTOM-LEFT", fill="gray", font=("Arial", 8))
        self.canvas.create_text(w - margin + 20, h - margin + 20, text="BOTTOM-RIGHT", fill="gray", font=("Arial", 8))
        
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
                fill=color, outline="white", width=2
            )
            
            # Label LEDs (every 5th for larger counts, all for small counts)
            if self.num_leds <= 20 or i % 5 == 0 or (self.calibration_mode and i == self.current_led_index):
                self.canvas.create_text(x, y - 15, text=str(i), fill="white", font=("Arial", 9, "bold"))
    
    def start_calibration(self):
        """Start LED calibration process."""
        if not self.conn.connected:
            messagebox.showwarning("Warning", "Please connect to device first")
            return
        
        self.calibration_mode = True
        self.current_led_index = 0
        
        self.conn.send_command({"cmd": "calibrate_start"})
        self.conn.send_command({"cmd": "highlight", "led": 0})
        
        self.info_label.config(
            text=f"🎯 Calibrating LED 0/{self.num_leds}\n"
                 f"The LED should be blinking WHITE on your strip.\n"
                 f"Click on the canvas where this LED is physically located.",
            foreground="orange"
        )
        
        self.draw_led_map()
    
    def canvas_click(self, event):
        """Handle click on canvas during calibration."""
        if not self.calibration_mode:
            return
        
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        margin = 40
        
        # Convert click to normalized position (0-1)
        x = max(0, min(1, (event.x - margin) / (w - 2 * margin)))
        y = max(0, min(1, (event.y - margin) / (h - 2 * margin)))
        
        self.led_positions[self.current_led_index] = {"x": x, "y": y}
        self.current_led_index += 1
        
        if self.current_led_index < self.num_leds:
            self.conn.send_command({"cmd": "highlight", "led": self.current_led_index})
            self.info_label.config(
                text=f"🎯 Calibrating LED {self.current_led_index}/{self.num_leds}\n"
                     f"Click where this LED is located on your screen.",
                foreground="orange"
            )
            self.draw_led_map()
        else:
            self.finish_calibration()
    
    def finish_calibration(self):
        """Complete calibration and save mapping."""
        self.calibration_mode = False
        
        # Send mapping to device
        mapping = [
            {"x": int(led["x"] * 255), "y": int(led["y"] * 255)}
            for led in self.led_positions
        ]
        
        self.conn.send_command({"cmd": "save_map", "mapping": mapping})
        self.conn.send_command({"cmd": "calibrate_end"})
        
        self.info_label.config(
            text="✅ Calibration complete! Configuration saved.\n"
                 "You can now start the Ambilight effect.",
            foreground="green"
        )
        
        self.draw_led_map()
        messagebox.showinfo("Success", "Calibration complete!")
    
    def test_pattern(self):
        """Run LED test pattern."""
        if self.conn.connected:
            self.conn.send_command({"cmd": "test_pattern"})
            self.status_bar.config(text="Running test pattern...")
            self.root.after(2000, lambda: self.status_bar.config(text="Ready"))
    
    # ===== Configuration Persistence =====
    
    def save_config(self):
        """Save configuration to file."""
        config = {
            "num_leds": self.num_leds,
            "led_positions": self.led_positions,
            "connection_mode": self.connection_mode.get(),
            "com_port": self.port_combo.get() if hasattr(self, 'port_combo') else "",
            "ip_address": self.ip_entry.get() if hasattr(self, 'ip_entry') else "",
        }
        
        try:
            with open("ambilight_config.json", "w") as f:
                json.dump(config, f, indent=2)
            messagebox.showinfo("Success", "Configuration saved to ambilight_config.json")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")
    
    def load_config(self):
        """Load configuration from file."""
        try:
            with open("ambilight_config.json", "r") as f:
                config = json.load(f)
            
            self.num_leds = config.get("num_leds", 60)
            self.led_positions = config.get("led_positions", [])
            
            # Restore connection settings
            if "connection_mode" in config:
                self.connection_mode.set(config["connection_mode"])
                self._on_mode_change(None)
            
            if "com_port" in config and config["com_port"]:
                ports = list(self.port_combo["values"])
                if config["com_port"] in ports:
                    self.port_combo.set(config["com_port"])
            
            if "ip_address" in config:
                self.ip_entry.delete(0, tk.END)
                self.ip_entry.insert(0, config["ip_address"])
            
            self.draw_led_map()
            messagebox.showinfo("Success", "Configuration loaded")
            
        except FileNotFoundError:
            messagebox.showwarning("Warning", "No saved configuration found")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {e}")
    
    # ===== Ambilight Capture =====
    
    def start_ambilight(self):
        """Start the ambilight capture loop."""
        if not self.conn.connected:
            messagebox.showwarning("Warning", "Please connect to device first")
            return
        
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_bar.config(text="Ambilight running...")
        
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()
    
    def stop_ambilight(self):
        """Stop the ambilight capture loop."""
        with self._lock:
            self.is_running = False
            self.prev_colors = None
        
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_bar.config(text="Ambilight stopped")
        
        if self.conn.connected:
            self.conn.send_command({"cmd": "clear"})
    
    def capture_loop(self):
        """Main capture loop - runs in background thread."""
        fps = int(self.fps_var.get())
        delay = 1.0 / fps
        frame_count = 0
        
        while self.is_running:
            try:
                # Calculate capture region
                bbox = None
                if self.use_custom_region.get():
                    try:
                        # Use cached screen size for efficiency
                        if self._screen_size is None:
                            full_screen = ImageGrab.grab()
                            self._screen_size = full_screen.size
                        sw, sh = self._screen_size
                        
                        rx = int(int(self.region_x.get() or "0") / 100 * sw)
                        ry = int(int(self.region_y.get() or "0") / 100 * sh)
                        rw = int(int(self.region_w.get() or "100") / 100 * sw)
                        rh = int(int(self.region_h.get() or "100") / 100 * sh)
                        
                        rx = max(0, min(rx, sw - 1))
                        ry = max(0, min(ry, sh - 1))
                        rw = max(1, min(rw, sw - rx))
                        rh = max(1, min(rh, sh - ry))
                        
                        bbox = (rx, ry, rx + rw, ry + rh)
                    except Exception as e:
                        print(f"Region calc error: {e}")
                        bbox = None
                
                # Capture screen
                screen = ImageGrab.grab(bbox=bbox)
                screen = screen.resize((100, 100))
                pixels = np.array(screen)
                
                h, w = pixels.shape[:2]
                brightness = int(self.brightness_scale.get())
                
                led_colors = bytearray()
                mode = self.capture_mode.get()
                
                # Process based on capture mode
                if mode == "Average Color":
                    led_colors = self._process_average_color(pixels, brightness)
                    
                elif mode == "Dominant Color":
                    led_colors = self._process_dominant_color(pixels, brightness)
                    
                elif mode == "Edge Sampling":
                    led_colors = self._process_edge_sampling(pixels, brightness)
                    
                elif mode == "Quadrant Colors":
                    led_colors = self._process_quadrant_colors(pixels, brightness)
                    
                elif mode == "Most Vibrant":
                    led_colors = self._process_most_vibrant(pixels, brightness)
                    
                elif mode == "Warm Bias":
                    led_colors = self._process_warm_bias(pixels, brightness)
                    
                elif mode == "Cool Bias":
                    led_colors = self._process_cool_bias(pixels, brightness)
                    
                else:  # Screen Map
                    led_colors = self._process_screen_map(pixels, brightness)
                
                # Apply smoothing with thread safety
                smooth_factor = self.smooth_scale.get() / 100.0
                
                with self._lock:
                    if self.prev_colors is not None and len(self.prev_colors) == len(led_colors):
                        smoothed = bytearray(len(led_colors))
                        for i in range(len(led_colors)):
                            smoothed[i] = int(
                                self.prev_colors[i] * smooth_factor +
                                led_colors[i] * (1 - smooth_factor)
                            )
                        led_colors = smoothed
                    
                    self.prev_colors = bytearray(led_colors)
                
                # Send to device
                self.conn.send_colors(bytes(led_colors))
                
                # Debug logging
                frame_count += 1
                if frame_count % 30 == 0:
                    sample = []
                    for i in range(min(3, self.num_leds)):
                        idx = i * 3
                        if idx + 2 < len(led_colors):
                            sample.append(f"LED{i}:({led_colors[idx]},{led_colors[idx+1]},{led_colors[idx+2]})")
                    print(f"[Frame {frame_count}] Mode: {mode} | {', '.join(sample)}...")
                
                time.sleep(delay)
                
            except Exception as e:
                print(f"Capture error: {e}")
                time.sleep(0.1)
    
    # ===== Capture Mode Processors =====
    
    def _apply_brightness(self, r, g, b, brightness):
        """Apply brightness to RGB values with black threshold."""
        if r + g + b < 15:
            return 0, 0, 0
        return (
            int(r * brightness / 255),
            int(g * brightness / 255),
            int(b * brightness / 255)
        )
    
    def _process_average_color(self, pixels, brightness):
        """Calculate average color of screen."""
        avg = np.mean(pixels, axis=(0, 1)).astype(int)
        r, g, b = self._apply_brightness(avg[0], avg[1], avg[2], brightness)
        
        led_colors = bytearray()
        for _ in range(self.num_leds):
            led_colors.extend([r, g, b])
        return led_colors
    
    def _process_dominant_color(self, pixels, brightness):
        """Extract most vibrant/saturated color from screen."""
        flat_pixels = pixels.reshape(-1, 3)
        
        max_vals = np.max(flat_pixels, axis=1)
        min_vals = np.min(flat_pixels, axis=1)
        
        with np.errstate(divide='ignore', invalid='ignore'):
            saturation = np.where(
                max_vals > 0,
                ((max_vals - min_vals) * 255) / max_vals,
                0
            ).astype(np.uint8)
        
        colorful_mask = (saturation > 50) & (max_vals > 30) & (max_vals < 240)
        
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
            avg = np.mean(flat_pixels, axis=0).astype(int)
            r_raw, g_raw, b_raw = avg[0], avg[1], avg[2]
        
        r, g, b = self._apply_brightness(r_raw, g_raw, b_raw, brightness)
        
        led_colors = bytearray()
        for _ in range(self.num_leds):
            led_colors.extend([r, g, b])
        return led_colors
    
    def _process_edge_sampling(self, pixels, brightness):
        """Sample from screen edges - designed for 16 LEDs (4 per side)."""
        h, w = pixels.shape[:2]
        edge_width = 10
        
        led_colors = bytearray()
        leds_per_side = max(1, self.num_leds // 4)
        
        for i in range(self.num_leds):
            side = min(3, i // leds_per_side)  # Clamp to 0-3 for 4 sides
            pos = i % leds_per_side
            
            if side == 0:  # Top edge
                x_start = int((pos / leds_per_side) * w)
                x_end = int(((pos + 1) / leds_per_side) * w)
                region = pixels[0:edge_width, x_start:x_end]
            elif side == 1:  # Right edge
                y_start = int((pos / leds_per_side) * h)
                y_end = int(((pos + 1) / leds_per_side) * h)
                region = pixels[y_start:y_end, w-edge_width:w]
            elif side == 2:  # Bottom edge (reversed)
                x_start = int(((leds_per_side - 1 - pos) / leds_per_side) * w)
                x_end = int(((leds_per_side - pos) / leds_per_side) * w)
                region = pixels[h-edge_width:h, x_start:x_end]
            else:  # Left edge (reversed)
                y_start = int(((leds_per_side - 1 - pos) / leds_per_side) * h)
                y_end = int(((leds_per_side - pos) / leds_per_side) * h)
                region = pixels[y_start:y_end, 0:edge_width]
            
            if region.size > 0:
                avg = np.mean(region, axis=(0, 1)).astype(int)
                r, g, b = self._apply_brightness(avg[0], avg[1], avg[2], brightness)
            else:
                r, g, b = 0, 0, 0
            
            led_colors.extend([r, g, b])
        
        return led_colors
    
    def _process_quadrant_colors(self, pixels, brightness):
        """Divide screen into 4 quadrants, assign colors to LED groups."""
        h, w = pixels.shape[:2]
        
        quadrants = [
            pixels[0:h//2, 0:w//2],       # Top-left
            pixels[0:h//2, w//2:w],       # Top-right
            pixels[h//2:h, 0:w//2],       # Bottom-left
            pixels[h//2:h, w//2:w],       # Bottom-right
        ]
        
        led_colors = bytearray()
        leds_per_quad = max(1, self.num_leds // 4)
        
        for q_idx, quad in enumerate(quadrants):
            avg = np.mean(quad, axis=(0, 1)).astype(int)
            r, g, b = self._apply_brightness(avg[0], avg[1], avg[2], brightness)
            
            for _ in range(leds_per_quad):
                led_colors.extend([r, g, b])
        
        # Fill remaining LEDs if num_leds isn't divisible by 4
        while len(led_colors) < self.num_leds * 3:
            led_colors.extend([0, 0, 0])
        
        return led_colors[:self.num_leds * 3]
    
    def _process_most_vibrant(self, pixels, brightness):
        """Find the single most saturated pixel color."""
        flat_pixels = pixels.reshape(-1, 3)
        max_vals = np.max(flat_pixels, axis=1)
        min_vals = np.min(flat_pixels, axis=1)
        
        with np.errstate(divide='ignore', invalid='ignore'):
            saturation = np.where(max_vals > 0, (max_vals - min_vals) / max_vals, 0)
        
        max_sat_idx = np.argmax(saturation)
        most_vibrant = flat_pixels[max_sat_idx]
        
        r, g, b = self._apply_brightness(
            int(most_vibrant[0]), int(most_vibrant[1]), int(most_vibrant[2]), brightness
        )
        
        led_colors = bytearray()
        for _ in range(self.num_leds):
            led_colors.extend([r, g, b])
        return led_colors
    
    def _process_warm_bias(self, pixels, brightness):
        """Average color shifted warmer (more red, less blue)."""
        avg = np.mean(pixels, axis=(0, 1)).astype(int)
        r_raw = min(255, int(avg[0] * 1.3))
        g_raw = avg[1]
        b_raw = max(0, int(avg[2] * 0.7))
        
        r, g, b = self._apply_brightness(r_raw, g_raw, b_raw, brightness)
        
        led_colors = bytearray()
        for _ in range(self.num_leds):
            led_colors.extend([r, g, b])
        return led_colors
    
    def _process_cool_bias(self, pixels, brightness):
        """Average color shifted cooler (more blue, less red)."""
        avg = np.mean(pixels, axis=(0, 1)).astype(int)
        r_raw = max(0, int(avg[0] * 0.7))
        g_raw = avg[1]
        b_raw = min(255, int(avg[2] * 1.3))
        
        r, g, b = self._apply_brightness(r_raw, g_raw, b_raw, brightness)
        
        led_colors = bytearray()
        for _ in range(self.num_leds):
            led_colors.extend([r, g, b])
        return led_colors
    
    def _process_screen_map(self, pixels, brightness):
        """Sample screen at each LED's calibrated position."""
        # Ensure we have positions for all LEDs
        while len(self.led_positions) < self.num_leds:
            self.led_positions.append({"x": 0.5, "y": 0.5})
        
        h, w = pixels.shape[:2]
        sample_radius = 1
        
        led_colors = bytearray()
        
        # Only iterate up to num_leds to ensure correct output size
        for i in range(self.num_leds):
            led = self.led_positions[i]
            x = int(led["x"] * (w - 1))
            y = int(led["y"] * (h - 1))
            
            # Sample small region around position
            x_start = max(0, x - sample_radius)
            x_end = min(w, x + sample_radius + 1)
            y_start = max(0, y - sample_radius)
            y_end = min(h, y + sample_radius + 1)
            
            region = pixels[y_start:y_end, x_start:x_end]
            
            if region.size > 0:
                avg = np.mean(region, axis=(0, 1)).astype(int)
                r, g, b = self._apply_brightness(avg[0], avg[1], avg[2], brightness)
            else:
                r, g, b = 0, 0, 0
            
            led_colors.extend([r, g, b])
        
        return led_colors


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = AmbilightController(root)
    root.mainloop()
