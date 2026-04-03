# ============================================================================
# CONFIGURATION
# ============================================================================

MAGIC_BYTE_1 = 0xAD
MAGIC_BYTE_2 = 0xDA

# Default settings
DEFAULT_LED_COUNT = 60
DEFAULT_BAUD_RATE = 115200
DEFAULT_WEBSOCKET_PORT = 81
DEFAULT_IP = "192.168.4.1"

# Low-latency transport tuning
LOW_LATENCY_DEFAULT = True
TX_COMMAND_QUEUE_SIZE = 64
TX_MAX_PENDING_FRAMES = 1
TX_IDLE_SLEEP_S = 0.01
WS_CONNECT_TIMEOUT_S = 6.0
WS_PING_INTERVAL_S = 10
WS_PING_TIMEOUT_S = 8
RECONNECT_BASE_DELAY_MS = 250
RECONNECT_MAX_DELAY_MS = 4000
WS_RECONNECT_JITTER_MS = 250
WS_RECONNECT_MAX_ATTEMPTS = 0  # 0 means infinite attempts

# Effect settings
EFFECT_FPS = 30

# Presets file path
PRESETS_FILE = "color_presets.json"

# Built-in color presets (name -> RGB tuple)
DEFAULT_PRESETS = {
    "Movie Night": (255, 147, 41),  # Warm amber
    "Gaming": (138, 43, 226),  # Blue-violet
    "Relaxed": (255, 200, 150),  # Soft warm white
    "Night Light": (255, 100, 50),  # Dim orange
    "Cool White": (200, 220, 255),  # Cool daylight
    "Forest": (34, 139, 34),  # Forest green
    "Sunset": (255, 100, 50),  # Orange-red
    "Ocean Blue": (0, 105, 148),  # Deep ocean
}
