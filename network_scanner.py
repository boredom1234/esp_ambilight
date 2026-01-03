# ============================================================================
# NETWORK SCANNER - Auto-discover ESP Ambilight devices on local network
# ============================================================================

import socket
import threading
import concurrent.futures
import urllib.request
import json
from typing import List, Dict, Optional, Callable


# Scanner configuration
SCAN_TIMEOUT = 0.5  # seconds per IP
SCAN_PARALLEL_WORKERS = 50  # concurrent connections
WEBSOCKET_PORT = 81
HTTP_PORT = 80


class NetworkScanner:
    """Scans local network for ESP Ambilight devices."""

    def __init__(self):
        self.scanning = False
        self.devices_found: List[Dict] = []
        self._stop_event = threading.Event()

    def get_all_local_ips(self) -> List[str]:
        """
        Get ALL local IP addresses from all network interfaces.
        This includes WiFi, Ethernet, and Mobile Hotspot networks.
        """
        local_ips = set()

        # Method 1: Get IPs from all interfaces using socket
        try:
            hostname = socket.gethostname()
            # Get all IPs associated with hostname
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127."):
                    local_ips.add(ip)
        except Exception:
            pass

        # Method 2: Try connecting to different destinations to find route IPs
        # This helps detect hotspot networks
        test_destinations = [
            ("8.8.8.8", 80),  # Internet route
            ("192.168.1.1", 80),  # Common router
            ("192.168.0.1", 80),  # Common router
            ("192.168.137.1", 80),  # Windows Mobile Hotspot (this is important!)
            ("10.0.0.1", 80),  # Some networks
        ]

        for dest in test_destinations:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)
                s.connect(dest)
                ip = s.getsockname()[0]
                if not ip.startswith("127."):
                    local_ips.add(ip)
                s.close()
            except Exception:
                pass

        # Method 3: Add Windows mobile hotspot network explicitly if host is hotspot
        # Windows hotspot typically uses 192.168.137.1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.bind(("192.168.137.1", 0))
            local_ips.add("192.168.137.1")
            s.close()
        except Exception:
            pass

        return list(local_ips)

    def get_ip_range(self) -> List[str]:
        """
        Generate list of IPs to scan based on ALL local networks.
        Scans all detected network interfaces including hotspot.
        """
        all_local_ips = self.get_all_local_ips()
        if not all_local_ips:
            return []

        ips = set()

        for local_ip in all_local_ips:
            parts = local_ip.split(".")
            if len(parts) != 4:
                continue

            base = f"{parts[0]}.{parts[1]}.{parts[2]}"
            # Skip .0 (network) and .255 (broadcast), skip our own IPs
            for i in range(1, 255):
                ip = f"{base}.{i}"
                if ip not in all_local_ips:
                    ips.add(ip)

        return list(ips)

    def check_port_open(self, ip: str, port: int) -> bool:
        """Check if a port is open on the given IP."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SCAN_TIMEOUT)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def validate_esp_ambilight(self, ip: str) -> Optional[Dict]:
        """
        Validate if the IP is an ESP Ambilight device.
        Returns device info dict if valid, None otherwise.
        """
        try:
            url = f"http://{ip}:{HTTP_PORT}/api/status"
            req = urllib.request.Request(url, headers={"User-Agent": "ESP-Scanner"})
            with urllib.request.urlopen(req, timeout=SCAN_TIMEOUT) as response:
                data = json.loads(response.read().decode())
                # ESP Ambilight returns: ledsActive, lastSource, wifiConnected, etc.
                if "ledsActive" in data or "wsClients" in data:
                    return {
                        "ip": ip,
                        "leds_active": data.get("ledsActive", False),
                        "last_source": data.get("lastSource", ""),
                        "ws_clients": data.get("wsClients", 0),
                        "uptime": data.get("uptime", 0),
                    }
        except Exception:
            pass
        return None

    def scan_ip(self, ip: str) -> Optional[Dict]:
        """Scan a single IP for ESP Ambilight device."""
        if self._stop_event.is_set():
            return None

        # First check if WebSocket port (81) or HTTP port (80) is open
        if self.check_port_open(ip, WEBSOCKET_PORT) or self.check_port_open(
            ip, HTTP_PORT
        ):
            # Validate it's actually an ESP Ambilight
            device = self.validate_esp_ambilight(ip)
            if device:
                return device
        return None

    def scan_network(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_complete: Optional[Callable[[List[Dict]], None]] = None,
        on_device_found: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        """
        Scan the local network for ESP Ambilight devices.

        Args:
            on_progress: Callback(current, total) for progress updates
            on_complete: Callback(devices) when scan finishes
            on_device_found: Callback(device) when a device is found
        """
        self.scanning = True
        self.devices_found = []
        self._stop_event.clear()

        def _scan_thread():
            ips = self.get_ip_range()
            total = len(ips)
            scanned = 0

            if total == 0:
                if on_complete:
                    on_complete([])
                self.scanning = False
                return

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=SCAN_PARALLEL_WORKERS
            ) as executor:
                future_to_ip = {executor.submit(self.scan_ip, ip): ip for ip in ips}

                for future in concurrent.futures.as_completed(future_to_ip):
                    if self._stop_event.is_set():
                        break

                    scanned += 1
                    if on_progress:
                        on_progress(scanned, total)

                    try:
                        device = future.result()
                        if device:
                            self.devices_found.append(device)
                            if on_device_found:
                                on_device_found(device)
                    except Exception:
                        pass

            self.scanning = False
            if on_complete:
                on_complete(self.devices_found)

        thread = threading.Thread(target=_scan_thread, daemon=True)
        thread.start()

    def stop_scan(self):
        """Stop an ongoing scan."""
        self._stop_event.set()
        self.scanning = False


# Convenience function for quick scans
def find_esp_devices(timeout: float = 30.0) -> List[Dict]:
    """
    Blocking function to find all ESP Ambilight devices on the network.

    Args:
        timeout: Maximum time to wait for scan completion

    Returns:
        List of device dictionaries with IP and status info
    """
    scanner = NetworkScanner()
    result = []
    done_event = threading.Event()

    def on_complete(devices):
        nonlocal result
        result = devices
        done_event.set()

    scanner.scan_network(on_complete=on_complete)
    done_event.wait(timeout=timeout)

    if scanner.scanning:
        scanner.stop_scan()

    return result


if __name__ == "__main__":
    # Test the scanner
    print("ESP Ambilight Network Scanner Test")
    print("=" * 40)
    scanner = NetworkScanner()

    # Show detected networks
    local_ips = scanner.get_all_local_ips()
    print(f"\nDetected network interfaces ({len(local_ips)}):")
    for ip in local_ips:
        parts = ip.split(".")
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.x"
        print(f"  • {ip} → will scan {subnet}")

    ip_range = scanner.get_ip_range()
    print(f"\nTotal IPs to scan: {len(ip_range)}")
    print("\nScanning for ESP Ambilight devices...")

    def progress(current, total):
        print(f"\rScanning: {current}/{total}", end="", flush=True)

    def found(device):
        print(f"\n✓ Found device: {device['ip']}")

    def complete(devices):
        print(f"\n\nScan complete. Found {len(devices)} device(s).")
        for d in devices:
            print(f"  - {d['ip']} (uptime: {d['uptime']}s)")

    scanner.scan_network(
        on_progress=progress, on_device_found=found, on_complete=complete
    )

    # Wait for scan to complete
    import time

    while scanner.scanning:
        time.sleep(0.1)
