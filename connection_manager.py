import json
import queue
import random
import socket
import threading
import time
from collections import deque

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
    """Manages low-latency USB and WebSocket transport to ESP32."""

    def __init__(self):
        self.mode = None  # "usb", "websocket"
        self.connected = False
        self.reconnecting = False

        # Connection objects
        self.serial_port = None
        self.ws = None
        self.ws_thread = None

        # Last successful connection settings (for reconnect)
        self._last_usb_port = None
        self._last_usb_baud = config.DEFAULT_BAUD_RATE
        self._last_ws_ip = None
        self._last_ws_port = config.DEFAULT_WEBSOCKET_PORT

        # WebSocket session state
        self._ws_session_id = 0
        self._ws_connecting = False
        self._ws_fail_streak = 0

        # Callbacks
        self.on_connected = None
        self.on_disconnected = None
        self.on_message = None
        self.on_error = None

        # Device info received from ESP32
        self.led_count = config.DEFAULT_LED_COUNT

        # Thread safety
        self._state_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._stats_lock = threading.Lock()

        # Low-latency TX pipeline
        self._cmd_queue = queue.Queue(maxsize=config.TX_COMMAND_QUEUE_SIZE)
        self._latest_frame = None
        self._tx_wakeup = threading.Event()
        self._tx_stop = threading.Event()
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self._tx_thread.start()

        # Reconnect behavior
        self._manual_disconnect = False
        self._auto_reconnect = True
        self._reconnect_lock = threading.Lock()
        self._reconnect_thread = None

        # Stats/telemetry
        self._send_times_ms = deque(maxlen=200)
        self.stats = {
            "frames_sent": 0,
            "frames_dropped": 0,
            "commands_sent": 0,
            "send_errors": 0,
            "reconnects": 0,
            "reconnect_attempts": 0,
            "send_ms_avg": 0.0,
            "send_ms_p95": 0.0,
            "last_error": "",
            "ws_close_code": None,
            "ws_close_reason": "",
        }

    def connect_usb(self, port: str, baud: int = config.DEFAULT_BAUD_RATE) -> bool:
        """Connect via USB serial and start streaming-ready state."""
        if not SERIAL_AVAILABLE:
            self._error("pyserial not installed")
            return False

        self._manual_disconnect = False
        self.reconnecting = False

        try:
            ser = serial.Serial(port, baud, timeout=0.15, write_timeout=0.15)
            time.sleep(1.0)  # Allow board reset after opening serial
            ser.reset_input_buffer()

            with self._state_lock:
                self.serial_port = ser
                self.mode = "usb"
                self.connected = True
                self._last_usb_port = port
                self._last_usb_baud = baud

            # Request device info with quick retries
            for _ in range(3):
                try:
                    ser.write((json.dumps({"cmd": "info"}) + "\n").encode())
                    deadline = time.time() + 0.35
                    while time.time() < deadline:
                        if ser.in_waiting:
                            response = ser.readline().decode(errors="ignore").strip()
                            if response.startswith("{"):
                                self._handle_message(response)
                                break
                        time.sleep(0.01)
                except Exception:
                    pass

            self._tx_wakeup.set()
            if self.on_connected:
                self.on_connected("usb", port)
            return True

        except Exception as e:
            self._error(f"USB connection failed: {e}")
            with self._state_lock:
                self.connected = False
                self.mode = None
            return False

    def connect_websocket(
        self,
        ip: str,
        port: int = config.DEFAULT_WEBSOCKET_PORT,
        manual_intent: bool = True,
    ) -> bool:
        """Connect via WebSocket with session-safe callbacks and cleanup."""
        if not WEBSOCKET_AVAILABLE:
            self._error("websocket-client not installed")
            return False

        if manual_intent:
            self._manual_disconnect = False
            self.reconnecting = False

        self._last_ws_ip = ip
        self._last_ws_port = port

        with self._state_lock:
            self._close_ws_locked()
            self._ws_session_id += 1
            session_id = self._ws_session_id
            self._ws_connecting = True

        try:
            ws_url = f"ws://{ip}:{port}"
            ws_app = websocket.WebSocketApp(
                ws_url,
                on_message=lambda ws, message: self._ws_on_message(ws, message, session_id),
                on_error=lambda ws, error: self._ws_on_error(ws, error, session_id),
                on_close=lambda ws, code, msg: self._ws_on_close(ws, code, msg, session_id),
                on_open=lambda ws: self._ws_on_open(ws, session_id),
            )

            # Keep transport low-latency over TCP
            sockopt = ((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),)
            ws_thread = threading.Thread(
                target=lambda: ws_app.run_forever(
                    ping_interval=config.WS_PING_INTERVAL_S,
                    ping_timeout=config.WS_PING_TIMEOUT_S,
                    sockopt=sockopt,
                ),
                daemon=True,
            )

            with self._state_lock:
                self.ws = ws_app
                self.ws_thread = ws_thread
            ws_thread.start()

            # Wait for connection with timeout
            start = time.time()
            while (time.time() - start) < config.WS_CONNECT_TIMEOUT_S:
                if self._manual_disconnect:
                    self._close_ws_attempt(session_id)
                    return False

                with self._state_lock:
                    connected = (
                        self.connected
                        and self.mode == "websocket"
                        and self._ws_session_id == session_id
                    )
                if connected:
                    return True
                time.sleep(0.05)

            self._error("WebSocket connection timeout")
            self._close_ws_attempt(session_id)
            return False

        except Exception as e:
            self._error(f"WebSocket connection failed: {e}")
            self._close_ws_attempt(session_id)
            return False

    def disconnect(self):
        """Manual disconnect: stop current transport and clear pending TX."""
        self._manual_disconnect = True
        self.reconnecting = False
        self._set_disconnected(notify=False)
        if self.on_disconnected:
            self.on_disconnected()

    def send_command(self, cmd: dict) -> bool:
        """Queue JSON command for prioritized send."""
        with self._state_lock:
            if not self.connected:
                return False

        data = json.dumps(cmd)
        try:
            self._cmd_queue.put_nowait(data)
            self._tx_wakeup.set()
            return True
        except queue.Full:
            self._error("Command queue full")
            return False

    def send_colors(self, rgb_data: bytes) -> bool:
        """Queue latest LED frame (drop stale frame for lowest latency)."""
        with self._state_lock:
            if not self.connected:
                return False

        with self._frame_lock:
            if self._latest_frame is not None:
                with self._stats_lock:
                    self.stats["frames_dropped"] += 1
            self._latest_frame = rgb_data

        self._tx_wakeup.set()
        return True

    # WebSocket callbacks
    def _ws_on_open(self, ws, session_id: int):
        if not self._is_current_ws(ws, session_id):
            return
        with self._state_lock:
            self.mode = "websocket"
            self.connected = True
            self._ws_connecting = False
            self._ws_fail_streak = 0
        self.reconnecting = False
        self._tx_wakeup.set()
        if self.on_connected:
            self.on_connected("websocket", f"{self._last_ws_ip}:{self._last_ws_port}")

    def _ws_on_message(self, ws, message, session_id: int):
        if not self._is_current_ws(ws, session_id):
            return
        self._handle_message(message)

    def _ws_on_error(self, ws, error, session_id: int):
        if not self._is_current_ws(ws, session_id):
            return
        self._error(f"WS error: {error}")

    def _ws_on_close(self, ws, close_status_code, close_msg, session_id: int):
        if not self._is_current_ws(ws, session_id):
            return

        with self._stats_lock:
            self.stats["ws_close_code"] = close_status_code
            self.stats["ws_close_reason"] = str(close_msg or "")

        with self._state_lock:
            self._ws_connecting = False
            self._ws_fail_streak += 1

        if self._manual_disconnect:
            return
        self._set_disconnected(notify=True)
        self._schedule_reconnect("websocket")

    def _is_current_ws(self, ws, session_id: int) -> bool:
        with self._state_lock:
            return ws is self.ws and session_id == self._ws_session_id

    def _close_ws_locked(self):
        ws = self.ws
        self.ws = None
        self.ws_thread = None
        self._ws_connecting = False
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def _close_ws_attempt(self, session_id: int):
        with self._state_lock:
            if session_id != self._ws_session_id:
                return
            self._close_ws_locked()
            if self.mode == "websocket":
                self.mode = None
            if not self.serial_port:
                self.connected = False

    def _handle_message(self, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type in ["info", "ready"]:
                self.led_count = data.get("ledCount", config.DEFAULT_LED_COUNT)

            if self.on_message:
                self.on_message(data)

        except (json.JSONDecodeError, TypeError):
            pass

    def _tx_loop(self):
        """Single TX loop: commands first, then newest frame only."""
        while not self._tx_stop.is_set():
            with self._state_lock:
                connected = self.connected
            if not connected:
                self._tx_wakeup.wait(config.TX_IDLE_SLEEP_S)
                self._tx_wakeup.clear()
                continue

            did_work = False
            try:
                cmd = self._cmd_queue.get_nowait()
                self._send_now(cmd, is_binary=False)
                did_work = True
            except queue.Empty:
                pass

            if not did_work:
                frame = None
                with self._frame_lock:
                    if self._latest_frame is not None:
                        frame = self._latest_frame
                        self._latest_frame = None
                if frame is not None:
                    self._send_now(frame, is_binary=True)
                    did_work = True

            if not did_work:
                self._tx_wakeup.wait(config.TX_IDLE_SLEEP_S)
                self._tx_wakeup.clear()

    def _send_now(self, payload, is_binary: bool):
        start = time.perf_counter()
        try:
            with self._state_lock:
                mode = self.mode
                ws = self.ws
                ser = self.serial_port
                connected = self.connected

            if not connected:
                return

            if mode == "websocket":
                if is_binary:
                    ws.send(payload, opcode=websocket.ABNF.OPCODE_BINARY)
                else:
                    ws.send(payload)
            elif mode == "usb":
                if is_binary:
                    checksum = 0
                    for b in payload:
                        checksum ^= b
                    frame = (
                        bytes([config.MAGIC_BYTE_1, config.MAGIC_BYTE_2])
                        + payload
                        + bytes([checksum])
                    )
                    ser.write(frame)
                else:
                    ser.write((payload + "\n").encode())
            else:
                return

            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._record_send_timing(elapsed_ms, is_binary=is_binary)

        except Exception as e:
            self._record_send_error(str(e))
            self._handle_transport_failure(str(e))

    def _record_send_timing(self, elapsed_ms: float, is_binary: bool):
        with self._stats_lock:
            self._send_times_ms.append(elapsed_ms)
            if is_binary:
                self.stats["frames_sent"] += 1
            else:
                self.stats["commands_sent"] += 1

            if self._send_times_ms:
                values = list(self._send_times_ms)
                self.stats["send_ms_avg"] = sum(values) / len(values)
                sorted_vals = sorted(values)
                idx = int((len(sorted_vals) - 1) * 0.95)
                self.stats["send_ms_p95"] = sorted_vals[idx]

    def _record_send_error(self, message: str):
        with self._stats_lock:
            self.stats["send_errors"] += 1
            self.stats["last_error"] = message

    def _handle_transport_failure(self, message: str):
        self._error(message)
        if self._manual_disconnect:
            return
        with self._state_lock:
            last_mode = self.mode
        self._set_disconnected(notify=True)
        if last_mode:
            self._schedule_reconnect(last_mode)

    def _set_disconnected(self, notify: bool):
        with self._state_lock:
            ws = self.ws
            ser = self.serial_port
            self.connected = False
            self.mode = None
            self.ws = None
            self.ws_thread = None
            self.serial_port = None
            self._ws_connecting = False

        if ser:
            try:
                ser.close()
            except Exception:
                pass

        if ws:
            try:
                ws.close()
            except Exception:
                pass

        self._clear_tx_buffers()
        if notify and self.on_disconnected:
            self.on_disconnected()

    def _clear_tx_buffers(self):
        with self._frame_lock:
            self._latest_frame = None
        while True:
            try:
                self._cmd_queue.get_nowait()
            except queue.Empty:
                break

    def _schedule_reconnect(self, target_mode: str):
        if not self._auto_reconnect or self._manual_disconnect:
            return

        self.reconnecting = True
        with self._reconnect_lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_worker, args=(target_mode,), daemon=True
            )
            self._reconnect_thread.start()

    def _reconnect_worker(self, target_mode: str):
        delay_ms = config.RECONNECT_BASE_DELAY_MS
        attempts = 0

        while not self._manual_disconnect:
            attempts += 1
            with self._stats_lock:
                self.stats["reconnect_attempts"] += 1

            if (
                target_mode == "websocket"
                and config.WS_RECONNECT_MAX_ATTEMPTS > 0
                and attempts > config.WS_RECONNECT_MAX_ATTEMPTS
            ):
                self.reconnecting = False
                return

            ok = False
            if target_mode == "websocket" and self._last_ws_ip:
                ok = self.connect_websocket(
                    self._last_ws_ip, self._last_ws_port, manual_intent=False
                )
            elif target_mode == "usb" and self._last_usb_port:
                ok = self.connect_usb(self._last_usb_port, self._last_usb_baud)

            if ok:
                with self._stats_lock:
                    self.stats["reconnects"] += 1
                self.reconnecting = False
                return

            jitter = random.randint(0, max(0, config.WS_RECONNECT_JITTER_MS))
            time.sleep((delay_ms + jitter) / 1000.0)
            delay_ms = min(delay_ms * 2, config.RECONNECT_MAX_DELAY_MS)

        self.reconnecting = False

    def get_stats(self) -> dict:
        with self._stats_lock:
            data = dict(self.stats)
        data["reconnecting"] = self.reconnecting
        return data

    def _error(self, msg):
        print(f"Connection error: {msg}")
        with self._stats_lock:
            self.stats["last_error"] = msg
        if self.on_error:
            self.on_error(msg)
