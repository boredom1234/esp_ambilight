import tkinter as tk
from tkinter import messagebox, colorchooser
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import time
import json
import os
import numpy as np
from PIL import ImageGrab, Image
import config
from connection_manager import ConnectionManager, SERIAL_AVAILABLE, WEBSOCKET_AVAILABLE
import image_processor
import effects

# Optional import for system tray
try:
    import pystray

    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    print("Warning: pystray not installed. System tray mode disabled.")

# Optional imports for serial
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    pass

try:
    from screeninfo import get_monitors

    SCREENINFO_AVAILABLE = True
except ImportError:
    SCREENINFO_AVAILABLE = False
    print("Warning: screeninfo not installed. Multi-monitor selection disabled.")


class AmbilightController:
    """Main application window."""

    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 Ambilight Controller")
        self.root.geometry("1050x950")
        self.root.minsize(900, 800)

        # Apply theme if not already applied (in case root passed from main is not ttk.Window)
        # However, main.py should be updated to pass ttk.Window.
        # For now, we assume root is compatible or we style frames.

        # Connection manager
        self.conn = ConnectionManager()
        self.conn.on_connected = self._on_connected
        self.conn.on_disconnected = self._on_disconnected
        self.conn.on_message = self._on_message
        self.conn.on_error = self._on_error

        # State
        self.num_leds = config.DEFAULT_LED_COUNT
        self.led_positions = []
        self.is_running = False
        self.calibration_mode = False
        self.current_led_index = 0
        self.prev_colors = None

        # Thread-safe parameter state
        self.current_brightness = 255
        self.current_smoothing = 0.0

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

        # Monitor selection
        self.selected_monitor = tk.StringVar(value="Primary")
        self.monitors = []  # List of detected monitors

        # Output mode: "Screen Capture", "Static Color", "Effect"
        self.output_mode = tk.StringVar(value="Screen Capture")

        # Static color settings
        self.static_color = (255, 147, 41)  # Default warm amber
        self.static_color_preview = None  # Canvas widget

        # Effect settings
        self.current_effect = tk.StringVar(value="Rainbow")
        self.effect_speed = tk.DoubleVar(value=1.0)
        self.effect_phase = 0.0
        self.effect_running = False

        # Presets
        self.presets = {}
        self.selected_preset = tk.StringVar(value="")
        self._load_presets()

        # System tray
        self.tray_icon = None
        self.minimized_to_tray = False

        self.create_ui()
        self.refresh_ports()

        # Setup system tray if available
        if TRAY_AVAILABLE:
            self._setup_tray()

    def create_ui(self):
        """Build the user interface."""

        # ===== Scrollable Container =====
        # Create a canvas with scrollbar for the main content
        self.scroll_canvas = tk.Canvas(self.root, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self.root, orient="vertical", command=self.scroll_canvas.yview
        )
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)

        # Create main frame inside canvas
        self.main_frame = ttk.Frame(self.scroll_canvas)
        self.canvas_window = self.scroll_canvas.create_window(
            (0, 0), window=self.main_frame, anchor="nw"
        )

        # Configure scroll region when frame size changes
        self.main_frame.bind("<Configure>", self._on_frame_configure)
        self.scroll_canvas.bind("<Configure>", self._on_canvas_configure)

        # Enable mousewheel scrolling
        self.scroll_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # ===== Connection Frame =====
        conn_frame = ttk.Labelframe(
            self.main_frame, text="Connection", padding=10, bootstyle="info"
        )
        conn_frame.pack(fill="x", padx=15, pady=5)

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
            mode_frame,
            textvariable=self.connection_mode,
            values=modes,
            state="readonly",
            width=12,
        )
        self.mode_combo.pack(side="left", padx=5)
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        # USB settings
        self.usb_frame = ttk.Frame(conn_frame)
        ttk.Label(self.usb_frame, text="COM Port:").pack(side="left", padx=5)
        self.port_combo = ttk.Combobox(self.usb_frame, width=12, state="readonly")
        self.port_combo.pack(side="left", padx=5)
        ttk.Button(self.usb_frame, text="🔄", width=3, command=self.refresh_ports).pack(
            side="left", padx=2
        )

        # WebSocket settings
        self.ws_frame = ttk.Frame(conn_frame)
        ttk.Label(self.ws_frame, text="IP Address:").pack(side="left", padx=5)
        self.ip_entry = ttk.Entry(self.ws_frame, width=15)
        self.ip_entry.insert(0, config.DEFAULT_IP)
        self.ip_entry.pack(side="left", padx=5)

        # Connect/Disconnect buttons
        btn_frame = ttk.Frame(conn_frame)
        btn_frame.pack(fill="x", pady=5)

        ttk.Button(btn_frame, text="Connect", command=self.connect_device).pack(
            side="left", padx=5
        )
        ttk.Button(btn_frame, text="Disconnect", command=self.disconnect_device).pack(
            side="left", padx=5
        )

        self.status_label = ttk.Label(btn_frame, text="Not Connected", foreground="red")
        self.status_label.pack(side="left", padx=20)

        # Manual LED count override
        led_frame = ttk.Frame(conn_frame)
        led_frame.pack(fill="x", pady=5)
        ttk.Label(led_frame, text="LED Count:").pack(side="left", padx=5)
        self.led_count_var = tk.StringVar(value="60")
        self.led_count_entry = ttk.Entry(
            led_frame, width=6, textvariable=self.led_count_var
        )
        self.led_count_entry.pack(side="left", padx=5)
        ttk.Button(led_frame, text="Apply", command=self.apply_led_count).pack(
            side="left", padx=5
        )
        self.led_count_label = ttk.Label(
            led_frame, text="(synced from device)", foreground="gray"
        )
        self.led_count_label.pack(side="left", padx=5)

        # Show USB frame by default
        self._on_mode_change(None)

        # ===== Calibration Frame =====
        cal_frame = ttk.Labelframe(
            self.main_frame, text="LED Calibration", padding=10, bootstyle="warning"
        )
        cal_frame.pack(fill="both", expand=True, padx=15, pady=5)

        cal_btn_frame = ttk.Frame(cal_frame)
        cal_btn_frame.pack(pady=10)

        ttk.Button(
            cal_btn_frame,
            text="Start Calibration",
            command=self.start_calibration,
            bootstyle="warning",
        ).pack(side="left", padx=5)
        ttk.Button(
            cal_btn_frame,
            text="Test Pattern",
            command=self.test_pattern,
            bootstyle="secondary-outline",
        ).pack(side="left", padx=5)
        ttk.Button(
            cal_btn_frame,
            text="Load Config",
            command=self.load_config,
            bootstyle="info-outline",
        ).pack(side="left", padx=5)
        ttk.Button(
            cal_btn_frame,
            text="Save Config",
            command=self.save_config,
            bootstyle="success-outline",
        ).pack(side="left", padx=5)

        # Canvas for LED mapping visualization
        self.canvas = tk.Canvas(cal_frame, bg="black", height=250)
        self.canvas.pack(fill="both", expand=True, pady=5)
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<Configure>", lambda e: self.draw_led_map())

        self.info_label = ttk.Label(
            cal_frame,
            text="Connect to device to begin",
            wraplength=800,
            font=("Arial", 10),
        )
        self.info_label.pack(pady=5)

        # ===== Controls Frame =====
        ctrl_frame = ttk.Labelframe(
            self.main_frame, text="Ambilight Controls", padding=10, bootstyle="success"
        )
        ctrl_frame.pack(fill="x", padx=15, pady=5)

        # Start/Stop
        self.start_btn = ttk.Button(
            ctrl_frame,
            text="▶ Start Ambilight",
            command=self.start_ambilight,
            bootstyle="success",
        )
        self.start_btn.pack(side="left", padx=10)

        self.stop_btn = ttk.Button(
            ctrl_frame,
            text="⏹ Stop",
            command=self.stop_ambilight,
            state="disabled",
            bootstyle="danger",
        )
        self.stop_btn.pack(side="left", padx=10)

        ttk.Button(
            ctrl_frame,
            text="🔲 Clear LEDs",
            command=self.force_clear_leds,
            bootstyle="secondary-outline",
        ).pack(side="left", padx=10)

        # Meters Container
        meter_frame = ttk.Frame(ctrl_frame)
        meter_frame.pack(fill="x", pady=5)

        # Brightness Meter
        b_container = ttk.Frame(meter_frame)
        b_container.pack(side="left", expand=True)

        self.brightness_meter = ttk.Meter(
            b_container,
            metersize=100,
            padding=3,
            amountused=100,
            amounttotal=100,
            metertype="semi",
            subtext="Brightness",
            interactive=True,
            bootstyle="warning",
        )
        self.brightness_meter.pack()
        # Track changes using after loop
        self._last_brightness = 100
        self._setup_brightness_polling()

        # Smoothing Meter
        s_container = ttk.Frame(meter_frame)
        s_container.pack(side="left", expand=True)

        self.smooth_meter = ttk.Meter(
            s_container,
            metersize=100,
            padding=3,
            amountused=0,
            amounttotal=100,
            metertype="semi",
            subtext="Smoothing",
            interactive=True,
            bootstyle="primary",
        )
        self.smooth_meter.pack()
        # Track changes using after loop
        self._last_smoothing = 0
        self._setup_smoothing_polling()

        # FPS Selection (Moved below meters)
        fps_frame = ttk.Frame(ctrl_frame)
        fps_frame.pack(fill="x", pady=10, padx=20)

        ttk.Label(fps_frame, text="Target FPS:").pack(side="left", padx=5)
        self.fps_var = tk.StringVar(value="60")
        fps_combo = ttk.Combobox(
            fps_frame,
            textvariable=self.fps_var,
            values=["15", "20", "30", "45", "60"],
            width=5,
            state="readonly",
        )
        fps_combo.pack(side="left", padx=5)

        # ===== Effects & Presets Frame =====
        effects_frame = ttk.Labelframe(
            self.main_frame, text="Effects & Presets", padding=10, bootstyle="secondary"
        )
        effects_frame.pack(fill="x", padx=15, pady=5)

        # Row 1: Output mode selection
        mode_row = ttk.Frame(effects_frame)
        mode_row.pack(fill="x", pady=5)

        ttk.Label(mode_row, text="Output Mode:").pack(side="left", padx=5)

        for mode in ["Screen Capture", "Static Color", "Effect"]:
            ttk.Radiobutton(
                mode_row,
                text=mode,
                value=mode,
                variable=self.output_mode,
                command=self._on_output_mode_change,
                bootstyle="toolbutton",
            ).pack(side="left", padx=5)

        # Row 2: Static Color Controls
        static_row = ttk.Frame(effects_frame)
        static_row.pack(fill="x", pady=5)

        ttk.Label(static_row, text="Static Color:").pack(side="left", padx=5)

        # Color preview canvas
        self.static_color_preview = tk.Canvas(
            static_row,
            width=40,
            height=25,
            bg=self._rgb_to_hex(self.static_color),
            highlightthickness=1,
            highlightbackground="white",
        )
        self.static_color_preview.pack(side="left", padx=5)

        ttk.Button(
            static_row,
            text="Pick Color",
            command=self._pick_color,
            bootstyle="info-outline",
        ).pack(side="left", padx=5)

        # Preset dropdown
        ttk.Label(static_row, text="Preset:").pack(side="left", padx=(20, 5))

        self.preset_combo = ttk.Combobox(
            static_row,
            textvariable=self.selected_preset,
            values=list(self.presets.keys()),
            width=15,
            state="readonly",
        )
        self.preset_combo.pack(side="left", padx=5)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)

        ttk.Button(
            static_row,
            text="Save",
            command=self._save_preset,
            bootstyle="success-outline",
            width=6,
        ).pack(side="left", padx=2)

        ttk.Button(
            static_row,
            text="Delete",
            command=self._delete_preset,
            bootstyle="danger-outline",
            width=6,
        ).pack(side="left", padx=2)

        # Row 3: Effect Controls
        effect_row = ttk.Frame(effects_frame)
        effect_row.pack(fill="x", pady=5)

        ttk.Label(effect_row, text="Effect:").pack(side="left", padx=5)

        effect_combo = ttk.Combobox(
            effect_row,
            textvariable=self.current_effect,
            values=list(effects.EFFECTS.keys()),
            width=12,
            state="readonly",
        )
        effect_combo.pack(side="left", padx=5)

        ttk.Label(effect_row, text="Speed:").pack(side="left", padx=(20, 5))

        speed_scale = ttk.Scale(
            effect_row,
            variable=self.effect_speed,
            from_=0.1,
            to=3.0,
            length=150,
            bootstyle="info",
        )
        speed_scale.pack(side="left", padx=5)

        # ===== Capture Settings Frame =====
        cap_frame = ttk.Labelframe(
            self.main_frame, text="Capture Settings", padding=10, bootstyle="info"
        )
        cap_frame.pack(fill="x", padx=15, pady=5)

        # Mode selection
        ttk.Label(cap_frame, text="Mode:").grid(row=0, column=0, padx=5)
        mode_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.capture_mode,
            values=[
                "Screen Map",
                "Average Color",
                "Dominant Color",
                "Edge Sampling",
                "Quadrant Colors",
                "Most Vibrant",
                "Warm Bias",
                "Cool Bias",
            ],
            state="readonly",
            width=18,
        )
        mode_combo.grid(row=0, column=1, padx=5)

        # Custom region
        tk.Checkbutton(
            cap_frame,
            text="Use Custom Region",
            variable=self.use_custom_region,
            command=self.toggle_region_inputs,
        ).grid(row=0, column=2, padx=15)

        self.reg_frame = ttk.Frame(cap_frame)
        self.reg_frame.grid(row=0, column=3, padx=5)

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

        # Monitor selection (row 1)
        ttk.Label(cap_frame, text="Monitor:").grid(
            row=1, column=0, padx=5, pady=5, sticky="w"
        )
        self.monitor_combo = ttk.Combobox(
            cap_frame, textvariable=self.selected_monitor, state="readonly", width=35
        )
        self.monitor_combo.grid(
            row=1, column=1, columnspan=2, padx=5, pady=5, sticky="w"
        )
        ttk.Button(cap_frame, text="🔄", width=3, command=self.refresh_monitors).grid(
            row=1, column=3, padx=5, pady=5, sticky="w"
        )

        # Initialize monitor list
        self.refresh_monitors()

        # ===== Status Bar =====
        self.status_bar = ttk.Label(
            self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ===== Scroll Helper Methods =====

    def _on_frame_configure(self, event):
        """Update scroll region when frame size changes."""
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Resize the inner frame to match canvas width."""
        self.scroll_canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """Handle mousewheel scrolling."""
        self.scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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

    def refresh_monitors(self):
        """Detect and list all connected monitors."""
        if not SCREENINFO_AVAILABLE:
            self.monitor_combo["values"] = ["Primary (default)"]
            self.selected_monitor.set("Primary (default)")
            return

        try:
            self.monitors = list(get_monitors())
            monitor_names = []
            for i, m in enumerate(self.monitors):
                name = f"Monitor {i + 1}: {m.width}x{m.height} @ ({m.x}, {m.y})"
                if m.is_primary:
                    name += " [Primary]"
                monitor_names.append(name)

            self.monitor_combo["values"] = monitor_names
            if monitor_names:
                # Default to primary monitor
                primary_idx = 0
                for i, m in enumerate(self.monitors):
                    if m.is_primary:
                        primary_idx = i
                        break
                self.selected_monitor.set(monitor_names[primary_idx])
        except Exception as e:
            print(f"Error detecting monitors: {e}")
            self.monitor_combo["values"] = ["Primary (default)"]
            self.selected_monitor.set("Primary (default)")

    def get_selected_monitor_bbox(self):
        """Get the bounding box (x, y, x2, y2) of the selected monitor."""
        if not SCREENINFO_AVAILABLE or not self.monitors:
            return None  # Will capture primary screen

        try:
            idx = self.monitor_combo.current()
            if 0 <= idx < len(self.monitors):
                m = self.monitors[idx]
                return (m.x, m.y, m.x + m.width, m.y + m.height)
        except Exception:
            pass
        return None

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

    def _setup_brightness_polling(self):
        """Setup polling to detect brightness meter changes."""
        self._poll_brightness()

    def _poll_brightness(self):
        """Poll brightness meter for changes."""
        try:
            # Get current value from meter widget
            current = int(self.brightness_meter.amountusedvar.get())
            if current != self._last_brightness:
                self._last_brightness = current
                self._on_brightness_changed(current)
        except (AttributeError, ValueError, tk.TclError):
            pass
        # Continue polling
        self.root.after(100, self._poll_brightness)

    def _on_brightness_changed(self, percent):
        """Handle brightness change."""
        brightness = int((percent / 100) * 255)
        brightness = max(0, min(255, brightness))
        self.current_brightness = brightness
        if self.conn.connected:
            self.conn.send_command({"cmd": "brightness", "value": brightness})

    def _setup_smoothing_polling(self):
        """Setup polling to detect smoothing meter changes."""
        self._poll_smoothing()

    def _poll_smoothing(self):
        """Poll smoothing meter for changes."""
        try:
            # Get current value from meter widget
            current = int(self.smooth_meter.amountusedvar.get())
            if current != self._last_smoothing:
                self._last_smoothing = current
                self._on_smoothing_changed(current)
        except (AttributeError, ValueError, tk.TclError):
            pass
        # Continue polling
        self.root.after(100, self._poll_smoothing)

    def _on_smoothing_changed(self, percent):
        """Handle smoothing change."""
        self.current_smoothing = percent / 100.0

    def apply_led_count(self):
        """Apply manual LED count override."""
        try:
            new_count = int(self.led_count_var.get())
            if 1 <= new_count <= 300:
                self.num_leds = new_count
                self.initialize_led_positions()
                self.led_count_label.config(
                    text=f"(manually set to {new_count})", foreground="orange"
                )
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
                self.status_label.config(
                    text=f"Connected (USB: {port})", foreground="green"
                )
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
                self.status_label.config(
                    text=f"Connected (WS: {ip})", foreground="green"
                )
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
            self.info_label.config(
                text=f"Connected! Found {self.num_leds} LEDs. Ready to calibrate."
            )
            if hasattr(self, "led_count_var"):
                self.led_count_var.set(str(self.num_leds))
            if hasattr(self, "led_count_label"):
                self.led_count_label.config(
                    text=f"(synced: {self.num_leds})", foreground="green"
                )

        self.root.after(0, update_ui)

    def _on_disconnected(self):
        """Callback when disconnected."""
        self.root.after(
            0, lambda: self.status_label.config(text="Disconnected", foreground="red")
        )

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
                if hasattr(self, "led_count_var"):
                    self.led_count_var.set(str(new_led_count))
                if hasattr(self, "led_count_label"):
                    self.led_count_label.config(
                        text=f"(synced: {new_led_count})", foreground="green"
                    )

            self.root.after(0, update_ui)

    def _on_error(self, error):
        """Handle connection error."""
        self.root.after(0, lambda: messagebox.showerror("Connection Error", error))

    # ===== LED Calibration =====

    def initialize_led_positions(self):
        """Initialize LED positions with default grid layout."""
        # Trim excess positions if num_leds decreased
        if len(self.led_positions) > self.num_leds:
            self.led_positions = self.led_positions[: self.num_leds]

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

            # Label LEDs (every 5th for larger counts, all for small counts)
            if (
                self.num_leds <= 20
                or i % 5 == 0
                or (self.calibration_mode and i == self.current_led_index)
            ):
                self.canvas.create_text(
                    x, y - 15, text=str(i), fill="white", font=("Arial", 9, "bold")
                )

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
            foreground="orange",
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
                foreground="orange",
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
            foreground="green",
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
        config_data = {
            "num_leds": self.num_leds,
            "led_positions": self.led_positions,
            "connection_mode": self.connection_mode.get(),
            "com_port": self.port_combo.get() if hasattr(self, "port_combo") else "",
            "ip_address": self.ip_entry.get() if hasattr(self, "ip_entry") else "",
            "selected_monitor": self.selected_monitor.get(),
        }

        try:
            with open("ambilight_config.json", "w") as f:
                json.dump(config_data, f, indent=2)
            messagebox.showinfo(
                "Success", "Configuration saved to ambilight_config.json"
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")

    def load_config(self):
        """Load configuration from file."""
        try:
            with open("ambilight_config.json", "r") as f:
                config_data = json.load(f)

            self.num_leds = config_data.get("num_leds", 60)
            self.led_positions = config_data.get("led_positions", [])

            # Restore connection settings
            if "connection_mode" in config_data:
                self.connection_mode.set(config_data["connection_mode"])
                self._on_mode_change(None)

            if "com_port" in config_data and config_data["com_port"]:
                ports = list(self.port_combo["values"])
                if config_data["com_port"] in ports:
                    self.port_combo.set(config_data["com_port"])

            if "ip_address" in config_data:
                self.ip_entry.delete(0, tk.END)
                self.ip_entry.insert(0, config_data["ip_address"])

            # Restore monitor selection
            if "selected_monitor" in config_data:
                saved_monitor = config_data["selected_monitor"]
                monitors = list(self.monitor_combo["values"])
                if saved_monitor in monitors:
                    self.selected_monitor.set(saved_monitor)

            self.draw_led_map()
            messagebox.showinfo("Success", "Configuration loaded")

        except FileNotFoundError:
            messagebox.showwarning("Warning", "No saved configuration found")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {e}")

    # ===== Effects & Presets Methods =====

    def _rgb_to_hex(self, rgb):
        """Convert RGB tuple to hex color string."""
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    def _load_presets(self):
        """Load presets from file and merge with defaults."""
        # Start with built-in presets
        self.presets = dict(config.DEFAULT_PRESETS)

        # Load user presets from file
        try:
            presets_path = os.path.join(os.path.dirname(__file__), config.PRESETS_FILE)
            if os.path.exists(presets_path):
                with open(presets_path, "r") as f:
                    user_presets = json.load(f)
                    for name, rgb in user_presets.items():
                        self.presets[name] = tuple(rgb)
        except Exception as e:
            print(f"Error loading presets: {e}")

    def _save_presets_to_file(self):
        """Save user presets to file (excluding defaults)."""
        user_presets = {}
        for name, rgb in self.presets.items():
            if name not in config.DEFAULT_PRESETS:
                user_presets[name] = list(rgb)

        try:
            presets_path = os.path.join(os.path.dirname(__file__), config.PRESETS_FILE)
            with open(presets_path, "w") as f:
                json.dump(user_presets, f, indent=2)
        except Exception as e:
            print(f"Error saving presets: {e}")

    def _update_preset_dropdown(self):
        """Update preset dropdown with current presets."""
        self.preset_combo["values"] = list(self.presets.keys())

    def _pick_color(self):
        """Open color picker dialog."""
        initial = self.static_color
        result = colorchooser.askcolor(
            color=self._rgb_to_hex(initial), title="Choose Static Color"
        )
        if result[0]:
            self.static_color = tuple(int(c) for c in result[0])
            self._update_color_preview()
            self._apply_static_color()

    def _update_color_preview(self):
        """Update the color preview canvas."""
        if self.static_color_preview:
            self.static_color_preview.configure(bg=self._rgb_to_hex(self.static_color))

    def _save_preset(self):
        """Save current color as a preset."""
        from tkinter import simpledialog

        name = simpledialog.askstring("Save Preset", "Enter preset name:")
        if name and name.strip():
            name = name.strip()
            self.presets[name] = self.static_color
            self._save_presets_to_file()
            self._update_preset_dropdown()
            self.selected_preset.set(name)
            messagebox.showinfo("Success", f"Preset '{name}' saved!")

    def _delete_preset(self):
        """Delete selected preset."""
        name = self.selected_preset.get()
        if not name:
            messagebox.showwarning("Warning", "No preset selected")
            return

        if name in config.DEFAULT_PRESETS:
            messagebox.showwarning("Warning", "Cannot delete built-in presets")
            return

        if messagebox.askyesno("Confirm", f"Delete preset '{name}'?"):
            del self.presets[name]
            self._save_presets_to_file()
            self._update_preset_dropdown()
            self.selected_preset.set("")
            messagebox.showinfo("Success", f"Preset '{name}' deleted")

    def _on_preset_selected(self, event):
        """Handle preset selection."""
        name = self.selected_preset.get()
        if name and name in self.presets:
            self.static_color = self.presets[name]
            self._update_color_preview()
            self._apply_static_color()

    def _on_output_mode_change(self):
        """Handle output mode change."""
        mode = self.output_mode.get()

        # Stop effect loop if running
        self.effect_running = False

        if mode == "Static Color":
            self._apply_static_color()
        elif mode == "Effect":
            # Start effect loop if connected (works even without capture running)
            if self.conn.connected and not self.is_running:
                self.effect_running = True
                threading.Thread(target=self._run_effect_loop, daemon=True).start()
        # Screen Capture mode is handled normally in capture_loop

    def _run_effect_loop(self):
        """Run effects independently when capture is not running."""
        fps = 30
        delay = 1.0 / fps

        while self.effect_running and not self.is_running:
            if self.output_mode.get() != "Effect":
                break

            try:
                effect_name = self.current_effect.get()
                if effect_name in effects.EFFECTS:
                    effect_func = effects.EFFECTS[effect_name]
                    led_colors = effect_func(
                        self.num_leds, self.current_brightness, self.effect_phase
                    )
                    self.conn.send_colors(bytes(led_colors))
                    self.effect_phase += 0.02 * self.effect_speed.get()
                    if self.effect_phase > 100:
                        self.effect_phase = 0
            except Exception as e:
                print(f"Effect error: {e}")
                break

            time.sleep(delay)

    def _apply_static_color(self):
        """Send static color to LEDs."""
        if not self.conn.connected:
            return

        mode = self.output_mode.get()
        if mode != "Static Color":
            return

        r, g, b = self.static_color
        led_colors = effects.generate_static_color(
            self.num_leds, self.current_brightness, r, g, b
        )
        self.conn.send_colors(bytes(led_colors))

    def force_clear_leds(self):
        """Force turn off all LEDs."""
        if not self.conn.connected:
            messagebox.showwarning("Warning", "Not connected to device")
            return

        # Stop any running loops
        self.effect_running = False

        # Send clear command
        self.conn.send_command({"cmd": "clear"})

        # Also send all black colors
        led_colors = bytearray([0, 0, 0] * self.num_leds)
        self.conn.send_colors(bytes(led_colors))

        self.status_bar.config(text="LEDs cleared")

    # ===== System Tray Methods =====

    def _setup_tray(self):
        """Setup system tray icon and menu."""
        if not TRAY_AVAILABLE:
            return

        # Create a simple icon
        icon_size = 64
        icon_image = Image.new("RGB", (icon_size, icon_size), color=(50, 50, 50))
        # Draw a simple LED-like circle
        from PIL import ImageDraw

        draw = ImageDraw.Draw(icon_image)
        draw.ellipse([8, 8, 56, 56], fill=(255, 147, 41), outline=(255, 200, 100))

        menu = pystray.Menu(
            pystray.MenuItem("Show Window", self._show_window, default=True),
            pystray.MenuItem("Start Ambilight", self._tray_start),
            pystray.MenuItem("Stop Ambilight", self._tray_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit_app),
        )

        self.tray_icon = pystray.Icon(
            "ESP32 Ambilight", icon_image, "ESP32 Ambilight Controller", menu
        )

        # Run tray icon in background thread
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

        # Bind window close to minimize to tray
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        """Handle window close - minimize to tray instead of exit."""
        if TRAY_AVAILABLE and self.tray_icon:
            self.root.withdraw()
            self.minimized_to_tray = True
        else:
            self._quit_app()

    def _show_window(self, icon=None, item=None):
        """Show window from tray."""
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        """Restore window (must be called from main thread)."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.minimized_to_tray = False

    def _tray_start(self, icon=None, item=None):
        """Start ambilight from tray menu."""
        self.root.after(0, self.start_ambilight)

    def _tray_stop(self, icon=None, item=None):
        """Stop ambilight from tray menu."""
        self.root.after(0, self.stop_ambilight)

    def _quit_app(self, icon=None, item=None):
        """Actually quit the application."""
        if self.tray_icon:
            self.tray_icon.stop()
        self.is_running = False
        self.root.after(0, self.root.destroy)

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
        # Test LED strip
        self.test_pattern()
        if self.conn.connected:
            self.conn.send_command({"cmd": "clear"})

        # turn off all leds
        self.conn.send_command({"cmd": "clear"})

    def capture_loop(self):
        """Main capture loop - runs in background thread."""
        fps = int(self.fps_var.get())
        delay = 1.0 / fps
        frame_count = 0

        while self.is_running:
            try:
                # Check output mode
                output_mode = self.output_mode.get()

                # Handle Static Color mode
                if output_mode == "Static Color":
                    r, g, b = self.static_color
                    led_colors = effects.generate_static_color(
                        self.num_leds, self.current_brightness, r, g, b
                    )
                    self.conn.send_colors(bytes(led_colors))
                    time.sleep(delay)
                    continue

                # Handle Effect mode
                if output_mode == "Effect":
                    effect_name = self.current_effect.get()
                    if effect_name in effects.EFFECTS:
                        effect_func = effects.EFFECTS[effect_name]
                        led_colors = effect_func(
                            self.num_leds, self.current_brightness, self.effect_phase
                        )
                        self.conn.send_colors(bytes(led_colors))
                        # Advance phase based on speed
                        self.effect_phase += 0.02 * self.effect_speed.get()
                        if self.effect_phase > 100:
                            self.effect_phase = 0
                    time.sleep(delay)
                    continue

                # Screen Capture mode - get selected monitor bounds
                monitor_bbox = self.get_selected_monitor_bbox()

                # Calculate capture region
                if monitor_bbox:
                    mx, my, mx2, my2 = monitor_bbox
                    mw, mh = mx2 - mx, my2 - my

                    if self.use_custom_region.get():
                        # Custom region WITHIN the selected monitor
                        try:
                            rx = mx + int(int(self.region_x.get() or "0") / 100 * mw)
                            ry = my + int(int(self.region_y.get() or "0") / 100 * mh)
                            rw = int(int(self.region_w.get() or "100") / 100 * mw)
                            rh = int(int(self.region_h.get() or "100") / 100 * mh)
                            bbox = (rx, ry, rx + rw, ry + rh)
                        except Exception as e:
                            print(f"Region calc error: {e}")
                            bbox = monitor_bbox
                    else:
                        bbox = monitor_bbox
                else:
                    # Fallback: primary monitor only
                    bbox = None
                    if self.use_custom_region.get():
                        try:
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
                screen = ImageGrab.grab(bbox=bbox, all_screens=True)

                # Resize keeping aspect ratio to avoid distortion
                sw, sh = screen.size
                target_w = 160
                target_h = max(1, int(target_w * (sh / sw)))
                screen = screen.resize((target_w, target_h))

                pixels = np.array(screen)

                h, w = pixels.shape[:2]

                # Use thread-safe variable
                brightness = self.current_brightness

                led_colors = bytearray()
                mode = self.capture_mode.get()

                # Process based on capture mode
                if mode == "Average Color":
                    led_colors = image_processor.process_average_color(
                        pixels, brightness, self.num_leds
                    )

                elif mode == "Dominant Color":
                    led_colors = image_processor.process_dominant_color(
                        pixels, brightness, self.num_leds
                    )

                elif mode == "Edge Sampling":
                    led_colors = image_processor.process_edge_sampling(
                        pixels, brightness, self.num_leds
                    )

                elif mode == "Quadrant Colors":
                    led_colors = image_processor.process_quadrant_colors(
                        pixels, brightness, self.num_leds
                    )

                elif mode == "Most Vibrant":
                    led_colors = image_processor.process_most_vibrant(
                        pixels, brightness, self.num_leds
                    )

                elif mode == "Warm Bias":
                    led_colors = image_processor.process_warm_bias(
                        pixels, brightness, self.num_leds
                    )

                elif mode == "Cool Bias":
                    led_colors = image_processor.process_cool_bias(
                        pixels, brightness, self.num_leds
                    )

                else:  # Screen Map
                    # Thread-safe copy of positions
                    with self._lock:
                        current_positions = list(self.led_positions)

                    led_colors = image_processor.process_screen_map(
                        pixels, brightness, self.num_leds, current_positions
                    )

                # Apply smoothing with thread safety
                smooth_factor = self.current_smoothing

                with self._lock:
                    if self.prev_colors is not None and len(self.prev_colors) == len(
                        led_colors
                    ):
                        smoothed = bytearray(len(led_colors))
                        for i in range(len(led_colors)):
                            smoothed[i] = int(
                                self.prev_colors[i] * smooth_factor
                                + led_colors[i] * (1 - smooth_factor)
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
                            sample.append(
                                f"LED{i}:({led_colors[idx]},{led_colors[idx + 1]},{led_colors[idx + 2]})"
                            )
                    print(
                        f"[Frame {frame_count}] Mode: {mode} | {', '.join(sample)}..."
                    )

                time.sleep(delay)

            except Exception as e:
                print(f"Capture error: {e}")
                time.sleep(0.1)
