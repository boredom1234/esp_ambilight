import tkinter as tk
from tkinter import messagebox, colorchooser
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import time
import json
import os
import re
import numpy as np
from PIL import ImageGrab, Image, ImageTk
import config
from connection_manager import ConnectionManager, SERIAL_AVAILABLE, WEBSOCKET_AVAILABLE
import image_processor
import effects
from network_scanner import NetworkScanner

try:
    import dxcam

    DXCAM_AVAILABLE = True
except ImportError:
    DXCAM_AVAILABLE = False
    print("Warning: dxcam not installed. Using Pillow ImageGrab capture.")

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
    """Main application window with modern sidebar layout."""

    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 Ambilight Controller")
        self.root.geometry("1100x700")
        self.root.minsize(900, 600)
        
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
        self._lock = threading.Lock()
        self.low_latency_mode = config.LOW_LATENCY_DEFAULT
        self._adaptive_fps = 60
        self._capture_thread = None
        self.capture_backend_pref = tk.StringVar(value="Auto")
        self.capture_backend = "dxcam" if DXCAM_AVAILABLE else "pil"
        self._dxcam = None
        self._dxcam_device_idx = 0
        self._dxcam_output_idx = 0
        self._dxcam_started = False

        # Capture settings
        self.capture_mode = tk.StringVar(value="Screen Map")
        self.use_custom_region = tk.BooleanVar(value=False)
        self.region_x = tk.StringVar(value="25")
        self.region_y = tk.StringVar(value="25")
        self.region_w = tk.StringVar(value="50")
        self.region_h = tk.StringVar(value="50")
        self._screen_size = None

        # Connection mode
        self.connection_mode = tk.StringVar(value="USB")

        # Monitor selection
        self.selected_monitor = tk.StringVar(value="Primary")
        self.monitors = []

        # Output mode: "Screen Capture", "Static Color", "Effect"
        self.output_mode = tk.StringVar(value="Screen Capture")

        # Static color settings
        self.static_color = (255, 147, 41)
        self.static_color_preview = None

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

        # Network scanner
        self.network_scanner = NetworkScanner()
        self.discovered_devices = []

        # Build UI
        self.create_ui()
        
        # Initial setup
        self.refresh_ports()
        self.refresh_monitors()
        self.initialize_led_positions()

        if TRAY_AVAILABLE:
            self._setup_tray()

    def create_ui(self):
        """Build the primary shell layout."""

        # Main container with two columns:
        # Column 0: Sidebar (fixed width), Column 1: Content (expands)
        self.root.grid_columnconfigure(0, weight=0, minsize=220)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = ttk.Frame(self.root, bootstyle="dark")
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        # App title
        title_frame = ttk.Frame(self.sidebar, bootstyle="dark")
        title_frame.pack(fill="x", pady=18, padx=14)

        lbl_title = ttk.Label(
            title_frame,
            text="Ambilight",
            font=("Segoe UI", 18, "bold"),
            bootstyle="inverse-dark"
        )
        lbl_title.pack(anchor="center")

        lbl_subtitle = ttk.Label(
            title_frame,
            text="Controller",
            font=("Segoe UI", 10),
            bootstyle="inverse-dark"
        )
        lbl_subtitle.pack(anchor="center")

        ttk.Separator(self.sidebar, bootstyle="secondary").pack(fill="x", padx=14, pady=8)

        # Navigation
        self.nav_var = tk.StringVar(value="Dashboard")
        self.create_nav_button("Dashboard", "Dashboard")
        self.create_nav_button("Connection", "Connection")
        self.create_nav_button("Calibration", "Calibration")
        self.create_nav_button("Settings", "Settings")

        # Spacer
        ttk.Frame(self.sidebar, bootstyle="dark").pack(fill="both", expand=True)

        # Sidebar transport status
        self.sidebar_status = ttk.Label(
            self.sidebar,
            text="Disconnected",
            bootstyle="danger-inverse",
            font=("Segoe UI", 9, "bold"),
            padding=6
        )
        self.sidebar_status.pack(side="bottom", fill="x", padx=14, pady=16)

        # Content
        self.content_area = ttk.Frame(self.root, padding=(18, 14))
        self.content_area.grid(row=0, column=1, sticky="nsew")

        # Views
        self.views = {}
        self.views["Dashboard"] = self.create_dashboard_view()
        self.views["Connection"] = self.create_connection_view()
        self.views["Calibration"] = self.create_calibration_view()
        self.views["Settings"] = self.create_settings_view()

        # Show initial view
        self.show_view("Dashboard")

    def create_nav_button(self, text, view_name):
        """Create a navigation button in the sidebar."""
        btn = ttk.Radiobutton(
            self.sidebar,
            text=text,
            variable=self.nav_var,
            value=view_name,
            command=lambda: self.show_view(view_name),
            bootstyle="toolbutton-dark",
            width=18
        )
        btn.pack(fill="x", pady=4, padx=14)

    def show_view(self, view_name):
        """Switch the visible view in the content area."""
        # Hide all views
        for view in self.views.values():
            view.pack_forget()
        
        # Show selected view
        if view_name in self.views:
            self.views[view_name].pack(fill="both", expand=True)

    # ==========================================
    #               VIEWS
    # ==========================================

    def create_dashboard_view(self):
        """Create the main dashboard view."""
        frame = ttk.Frame(self.content_area)
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=2)
        frame.grid_rowconfigure(2, weight=1)

        ttk.Label(frame, text="Dashboard", font=("Segoe UI", 24)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )

        left_col = ttk.Frame(frame)
        left_col.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        left_col.grid_columnconfigure(0, weight=1)
        left_col.grid_rowconfigure(2, weight=1)

        right_col = ttk.Frame(frame)
        right_col.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=(10, 0))
        right_col.grid_columnconfigure(0, weight=1)

        control_frame = ttk.Labelframe(
            left_col, text="Session Control", padding=14, bootstyle="primary"
        )
        control_frame.grid(row=0, column=0, sticky="ew")
        control_frame.grid_columnconfigure(0, weight=1)
        control_frame.grid_columnconfigure(1, weight=1)

        self.start_btn = ttk.Button(
            control_frame,
            text="Start",
            command=self.start_ambilight,
            bootstyle="success",
            width=16,
        )
        self.start_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.stop_btn = ttk.Button(
            control_frame,
            text="Stop",
            command=self.stop_ambilight,
            state="disabled",
            bootstyle="danger",
            width=16,
        )
        self.stop_btn.grid(row=0, column=1, sticky="w")

        ttk.Button(
            control_frame,
            text="Clear LEDs",
            command=self.force_clear_leds,
            bootstyle="secondary-outline",
            width=16,
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

        self.dashboard_status = ttk.Label(
            control_frame,
            text="System Ready",
            font=("Segoe UI", 11, "bold"),
            anchor="e",
        )
        self.dashboard_status.grid(row=1, column=1, sticky="e", pady=(10, 0))

        runtime_frame = ttk.Labelframe(
            left_col, text="Runtime Status", padding=12, bootstyle="secondary"
        )
        runtime_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        runtime_frame.grid_columnconfigure(0, weight=1)

        self.runtime_status_label = ttk.Label(
            runtime_frame,
            text="Idle | FPS:0 Sent:0/s Drop:0/s TX p95:0.0ms Cap:-",
            font=("Consolas", 10),
        )
        self.runtime_status_label.grid(row=0, column=0, sticky="w")

        live_frame = ttk.Labelframe(
            left_col, text="Live Adjustments", padding=12, bootstyle="info"
        )
        live_frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        live_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(live_frame, text="Brightness").grid(row=0, column=0, sticky="w")
        self.brightness_scale = ttk.Scale(
            live_frame,
            from_=0,
            to=100,
            command=self._on_brightness_scale,
            bootstyle="warning",
        )
        self.brightness_scale.grid(row=0, column=1, sticky="ew", padx=10)
        self.lbl_brightness = ttk.Label(live_frame, text="100%", width=6, anchor="e")
        self.lbl_brightness.grid(row=0, column=2, sticky="e")
        self.brightness_scale.set(100)

        ttk.Label(live_frame, text="Smoothing").grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        self.smooth_scale = ttk.Scale(
            live_frame,
            from_=0,
            to=90,
            command=self._on_smoothing_scale,
            bootstyle="primary",
        )
        self.smooth_scale.grid(row=1, column=1, sticky="ew", padx=10, pady=(12, 0))
        self.lbl_smoothing = ttk.Label(live_frame, text="0%", width=6, anchor="e")
        self.lbl_smoothing.grid(row=1, column=2, sticky="e", pady=(12, 0))
        self.smooth_scale.set(0)

        mode_frame = ttk.Labelframe(
            right_col, text="Output Mode", padding=12, bootstyle="secondary"
        )
        mode_frame.grid(row=0, column=0, sticky="ew")

        for row, mode in enumerate(["Screen Capture", "Static Color", "Effect"]):
            ttk.Radiobutton(
                mode_frame,
                text=mode,
                variable=self.output_mode,
                value=mode,
                command=self._on_output_mode_change,
            ).grid(row=row, column=0, sticky="w", pady=4)

        details_frame = ttk.Frame(right_col)
        details_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        details_frame.grid_columnconfigure(0, weight=1)

        self.static_ctrl_frame = ttk.Labelframe(
            details_frame, text="Static Color Settings", padding=12
        )
        self.static_ctrl_frame.grid(row=0, column=0, sticky="ew")
        self.static_ctrl_frame.grid_columnconfigure(4, weight=1)

        self.static_color_preview = tk.Canvas(
            self.static_ctrl_frame,
            width=24,
            height=24,
            bg=self._rgb_to_hex(self.static_color),
            highlightthickness=1,
        )
        self.static_color_preview.grid(row=0, column=0, padx=(0, 8), sticky="w")

        ttk.Button(
            self.static_ctrl_frame, text="Pick Color", command=self._pick_color
        ).grid(row=0, column=1, padx=(0, 10), sticky="w")

        ttk.Label(self.static_ctrl_frame, text="Preset").grid(
            row=0, column=2, padx=(0, 6), sticky="w"
        )
        self.preset_combo = ttk.Combobox(
            self.static_ctrl_frame,
            textvariable=self.selected_preset,
            values=list(self.presets.keys()),
            state="readonly",
            width=14,
        )
        self.preset_combo.grid(row=0, column=3, padx=(0, 8), sticky="w")
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)

        ttk.Button(
            self.static_ctrl_frame,
            text="Save Preset",
            command=self._save_preset,
            bootstyle="outline",
        ).grid(row=0, column=4, sticky="e")

        self.effect_ctrl_frame = ttk.Labelframe(
            details_frame, text="Effect Settings", padding=12
        )
        self.effect_ctrl_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.effect_ctrl_frame.grid_columnconfigure(3, weight=1)

        ttk.Label(self.effect_ctrl_frame, text="Effect").grid(row=0, column=0, sticky="w")
        self.effect_combo = ttk.Combobox(
            self.effect_ctrl_frame,
            textvariable=self.current_effect,
            values=list(effects.EFFECTS.keys()),
            state="readonly",
            width=16,
        )
        self.effect_combo.grid(row=0, column=1, padx=(8, 14), sticky="w")

        ttk.Label(self.effect_ctrl_frame, text="Speed").grid(row=0, column=2, sticky="w")
        ttk.Scale(
            self.effect_ctrl_frame,
            variable=self.effect_speed,
            from_=0.1,
            to=3.0,
        ).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        self._on_output_mode_change()
        return frame

        # Big Start/Stop Controls
        control_frame = ttk.Labelframe(frame, text="Master Control", padding=15, bootstyle="primary")
        control_frame.pack(fill="x", pady=10)

        # Start/Stop Buttons
        btn_box = ttk.Frame(control_frame)
        btn_box.pack(side="left", padx=20)
        
        self.start_btn = ttk.Button(
            btn_box, 
            text="▶ START", 
            command=self.start_ambilight, 
            bootstyle="success", 
            width=10
        )
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = ttk.Button(
            btn_box, 
            text="⏹ STOP", 
            command=self.stop_ambilight, 
            state="disabled", 
            bootstyle="danger", 
            width=10
        )
        self.stop_btn.pack(side="left", padx=5)

        # Clear Button
        ttk.Button(
            btn_box, 
            text="Clear LEDs", 
            command=self.force_clear_leds, 
            bootstyle="secondary-outline"
        ).pack(side="left", padx=15)

        # Current State Info
        self.dashboard_status = ttk.Label(control_frame, text="System Ready", font=("Segoe UI", 12))
        self.dashboard_status.pack(side="right", padx=20)

        # Controls Section (Brightness/Smoothing/FPS)
        meters_frame = ttk.Frame(frame)
        meters_frame.pack(fill="x", pady=20)
        
        # Left: Live Controls
        live_frame = ttk.Labelframe(meters_frame, text="Live Adjustments", padding=10, bootstyle="info")
        live_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # Brightness
        b_row = ttk.Frame(live_frame)
        b_row.pack(fill="x", pady=10)
        ttk.Label(b_row, text="Brightness", width=10).pack(side="left")
        self.brightness_scale = ttk.Scale(b_row, from_=0, to=100, command=self._on_brightness_scale, bootstyle="warning")
        self.brightness_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.lbl_brightness = ttk.Label(b_row, text="100%")
        self.lbl_brightness.pack(side="right")
        self.brightness_scale.set(100)

        # Smoothing
        s_row = ttk.Frame(live_frame)
        s_row.pack(fill="x", pady=10)
        ttk.Label(s_row, text="Smoothing", width=10).pack(side="left")
        self.smooth_scale = ttk.Scale(s_row, from_=0, to=90, command=self._on_smoothing_scale, bootstyle="primary")
        self.smooth_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.lbl_smoothing = ttk.Label(s_row, text="0%")
        self.lbl_smoothing.pack(side="right")
        self.smooth_scale.set(0)

        # Right: Output Mode
        mode_frame = ttk.Labelframe(meters_frame, text="Output Mode", padding=10, bootstyle="secondary")
        mode_frame.pack(side="right", fill="both", expand=True, padx=(10, 0))

        modes = ["Screen Capture", "Static Color", "Effect"]
        for mode in modes:
            ttk.Radiobutton(
                mode_frame, 
                text=mode, 
                variable=self.output_mode, 
                value=mode,
                command=self._on_output_mode_change
            ).pack(anchor="w", pady=5)
            
        # Quick Effect/Color Controls (Visible only when relevant modes selected? 
        # For simplicity, put them below)
        
        # Effect/Color Details Frame
        details_frame = ttk.Frame(frame)
        details_frame.pack(fill="x", pady=10)
        
        # Static Color Controls
        self.static_ctrl_frame = ttk.Labelframe(details_frame, text="Static Color Settings", padding=10)
        self.static_ctrl_frame.pack(fill="x", pady=5)
        
        c_row = ttk.Frame(self.static_ctrl_frame)
        c_row.pack(fill="x")
        
        self.static_color_preview = tk.Canvas(
            c_row, width=30, height=30, bg=self._rgb_to_hex(self.static_color), highlightthickness=1
        )
        self.static_color_preview.pack(side="left", padx=5)
        
        ttk.Button(c_row, text="Pick Color", command=self._pick_color).pack(side="left", padx=5)
        
        ttk.Label(c_row, text="Preset:").pack(side="left", padx=(20, 5))
        self.preset_combo = ttk.Combobox(
            c_row, textvariable=self.selected_preset, values=list(self.presets.keys()), state="readonly", width=12
        )
        self.preset_combo.pack(side="left", padx=5)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        
        ttk.Button(c_row, text="Save Preset", command=self._save_preset, bootstyle="outline").pack(side="left", padx=5)

        # Effect Controls
        self.effect_ctrl_frame = ttk.Labelframe(details_frame, text="Effect Settings", padding=10)
        self.effect_ctrl_frame.pack(fill="x", pady=5)
        
        e_row = ttk.Frame(self.effect_ctrl_frame)
        e_row.pack(fill="x")
        
        ttk.Label(e_row, text="Effect:").pack(side="left")
        effect_combo = ttk.Combobox(
            e_row, textvariable=self.current_effect, values=list(effects.EFFECTS.keys()), state="readonly"
        )
        effect_combo.pack(side="left", padx=5)
        
        ttk.Label(e_row, text="Speed:").pack(side="left", padx=(20, 5))
        ttk.Scale(e_row, variable=self.effect_speed, from_=0.1, to=3.0).pack(side="left", fill="x", expand=True)

        return frame

    def create_connection_view(self):
        """Create the connection settings view."""
        frame = ttk.Frame(self.content_area)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        ttk.Label(frame, text="Connection", font=("Segoe UI", 24)).grid(
            row=0, column=0, sticky="w", pady=(0, 14)
        )

        method_frame = ttk.Labelframe(
            frame, text="Connection Method", padding=12, bootstyle="secondary"
        )
        method_frame.grid(row=1, column=0, sticky="ew")

        if SERIAL_AVAILABLE:
            ttk.Radiobutton(
                method_frame,
                text="USB Serial",
                variable=self.connection_mode,
                value="USB",
                command=self._update_conn_ui,
            ).grid(row=0, column=0, sticky="w", padx=(0, 18))

        if WEBSOCKET_AVAILABLE:
            ttk.Radiobutton(
                method_frame,
                text="Wi-Fi (WebSocket)",
                variable=self.connection_mode,
                value="WebSocket",
                command=self._update_conn_ui,
            ).grid(row=0, column=1, sticky="w")

        config_grid = ttk.Frame(frame)
        config_grid.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        config_grid.grid_columnconfigure(0, weight=1)
        config_grid.grid_columnconfigure(1, weight=1)

        self.usb_panel = ttk.Labelframe(
            config_grid, text="USB Configuration", padding=14, bootstyle="info"
        )
        self.usb_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.usb_panel.grid_columnconfigure(1, weight=1)

        ttk.Label(self.usb_panel, text="Port").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(self.usb_panel, width=22, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(
            self.usb_panel, text="Refresh", command=self.refresh_ports, bootstyle="outline"
        ).grid(row=0, column=2, sticky="e")

        self.ws_panel = ttk.Labelframe(
            config_grid, text="Wi-Fi Configuration", padding=14, bootstyle="info"
        )
        self.ws_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.ws_panel.grid_columnconfigure(1, weight=1)

        ttk.Label(self.ws_panel, text="Device IP").grid(row=0, column=0, sticky="w")
        self.ip_var = tk.StringVar(value=config.DEFAULT_IP)
        self.ip_combo = ttk.Combobox(self.ws_panel, textvariable=self.ip_var, width=25)
        self.ip_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))

        self.scan_btn = ttk.Button(
            self.ws_panel, text="Scan Network", command=self.scan_network, bootstyle="info"
        )
        self.scan_btn.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.scan_status_label = ttk.Label(self.ws_panel, text="", foreground="gray")
        self.scan_status_label.grid(row=1, column=1, sticky="w", pady=(10, 0))

        action_frame = ttk.Frame(frame, padding=(0, 14, 0, 0))
        action_frame.grid(row=3, column=0, sticky="ew")
        action_frame.grid_columnconfigure(0, weight=1)
        action_frame.grid_columnconfigure(1, weight=0)
        action_frame.grid_columnconfigure(2, weight=0)

        self.connection_hint_label = ttk.Label(
            action_frame, text="Choose mode and target, then connect."
        )
        self.connection_hint_label.grid(row=0, column=0, sticky="w")

        self.connect_btn = ttk.Button(
            action_frame,
            text="Connect",
            command=self.connect_device,
            bootstyle="success",
            width=16,
        )
        self.connect_btn.grid(row=0, column=1, padx=(8, 8))

        self.disconnect_btn = ttk.Button(
            action_frame,
            text="Disconnect",
            command=self.disconnect_device,
            bootstyle="danger",
            width=16,
        )
        self.disconnect_btn.grid(row=0, column=2)

        self._update_conn_ui()
        return frame

    def _update_conn_ui(self):
        """Show/Hide panels based on connection mode."""
        mode = self.connection_mode.get()
        if hasattr(self, "usb_panel"):
            self.usb_panel.grid_remove()
        if hasattr(self, "ws_panel"):
            self.ws_panel.grid_remove()

        if mode == "USB" and hasattr(self, "usb_panel"):
            self.usb_panel.grid()
            if hasattr(self, "connection_hint_label"):
                self.connection_hint_label.config(
                    text="Select USB serial port, then connect."
                )
        elif mode == "WebSocket" and hasattr(self, "ws_panel"):
            self.ws_panel.grid()
            if hasattr(self, "connection_hint_label"):
                self.connection_hint_label.config(
                    text="Enter ESP32 IP or scan network, then connect."
                )

    def create_calibration_view(self):
        """Create the calibration view."""
        frame = ttk.Frame(self.content_area)
        
        ttk.Label(frame, text="LED Calibration", font=("Segoe UI", 24)).pack(anchor="w", pady=(0, 20))

        # Toolbar
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=5)
        
        ttk.Button(toolbar, text="Start Calibration", command=self.start_calibration, bootstyle="warning").pack(side="left", padx=5)
        ttk.Button(toolbar, text="Test Pattern", command=self.test_pattern).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Load Config", command=self.load_config).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Save Config", command=self.save_config).pack(side="left", padx=5)

        # LED Count Override
        count_frame = ttk.Frame(toolbar)
        count_frame.pack(side="right", padx=5)
        
        ttk.Label(count_frame, text="Override Count:").pack(side="left")
        self.led_count_var = tk.StringVar(value="60")
        self.led_count_entry = ttk.Entry(count_frame, width=5, textvariable=self.led_count_var)
        self.led_count_entry.pack(side="left", padx=5)
        ttk.Button(count_frame, text="Apply", command=self.apply_led_count, bootstyle="outline", width=6).pack(side="left")

        # Instruction Area
        self.info_label = ttk.Label(
            frame, 
            text="Connect to a device to see status.", 
            bootstyle="info",
            font=("Segoe UI", 10)
        )
        self.info_label.pack(fill="x", pady=10)

        # Canvas
        self.canvas = tk.Canvas(frame, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=10)
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<Configure>", lambda e: self.draw_led_map())

        return frame

    def create_settings_view(self):
        """Create the settings view."""
        frame = ttk.Frame(self.content_area)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        ttk.Label(frame, text="Settings", font=("Segoe UI", 24)).grid(
            row=0, column=0, sticky="w", pady=(0, 14)
        )

        cap_frame = ttk.Labelframe(
            frame, text="Capture Settings", padding=14, bootstyle="secondary"
        )
        cap_frame.grid(row=1, column=0, sticky="ew")
        cap_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(cap_frame, text="Target Monitor").grid(
            row=0, column=0, sticky="w", pady=5
        )
        self.monitor_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.selected_monitor,
            state="readonly",
            width=40,
        )
        self.monitor_combo.grid(row=0, column=1, padx=(10, 10), sticky="ew")
        ttk.Button(
            cap_frame, text="Refresh", command=self.refresh_monitors, bootstyle="outline"
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(cap_frame, text="Algorithm").grid(row=1, column=0, sticky="w", pady=5)
        self.algo_combo = ttk.Combobox(
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
            width=25,
        )
        self.algo_combo.grid(row=1, column=1, padx=(10, 10), sticky="w")

        ttk.Label(cap_frame, text="Target FPS").grid(row=2, column=0, sticky="w", pady=5)
        self.fps_var = tk.StringVar(value="60")
        self.fps_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.fps_var,
            values=["15", "20", "30", "45", "60", "90", "120"],
            width=10,
            state="readonly",
        )
        self.fps_combo.grid(row=2, column=1, padx=(10, 10), sticky="w")

        ttk.Label(cap_frame, text="Capture Backend").grid(
            row=3, column=0, sticky="w", pady=5
        )
        self.backend_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.capture_backend_pref,
            values=["Auto", "DXCam", "Pillow"],
            width=15,
            state="readonly",
        )
        self.backend_combo.grid(row=3, column=1, padx=(10, 10), sticky="w")

        reg_frame = ttk.Labelframe(
            frame,
            text="Advanced Capture Region",
            padding=14,
            bootstyle="warning",
        )
        reg_frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))

        ttk.Checkbutton(
            reg_frame,
            text="Enable Custom Region",
            variable=self.use_custom_region,
            command=self.toggle_region_inputs,
        ).grid(row=0, column=0, sticky="w")

        self.reg_input_frame = ttk.Frame(reg_frame)
        self.reg_input_frame.grid(row=1, column=0, sticky="w", pady=(10, 0))

        validate_cmd = (self.root.register(self.validate_percent), "%P")
        for label, var in [
            ("X %", self.region_x),
            ("Y %", self.region_y),
            ("Width %", self.region_w),
            ("Height %", self.region_h),
        ]:
            sub = ttk.Frame(self.reg_input_frame)
            sub.pack(side="left", padx=(0, 12))
            ttk.Label(sub, text=label).pack(anchor="w")
            e = ttk.Entry(
                sub,
                width=6,
                textvariable=var,
                validate="key",
                validatecommand=validate_cmd,
            )
            e.pack(anchor="w")
            setattr(self, f"ent_{label[0].lower()}", e)

        self.toggle_region_inputs()
        return frame

        # Capture Settings
        cap_frame = ttk.Labelframe(frame, text="Capture Settings", padding=15)
        cap_frame.pack(fill="x", pady=10)
        
        # Monitor
        ttk.Label(cap_frame, text="Target Monitor:").grid(row=0, column=0, sticky="w", pady=5)
        self.monitor_combo = ttk.Combobox(cap_frame, textvariable=self.selected_monitor, state="readonly", width=40)
        self.monitor_combo.grid(row=0, column=1, padx=10, sticky="w")
        ttk.Button(cap_frame, text="Refresh", command=self.refresh_monitors, bootstyle="outline").grid(row=0, column=2)

        # Algorithm
        ttk.Label(cap_frame, text="Algorithm:").grid(row=1, column=0, sticky="w", pady=5)
        self.algo_combo = ttk.Combobox(
            cap_frame, 
            textvariable=self.capture_mode,
            values=[
                "Screen Map", "Average Color", "Dominant Color", 
                "Edge Sampling", "Quadrant Colors", "Most Vibrant", 
                "Warm Bias", "Cool Bias"
            ],
            state="readonly",
            width=25
        )
        self.algo_combo.grid(row=1, column=1, padx=10, sticky="w")

        # FPS
        ttk.Label(cap_frame, text="Target FPS:").grid(row=2, column=0, sticky="w", pady=5)
        self.fps_var = tk.StringVar(value="60")
        self.fps_combo = ttk.Combobox(
            cap_frame, 
            textvariable=self.fps_var,
            values=["15", "20", "30", "45", "60", "90", "120"],
            width=10,
            state="readonly"
        )
        self.fps_combo.grid(row=2, column=1, padx=10, sticky="w")

        # Capture Backend
        ttk.Label(cap_frame, text="Capture Backend:").grid(
            row=3, column=0, sticky="w", pady=5
        )
        self.backend_combo = ttk.Combobox(
            cap_frame,
            textvariable=self.capture_backend_pref,
            values=["Auto", "DXCam", "Pillow"],
            width=15,
            state="readonly",
        )
        self.backend_combo.grid(row=3, column=1, padx=10, sticky="w")

        # Custom Region
        reg_frame = ttk.Labelframe(frame, text="Custom Capture Region (Advanced)", padding=15)
        reg_frame.pack(fill="x", pady=10)
        
        tk.Checkbutton(
            reg_frame, 
            text="Enable Custom Region", 
            variable=self.use_custom_region,
            command=self.toggle_region_inputs
        ).pack(anchor="w")
        
        self.reg_input_frame = ttk.Frame(reg_frame)
        self.reg_input_frame.pack(fill="x", pady=10)
        
        validate_cmd = (self.root.register(self.validate_percent), "%P")
        
        for i, (label, var) in enumerate([
            ("X %", self.region_x), ("Y %", self.region_y), 
            ("Width %", self.region_w), ("Height %", self.region_h)
        ]):
            ttk.Label(self.reg_input_frame, text=label).pack(side="left", padx=(0, 5))
            e = ttk.Entry(self.reg_input_frame, width=5, textvariable=var, validate="key", validatecommand=validate_cmd)
            e.pack(side="left", padx=(0, 20))
            # Keep reference to disable later
            setattr(self, f"ent_{label[0].lower()}", e)
            
        self.toggle_region_inputs()

        return frame

    # ==========================================
    #           LOGIC IMPLEMENTATION
    # ==========================================

    def _on_brightness_scale(self, val):
        val = int(float(val))
        self.lbl_brightness.config(text=f"{val}%")
        self.current_brightness = int((val / 100) * 255)
        if self.conn.connected:
            self.conn.send_command({"cmd": "brightness", "value": self.current_brightness})

    def _on_smoothing_scale(self, val):
        val = int(float(val))
        self.lbl_smoothing.config(text=f"{val}%")
        self.current_smoothing = val / 100.0

    def toggle_region_inputs(self):
        state = "normal" if self.use_custom_region.get() else "disabled"
        stack = list(self.reg_input_frame.winfo_children())
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if isinstance(widget, ttk.Entry):
                widget.config(state=state)

    def validate_percent(self, val):
        if val == "": return True
        try:
            v = int(val)
            return 0 <= v <= 100
        except ValueError:
            return False

    def refresh_ports(self):
        if SERIAL_AVAILABLE:
            ports = [port.device for port in serial.tools.list_ports.comports()]
            self.port_combo["values"] = ports
            if ports: self.port_combo.set(ports[0])

    def refresh_monitors(self):
        if not SCREENINFO_AVAILABLE:
            self.monitor_combo["values"] = ["Primary (default)"]
            self.selected_monitor.set("Primary (default)")
            return

        try:
            self.monitors = list(get_monitors())
            names = []
            for i, m in enumerate(self.monitors):
                n = f"Monitor {i+1}: {m.width}x{m.height}"
                if m.is_primary: n += " [Primary]"
                names.append(n)
            
            self.monitor_combo["values"] = names
            if names:
                # Default to primary
                for i, m in enumerate(self.monitors):
                    if m.is_primary:
                        self.selected_monitor.set(names[i])
                        break
        except Exception as e:
            print(f"Monitor error: {e}")
            self.monitor_combo["values"] = ["Primary"]

    def connect_device(self):
        mode = self.connection_mode.get()

        self.sidebar_status.config(text="Connecting...", bootstyle="warning-inverse")
        self.dashboard_status.config(text="Connecting...", foreground="orange")
        if hasattr(self, "runtime_status_label"):
            self.runtime_status_label.config(
                text=f"Connecting via {mode}..."
            )
        self.root.update()

        success = False
        if mode == "USB":
            port = self.port_combo.get()
            if port and self.conn.connect_usb(port):
                success = True
        elif mode == "WebSocket":
            ip = self.ip_var.get()
            # Clean IP string
            if "(" in ip: ip = ip.split("(")[1].split(")")[0]
            if ip and self.conn.connect_websocket(ip):
                success = True

        if success:
            self.sidebar_status.config(text="Connected", bootstyle="success-inverse")
            self.dashboard_status.config(text="Connected", foreground="green")
            if hasattr(self, "connection_hint_label"):
                self.connection_hint_label.config(text="Connected.")
        else:
            self.sidebar_status.config(text="Failed", bootstyle="danger-inverse")
            self.dashboard_status.config(text="Connection Failed", foreground="red")
            if hasattr(self, "connection_hint_label"):
                self.connection_hint_label.config(
                    text="Connection failed. Verify target and try again."
                )
            messagebox.showerror("Error", "Could not connect to device")

    def disconnect_device(self):
        self.conn.disconnect()
        self.sidebar_status.config(text="Disconnected", bootstyle="danger-inverse")
        self.dashboard_status.config(text="Disconnected", foreground="red")
        if hasattr(self, "runtime_status_label"):
            self.runtime_status_label.config(text="Idle | Disconnected")
        if hasattr(self, "connection_hint_label"):
            self.connection_hint_label.config(text="Disconnected.")

    def _on_connected(self, mode, details):
        self.num_leds = self.conn.led_count
        self.initialize_led_positions()
        
        def ui_update():
            self.sidebar_status.config(text=f"Connected ({mode})", bootstyle="success-inverse")
            self.dashboard_status.config(text=f"Online - {self.num_leds} LEDs", foreground="green")
            self.info_label.config(text=f"Connected! Found {self.num_leds} LEDs.")
            if hasattr(self, "runtime_status_label"):
                self.runtime_status_label.config(
                    text=f"Connected via {mode.upper()} | LEDs:{self.num_leds}"
                )
            # Sync LED count field
            self.led_count_var.set(str(self.num_leds))
            
        self.root.after(0, ui_update)

    def _on_disconnected(self):
        def ui_update():
            if self.conn.reconnecting and self.connection_mode.get() == "WebSocket":
                self.sidebar_status.config(
                    text="Reconnecting...", bootstyle="warning-inverse"
                )
                self.dashboard_status.config(text="Reconnecting WS...", foreground="orange")
                if hasattr(self, "runtime_status_label"):
                    self.runtime_status_label.config(text="Reconnecting WebSocket...")
            else:
                self.sidebar_status.config(text="Disconnected", bootstyle="danger-inverse")
                self.dashboard_status.config(text="Disconnected", foreground="red")
                if hasattr(self, "runtime_status_label"):
                    self.runtime_status_label.config(text="Idle | Disconnected")

        self.root.after(0, ui_update)

    def _on_message(self, data):
        if data.get("type") == "info":
            new_count = data.get("ledCount", 60)
            if new_count != self.num_leds:
                self.num_leds = new_count
                self.initialize_led_positions()
                self.root.after(0, lambda: self.led_count_var.set(str(new_count)))

    def _on_error(self, err):
        print(f"Error: {err}")
        self.root.after(
            0,
            lambda: self.dashboard_status.config(
                text=f"Transport error: {err[:80]}", foreground="orange"
            ),
        )
        if hasattr(self, "runtime_status_label"):
            self.root.after(
                0,
                lambda: self.runtime_status_label.config(
                    text=f"Transport error: {err[:80]}"
                ),
            )

    # ===== Calibration Logic =====

    def apply_led_count(self):
        try:
            cnt = int(self.led_count_var.get())
            if 1 <= cnt <= 300:
                self.num_leds = cnt
                self.initialize_led_positions()
                messagebox.showinfo("Success", f"LED count set to {cnt}")
            else:
                messagebox.showerror("Error", "Invalid count (1-300)")
        except ValueError:
            pass

    def initialize_led_positions(self):
        if len(self.led_positions) > self.num_leds:
             self.led_positions = self.led_positions[:self.num_leds]
        
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
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1: return
        
        margin = 30
        self.canvas.create_rectangle(margin, margin, w-margin, h-margin, outline="#444", width=2, dash=(4,4))
        
        for i, led in enumerate(self.led_positions):
            x = margin + led["x"] * (w - 2 * margin)
            y = margin + led["y"] * (h - 2 * margin)
            
            color = "#00ccff"
            size = 4
            if self.calibration_mode:
                if i == self.current_led_index:
                    color = "yellow"
                    size = 6
                elif i < self.current_led_index:
                     color = "#00ff00"
            
            self.canvas.create_oval(x-size, y-size, x+size, y+size, fill=color, outline="")
            
            if self.num_leds <= 20 or i % 5 == 0 or (self.calibration_mode and i == self.current_led_index):
                self.canvas.create_text(x, y-10, text=str(i), fill="white", font=("Arial", 8))

    def start_calibration(self):
        if not self.conn.connected:
            messagebox.showwarning("Warning", "Connect first")
            return
        
        self.calibration_mode = True
        self.current_led_index = 0
        self.conn.send_command({"cmd": "calibrate_start"})
        self.conn.send_command({"cmd": "highlight", "led": 0})
        self.info_label.config(text="Click on canvas to map LED 0 (blinking white)", bootstyle="warning")
        self.draw_led_map()

    def canvas_click(self, event):
        if not self.calibration_mode: return
        
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        margin = 30
        x = max(0, min(1, (event.x - margin) / (w - 2 * margin)))
        y = max(0, min(1, (event.y - margin) / (h - 2 * margin)))
        
        self.led_positions[self.current_led_index] = {"x": x, "y": y}
        self.current_led_index += 1
        
        if self.current_led_index < self.num_leds:
            self.conn.send_command({"cmd": "highlight", "led": self.current_led_index})
            self.info_label.config(text=f"Click to map LED {self.current_led_index}")
            self.draw_led_map()
        else:
            self.finish_calibration()

    def finish_calibration(self):
        self.calibration_mode = False
        mapping = [{"x": int(l["x"]*255), "y": int(l["y"]*255)} for l in self.led_positions]
        self.conn.send_command({"cmd": "save_map", "mapping": mapping})
        self.conn.send_command({"cmd": "calibrate_end"})
        self.info_label.config(text="Calibration saved!", bootstyle="success")
        self.draw_led_map()

     # ===== Runtime Logic (Start/Stop/Loop) =====

    def start_ambilight(self):
        if not self.conn.connected:
            messagebox.showwarning("Warning", "Connect first!")
            return
        if self.is_running:
            return
        
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.dashboard_status.config(text="RUNNING", foreground="green")
        if hasattr(self, "runtime_status_label"):
            self.runtime_status_label.config(text="Starting capture loop...")
        try:
            self._adaptive_fps = max(15, int(self.fps_var.get() or "60"))
        except ValueError:
            self._adaptive_fps = 60
        self._start_capture_backend()
        
        self._capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self._capture_thread.start()

    def stop_ambilight(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.dashboard_status.config(text="STOPPED", foreground="red")
        if hasattr(self, "runtime_status_label"):
            self.runtime_status_label.config(text="Stopped")
        self._stop_capture_backend()
        if self.conn.connected:
            self.conn.send_command({"cmd": "clear"})

    def capture_loop(self):
        """Main capture loop - runs in background thread."""
        next_tick = time.perf_counter()
        loop_sent = 0
        loop_dropped = 0
        last_status_push = time.time()

        while self.is_running:
            try:
                requested_fps = int(self.fps_var.get())
            except ValueError:
                requested_fps = 60

            requested_fps = max(15, min(120, requested_fps))
            effective_fps = self._get_effective_fps(requested_fps)
            interval = 1.0 / max(1, effective_fps)
            send_ok = False

            try:
                output_mode = self.output_mode.get()

                if output_mode == "Static Color":
                    send_ok = self._apply_static_color()
                elif output_mode == "Effect":
                    send_ok = self._run_effect_step()
                else:
                    bbox = self.get_capture_bbox()
                    frame = self._grab_frame(bbox)
                    if frame is None:
                        send_ok = False
                        time.sleep(0.005)
                        continue

                    sh, sw = frame.shape[:2]
                    target_w = 160
                    if effective_fps >= 90:
                        target_w = 128
                    pixels = self._fast_resize_frame(frame, target_w)
                    brightness = self.current_brightness
                    led_colors = self.process_image(pixels, brightness)
                    smooth_factor = self.current_smoothing

                    with self._lock:
                        if (
                            self.prev_colors is not None
                            and len(self.prev_colors) == len(led_colors)
                        ):
                            smoothed = bytearray(len(led_colors))
                            for i in range(len(led_colors)):
                                smoothed[i] = int(
                                    self.prev_colors[i] * smooth_factor
                                    + led_colors[i] * (1 - smooth_factor)
                                )
                            led_colors = smoothed

                        self.prev_colors = bytearray(led_colors)

                    send_ok = self.conn.send_colors(bytes(led_colors))

            except Exception as e:
                print(f"Capture loop error: {e}")
                send_ok = False
                time.sleep(0.2)

            if send_ok:
                loop_sent += 1
            else:
                loop_dropped += 1

            now = time.time()
            if now - last_status_push >= 1.0:
                self._push_runtime_status(loop_sent, loop_dropped, effective_fps)
                loop_sent = 0
                loop_dropped = 0
                last_status_push = now

            next_tick += interval
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.perf_counter()
                
    def get_capture_bbox(self):
        """Calculate capture bounding box based on settings."""
        # 1. Check Custom Region
        if self.use_custom_region.get():
             try:
                # We need a base reference size. 
                # If a monitor is selected, use that.
                # If not (or issue obtaining), use generic full screen concept.
                
                mw, mh = 1920, 1080 # Fallback
                mx, my = 0, 0
                
                # Try getting selected monitor bounds
                idx = self.monitor_combo.current()
                if SCREENINFO_AVAILABLE and 0 <= idx < len(self.monitors):
                    m = self.monitors[idx]
                    mx, my, mw, mh = m.x, m.y, m.width, m.height
                else:
                    # Fallback to caching primary screen size if available
                     if self._screen_size is None:
                        self._screen_size = (
                            self.root.winfo_screenwidth(),
                            self.root.winfo_screenheight(),
                        )
                     if self._screen_size:
                        mw, mh = self._screen_size
                
                # Calculate percentages
                try:
                    rx = int(int(self.region_x.get()) / 100 * mw)
                    ry = int(int(self.region_y.get()) / 100 * mh)
                    rw = int(int(self.region_w.get()) / 100 * mw)
                    rh = int(int(self.region_h.get()) / 100 * mh)
                    
                    return (mx + rx, my + ry, mx + rx + rw, my + ry + rh)
                except ValueError:
                    pass # Invalid inputs, fall through to full monitor
                    
             except Exception as e:
                print(f"Region calc error: {e}")

        # 2. Monitor Selection
        idx = self.monitor_combo.current()
        if SCREENINFO_AVAILABLE and 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            return (m.x, m.y, m.x+m.width, m.y+m.height)
        
        return None

    def _start_capture_backend(self):
        self._stop_capture_backend()
        preferred = self._resolve_capture_backend_preference()

        if preferred == "pil":
            self.capture_backend = "pil"
            return

        self._dxcam_device_idx, self._dxcam_output_idx = self._resolve_dxcam_target()

        try:
            self._dxcam = dxcam.create(
                device_idx=self._dxcam_device_idx,
                output_idx=self._dxcam_output_idx,
                output_color="RGB",
            )
            # Start internal capture thread once; read newest frame via get_latest_frame().
            self._dxcam.start(target_fps=120, video_mode=True)
            self._dxcam_started = True
            self.capture_backend = "dxcam"
        except Exception as e:
            # Retry with primary output before falling back.
            try:
                self._dxcam_device_idx = 0
                self._dxcam_output_idx = 0
                self._dxcam = dxcam.create(
                    device_idx=0, output_idx=0, output_color="RGB"
                )
                self._dxcam.start(target_fps=120, video_mode=True)
                self._dxcam_started = True
                self.capture_backend = "dxcam"
                print(f"DXCam init recovered on output 0 (initial error: {e})")
            except Exception as e2:
                print(f"DXCam init failed, falling back to Pillow: {e2}")
                self._dxcam = None
                self._dxcam_started = False
                self.capture_backend = "pil"

    def _resolve_capture_backend_preference(self):
        pref = self.capture_backend_pref.get().strip().lower()
        if pref == "pillow":
            return "pil"
        if pref == "dxcam":
            return "dxcam" if DXCAM_AVAILABLE else "pil"
        return "dxcam" if DXCAM_AVAILABLE else "pil"

    def _resolve_dxcam_target(self):
        """Map selected monitor to dxcam (device_idx, output_idx)."""
        target_monitor = None
        try:
            sel = self.monitor_combo.current()
            if SCREENINFO_AVAILABLE and self.monitors and 0 <= sel < len(self.monitors):
                target_monitor = self.monitors[sel]
        except Exception:
            target_monitor = None

        try:
            info_text = dxcam.output_info()
        except Exception:
            return 0, 0

        entries = []
        pattern = re.compile(
            r"Device\[(\d+)\]\s+Output\[(\d+)\]:\s+Res:\((\d+),\s*(\d+)\).*Primary:(True|False)"
        )
        for line in str(info_text).splitlines():
            m = pattern.search(line)
            if not m:
                continue
            entries.append(
                {
                    "device_idx": int(m.group(1)),
                    "output_idx": int(m.group(2)),
                    "width": int(m.group(3)),
                    "height": int(m.group(4)),
                    "primary": m.group(5) == "True",
                }
            )

        if not entries:
            return 0, 0

        if target_monitor is not None:
            same_res = [
                e
                for e in entries
                if e["width"] == target_monitor.width and e["height"] == target_monitor.height
            ]
            candidates = same_res if same_res else entries
            # Prefer matching primary/non-primary state with selected screen.
            pref = [e for e in candidates if e["primary"] == bool(target_monitor.is_primary)]
            chosen = pref[0] if pref else candidates[0]
            return chosen["device_idx"], chosen["output_idx"]

        primary = [e for e in entries if e["primary"]]
        chosen = primary[0] if primary else entries[0]
        return chosen["device_idx"], chosen["output_idx"]

    def _stop_capture_backend(self):
        if self._dxcam is not None:
            try:
                self._dxcam.stop()
            except Exception:
                pass
            self._dxcam_started = False
            try:
                del self._dxcam
            except Exception:
                pass
            self._dxcam = None

    def _grab_frame(self, bbox):
        if (
            self.capture_backend == "dxcam"
            and self._dxcam is not None
            and self._dxcam_started
        ):
            try:
                frame = self._dxcam.get_latest_frame()
                if frame is not None:
                    if self.use_custom_region.get():
                        frame = self._crop_frame_by_percent(frame)
                    return frame
            except Exception:
                # Fallback to PIL for unsupported monitor geometry / DX failures.
                pass

        if bbox:
            screen = ImageGrab.grab(bbox=bbox, all_screens=True)
        else:
            screen = ImageGrab.grab()
        return np.array(screen)

    def _crop_frame_by_percent(self, frame):
        h, w = frame.shape[:2]
        if w <= 1 or h <= 1:
            return frame
        try:
            x_pct = int(self.region_x.get())
            y_pct = int(self.region_y.get())
            w_pct = int(self.region_w.get())
            h_pct = int(self.region_h.get())
        except ValueError:
            return frame

        x0 = int(max(0, min(100, x_pct)) / 100.0 * w)
        y0 = int(max(0, min(100, y_pct)) / 100.0 * h)
        x1 = x0 + int(max(1, min(100, w_pct)) / 100.0 * w)
        y1 = y0 + int(max(1, min(100, h_pct)) / 100.0 * h)
        x1 = min(w, max(x0 + 1, x1))
        y1 = min(h, max(y0 + 1, y1))
        return frame[y0:y1, x0:x1]

    def _fast_resize_frame(self, frame, target_w):
        h, w = frame.shape[:2]
        if w <= 0 or h <= 0:
            return frame
        if w <= target_w:
            return frame

        target_h = max(1, int(target_w * (h / w)))
        x_idx = np.linspace(0, w - 1, target_w, dtype=np.int32)
        y_idx = np.linspace(0, h - 1, target_h, dtype=np.int32)
        return frame[np.ix_(y_idx, x_idx)]

    def process_image(self, pixels, brightness):
        # Dispatch to image_processor based on algo
        mode = self.capture_mode.get()
        
        if mode == "Average Color":
            return image_processor.process_average_color(pixels, brightness, self.num_leds)
        elif mode == "Dominant Color":
            return image_processor.process_dominant_color(pixels, brightness, self.num_leds)
        elif mode == "Edge Sampling":
            return image_processor.process_edge_sampling(pixels, brightness, self.num_leds)
        elif mode == "Quadrant Colors":
            return image_processor.process_quadrant_colors(pixels, brightness, self.num_leds)
        elif mode == "Most Vibrant":
            return image_processor.process_most_vibrant(pixels, brightness, self.num_leds)
        elif mode == "Warm Bias":
            return image_processor.process_warm_bias(pixels, brightness, self.num_leds)
        elif mode == "Cool Bias":
            return image_processor.process_cool_bias(pixels, brightness, self.num_leds)
        else:
             # Screen Map (default)
             with self._lock:
                 pos = list(self.led_positions)
             return image_processor.process_screen_map(pixels, brightness, self.num_leds, pos)

    def _apply_static_color(self):
        r, g, b = self.static_color
        colors = effects.generate_static_color(self.num_leds, self.current_brightness, r, g, b)
        return self.conn.send_colors(bytes(colors))

    def _run_effect_step(self):
        name = self.current_effect.get()
        if name in effects.EFFECTS:
            func = effects.EFFECTS[name]
            colors = func(self.num_leds, self.current_brightness, self.effect_phase)
            ok = self.conn.send_colors(bytes(colors))
            self.effect_phase += 1.0 * self.effect_speed.get()
            if self.effect_phase > 100:
                self.effect_phase = 0
            return ok
        return False

    def _get_effective_fps(self, requested_fps):
        if not self.low_latency_mode:
            self._adaptive_fps = requested_fps
            return requested_fps

        stats = self.conn.get_stats()
        p95_send_ms = stats.get("send_ms_p95", 0.0)
        budget_ms = 1000.0 / max(1, self._adaptive_fps)

        if p95_send_ms > budget_ms * 0.7:
            self._adaptive_fps = max(20, self._adaptive_fps - 5)
        elif self._adaptive_fps < requested_fps:
            self._adaptive_fps += 1

        self._adaptive_fps = min(self._adaptive_fps, requested_fps)
        return self._adaptive_fps

    def _push_runtime_status(self, loop_sent, loop_dropped, effective_fps):
        stats = self.conn.get_stats()
        text = (
            f"RUNNING | FPS:{effective_fps} Sent:{loop_sent}/s Drop:{loop_dropped}/s "
            f"TX p95:{stats.get('send_ms_p95', 0.0):.1f}ms Cap:{self.capture_backend.upper()}"
        )
        self.root.after(
            0, lambda: self.dashboard_status.config(text=text, foreground="green")
        )
        if hasattr(self, "runtime_status_label"):
            self.root.after(0, lambda: self.runtime_status_label.config(text=text))

    # ===== Misc Helpers =====
    def _rgb_to_hex(self, rgb):
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        
    def _pick_color(self):
        res = colorchooser.askcolor(color=self._rgb_to_hex(self.static_color))
        if res[0]:
            self.static_color = tuple(int(c) for c in res[0])
            self.static_color_preview.config(bg=res[1])
            if self.output_mode.get() == "Static Color":
                self._apply_static_color()

    def _load_presets(self):
        """Load presets from file and merge with defaults."""
        self.presets = dict(config.DEFAULT_PRESETS)
        try:
            presets_path = os.path.join(os.path.dirname(__file__), config.PRESETS_FILE)
            if os.path.exists(presets_path):
                with open(presets_path, "r") as f:
                    user_presets = json.load(f)
                    for name, rgb in user_presets.items():
                        self.presets[name] = tuple(rgb)
        except Exception as e:
            print(f"Error loading presets: {e}")

    def _save_preset(self):
        """Save current color as a preset."""
        from tkinter import simpledialog
        name = simpledialog.askstring("Save Preset", "Enter preset name:", parent=self.root)
        if name and name.strip():
            name = name.strip()
            self.presets[name] = self.static_color
            
            # Save to file
            user_presets = {}
            for n, rgb in self.presets.items():
                if n not in config.DEFAULT_PRESETS:
                    user_presets[n] = list(rgb)
                    
            try:
                presets_path = os.path.join(os.path.dirname(__file__), config.PRESETS_FILE)
                with open(presets_path, "w") as f:
                    json.dump(user_presets, f, indent=2)
                
                # Update UI
                self.preset_combo["values"] = list(self.presets.keys())
                self.selected_preset.set(name)
                messagebox.showinfo("Success", f"Preset '{name}' saved!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save preset: {e}")

    def _on_preset_selected(self, e):
        name = self.selected_preset.get()
        if name and name in self.presets:
            self.static_color = self.presets[name]
            # self.static_color_preview is a Canvas
            if self.static_color_preview:
                self.static_color_preview.config(bg=self._rgb_to_hex(self.static_color))
            if self.output_mode.get() == "Static Color":
                 self._apply_static_color()

    def _on_output_mode_change(self):
        mode = self.output_mode.get()
        if not hasattr(self, "static_ctrl_frame") or not hasattr(
            self, "effect_ctrl_frame"
        ):
            return

        if mode == "Static Color":
            self.static_ctrl_frame.grid()
            self.effect_ctrl_frame.grid_remove()
        elif mode == "Effect":
            self.static_ctrl_frame.grid_remove()
            self.effect_ctrl_frame.grid()
        else:
            self.static_ctrl_frame.grid_remove()
            self.effect_ctrl_frame.grid_remove()
        
    def force_clear_leds(self):
        if self.conn.connected:
            self.conn.send_command({"cmd": "clear"})

    # ===== System Tray Methods =====

    def _setup_tray(self):
        """Setup system tray icon and menu."""
        if not TRAY_AVAILABLE:
            return

        # Create a simple icon
        icon_size = 64
        icon_image = Image.new("RGB", (icon_size, icon_size), color=(50, 50, 50))
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
        self.root.after(0, self.start_ambilight)

    def _tray_stop(self, icon=None, item=None):
        self.root.after(0, self.stop_ambilight)

    def _quit_app(self, icon=None, item=None):
        """Actually quit the application."""
        if self.tray_icon:
            self.tray_icon.stop()
        self.is_running = False
        self._stop_capture_backend()
        self.conn.disconnect()
        self.root.after(0, self.root.destroy)

    def test_pattern(self):
        if self.conn.connected:
            self.conn.send_command({"cmd": "test_pattern"})

    # ===== Configuration Persistence =====

    def save_config(self):
        """Save configuration to file."""
        config_data = {
            "num_leds": self.num_leds,
            "led_positions": self.led_positions,
            "connection_mode": self.connection_mode.get(),
            "com_port": self.port_combo.get() if hasattr(self, "port_combo") else "",
            "ip_address": self.ip_var.get(),
            "selected_monitor": self.selected_monitor.get(),
            "capture_mode": self.capture_mode.get(),
            "fps": self.fps_var.get(),
            "capture_backend_pref": self.capture_backend_pref.get(),
        }

        try:
            with open("ambilight_config.json", "w") as f:
                json.dump(config_data, f, indent=2)
            messagebox.showinfo("Success", "Configuration saved to ambilight_config.json")
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
                self._update_conn_ui()

            if "com_port" in config_data and config_data["com_port"]:
                ports = list(self.port_combo["values"])
                if config_data["com_port"] in ports:
                    self.port_combo.set(config_data["com_port"])

            if "ip_address" in config_data:
                self.ip_var.set(config_data["ip_address"])

            # Restore monitor selection
            if "selected_monitor" in config_data:
                saved_monitor = config_data["selected_monitor"]
                monitors = list(self.monitor_combo["values"])
                if saved_monitor in monitors:
                    self.selected_monitor.set(saved_monitor)
            
            if "capture_mode" in config_data:
                self.capture_mode.set(config_data["capture_mode"])

            if "fps" in config_data:
                self.fps_var.set(config_data["fps"])

            if "capture_backend_pref" in config_data:
                val = config_data["capture_backend_pref"]
                if val in ["Auto", "DXCam", "Pillow"]:
                    self.capture_backend_pref.set(val)

            self.draw_led_map()
            
            # Update UI elements
            self.led_count_var.set(str(self.num_leds))
            
            messagebox.showinfo("Success", "Configuration loaded")

        except FileNotFoundError:
            messagebox.showwarning("Warning", "No saved configuration found")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {e}")
            print(e)

        
    def scan_network(self):
        """Start scanning the local network for ESP Ambilight devices."""
        if self.network_scanner.scanning:
            self.network_scanner.stop_scan()
            self.scan_btn.config(text="Scan Network")
            self.scan_status_label.config(text="Scan stopped", foreground="orange")
            return

        # Clear previous
        self.discovered_devices = []
        self.ip_combo["values"] = []
        self.scan_btn.config(text="Stop Scan")
        self.scan_status_label.config(text="Scanning...", foreground="blue")

        self.network_scanner.scan_network(
            on_progress=lambda c, t: self.root.after(
                0, lambda: self.scan_status_label.config(text=f"Scanning: {c}/{t}")
            ),
            on_device_found=lambda d: self.root.after(0, lambda: self._on_device_found(d)),
            on_complete=lambda d: self.root.after(0, lambda: self._on_scan_complete(d))
        )
            
    def _on_device_found(self, device):
        self.discovered_devices.append(device)
        values = [f"{d['ip']}" for d in self.discovered_devices]
        self.ip_combo["values"] = values
        if not self.ip_var.get() and values:
             self.ip_var.set(values[0])
             
    def _on_scan_complete(self, devices):
        self.scan_btn.config(text="Scan Network")
        count = len(devices)
        if count == 0:
            self.scan_status_label.config(text="No devices found", foreground="orange")
        else:
            self.scan_status_label.config(text=f"{count} found", foreground="green")

# Main execution
if __name__ == "__main__":
    root = ttk.Window(themename="darkly") # Use a modern dark theme
    app = AmbilightController(root)
    root.mainloop()
