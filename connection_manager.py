import json
import time
import threading
import config

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


class ConnectionManager:
    """Manages connections to ESP32 via USB or WebSocket."""

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
        self.led_count = config.DEFAULT_LED_COUNT

    def connect_usb(self, port: str, baud: int = config.DEFAULT_BAUD_RATE) -> bool:
        """Connect via USB Serial."""
        if not SERIAL_AVAILABLE:
            self._error("pyserial not installed")
            return False

        try:
            self.serial_port = serial.Serial(port, baud, timeout=1)
            time.sleep(2)  # Wait for Arduino reset
            self.serial_port.reset_input_buffer()

            # Mark as connected first so send_command works
            self.mode = "usb"
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
                    if response.startswith("{"):
                        self._handle_message(response)
                        break

            if self.on_connected:
                self.on_connected("usb", port)

            print(f"[USB] Connected! LED count: {self.led_count}")
            return True

        except Exception as e:
            self._error(f"USB connection failed: {e}")
            self.connected = False
            self.mode = None
            return False

    def connect_websocket(
        self, ip: str, port: int = config.DEFAULT_WEBSOCKET_PORT
    ) -> bool:
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
                daemon=True,
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

        if self.mode == "usb" and self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None

        elif self.mode == "websocket" and self.ws:
            try:
                self.ws.close()
            except Exception:
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

            if self.mode == "usb":
                self.serial_port.write((data + "\n").encode())

            elif self.mode == "websocket":
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
            if self.mode == "websocket":
                # WebSocket uses raw binary (has its own integrity check)
                self.ws.send(rgb_data, opcode=websocket.ABNF.OPCODE_BINARY)

            else:
                # USB uses framed protocol with checksum
                checksum = 0
                for b in rgb_data:
                    checksum ^= b

                frame = (
                    bytes([config.MAGIC_BYTE_1, config.MAGIC_BYTE_2])
                    + rgb_data
                    + bytes([checksum])
                )

                if self.mode == "usb":
                    self.serial_port.write(frame)

            return True

        except Exception as e:
            print(f"Send colors error: {e}")
            return False

    # WebSocket callbacks
    def _ws_on_open(self, ws):
        self.mode = "websocket"
        self.connected = True
        print("[WS] Connection opened, waiting for device info...")
        if self.on_connected:
            self.on_connected("websocket", "")

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
