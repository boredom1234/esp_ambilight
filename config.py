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
