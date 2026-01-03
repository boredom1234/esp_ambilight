import numpy as np


def apply_brightness(r, g, b, brightness):
    """Apply brightness to RGB values with black threshold."""
    if r + g + b < 15:
        return 0, 0, 0
    return (
        int(r * brightness / 255),
        int(g * brightness / 255),
        int(b * brightness / 255),
    )


def process_average_color(pixels, brightness, num_leds):
    """Calculate average color of screen."""
    avg = np.mean(pixels, axis=(0, 1)).astype(int)
    r, g, b = apply_brightness(avg[0], avg[1], avg[2], brightness)

    led_colors = bytearray()
    for _ in range(num_leds):
        led_colors.extend([r, g, b])
    return led_colors


def process_dominant_color(pixels, brightness, num_leds):
    """Extract most vibrant/saturated color from screen."""
    flat_pixels = pixels.reshape(-1, 3)

    max_vals = np.max(flat_pixels, axis=1)
    min_vals = np.min(flat_pixels, axis=1)

    with np.errstate(divide="ignore", invalid="ignore"):
        # Use float for calculation to avoid overflow and precision issues
        max_vals_f = max_vals.astype(float)
        min_vals_f = min_vals.astype(float)
        saturation = np.where(
            max_vals > 0, ((max_vals_f - min_vals_f) * 255.0) / max_vals_f, 0
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

    r, g, b = apply_brightness(r_raw, g_raw, b_raw, brightness)

    led_colors = bytearray()
    for _ in range(num_leds):
        led_colors.extend([r, g, b])
    return led_colors


def process_edge_sampling(pixels, brightness, num_leds):
    """Sample from screen edges - designed for 16 LEDs (4 per side) or more."""
    h, w = pixels.shape[:2]
    edge_width = 10

    led_colors = bytearray()
    leds_per_side = max(1, num_leds // 4)

    for i in range(num_leds):
        side = min(3, i // leds_per_side)  # Clamp to 0-3 for 4 sides
        pos = i % leds_per_side

        if side == 0:  # Top edge
            x_start = int((pos / leds_per_side) * w)
            x_end = int(((pos + 1) / leds_per_side) * w)
            region = pixels[0:edge_width, x_start:x_end]
        elif side == 1:  # Right edge
            y_start = int((pos / leds_per_side) * h)
            y_end = int(((pos + 1) / leds_per_side) * h)
            region = pixels[y_start:y_end, w - edge_width : w]
        elif side == 2:  # Bottom edge (reversed)
            x_start = int(((leds_per_side - 1 - pos) / leds_per_side) * w)
            x_end = int(((leds_per_side - pos) / leds_per_side) * w)
            region = pixels[h - edge_width : h, x_start:x_end]
        else:  # Left edge (reversed)
            y_start = int(((leds_per_side - 1 - pos) / leds_per_side) * h)
            y_end = int(((leds_per_side - pos) / leds_per_side) * h)
            region = pixels[y_start:y_end, 0:edge_width]

        if region.size > 0:
            avg = np.mean(region, axis=(0, 1)).astype(int)
            r, g, b = apply_brightness(avg[0], avg[1], avg[2], brightness)
        else:
            r, g, b = 0, 0, 0

        led_colors.extend([r, g, b])

    return led_colors


def process_quadrant_colors(pixels, brightness, num_leds):
    """Divide screen into 4 quadrants, assign colors to LED groups."""
    h, w = pixels.shape[:2]

    quadrants = [
        pixels[0 : h // 2, 0 : w // 2],  # Top-left
        pixels[0 : h // 2, w // 2 : w],  # Top-right
        pixels[h // 2 : h, 0 : w // 2],  # Bottom-left
        pixels[h // 2 : h, w // 2 : w],  # Bottom-right
    ]

    led_colors = bytearray()
    leds_per_quad = max(1, num_leds // 4)

    for q_idx, quad in enumerate(quadrants):
        avg = np.mean(quad, axis=(0, 1)).astype(int)
        r, g, b = apply_brightness(avg[0], avg[1], avg[2], brightness)

        for _ in range(leds_per_quad):
            led_colors.extend([r, g, b])

    # Fill remaining LEDs if num_leds isn't divisible by 4
    while len(led_colors) < num_leds * 3:
        led_colors.extend([0, 0, 0])

    return led_colors[: num_leds * 3]


def process_most_vibrant(pixels, brightness, num_leds):
    """Find the single most saturated pixel color."""
    flat_pixels = pixels.reshape(-1, 3)
    max_vals = np.max(flat_pixels, axis=1)
    min_vals = np.min(flat_pixels, axis=1)

    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(max_vals > 0, (max_vals - min_vals) / max_vals, 0)

    max_sat_idx = np.argmax(saturation)
    most_vibrant = flat_pixels[max_sat_idx]

    r, g, b = apply_brightness(
        int(most_vibrant[0]), int(most_vibrant[1]), int(most_vibrant[2]), brightness
    )

    led_colors = bytearray()
    for _ in range(num_leds):
        led_colors.extend([r, g, b])
    return led_colors


def process_warm_bias(pixels, brightness, num_leds):
    """Average color shifted warmer (more red, less blue)."""
    avg = np.mean(pixels, axis=(0, 1)).astype(int)
    r_raw = min(255, int(avg[0] * 1.3))
    g_raw = avg[1]
    b_raw = max(0, int(avg[2] * 0.7))

    r, g, b = apply_brightness(r_raw, g_raw, b_raw, brightness)

    led_colors = bytearray()
    for _ in range(num_leds):
        led_colors.extend([r, g, b])
    return led_colors


def process_cool_bias(pixels, brightness, num_leds):
    """Average color shifted cooler (more blue, less red)."""
    avg = np.mean(pixels, axis=(0, 1)).astype(int)
    r_raw = max(0, int(avg[0] * 0.7))
    g_raw = avg[1]
    b_raw = min(255, int(avg[2] * 1.3))

    r, g, b = apply_brightness(r_raw, g_raw, b_raw, brightness)

    led_colors = bytearray()
    for _ in range(num_leds):
        led_colors.extend([r, g, b])
    return led_colors


def process_screen_map(pixels, brightness, num_leds, led_positions):
    """Sample screen at each LED's calibrated position."""
    # Ensure we have positions for all LEDs
    # Note: led_positions could be modified in place if we aren't careful,
    # but here we should probably return a new list if we were to modify it.
    # However, for pure processing, we assume led_positions is valid or we just use what we have.

    # Create local copy or extend if needed (internal logic only)
    positions = list(led_positions)
    while len(positions) < num_leds:
        positions.append({"x": 0.5, "y": 0.5})

    h, w = pixels.shape[:2]
    sample_radius = 1

    led_colors = bytearray()

    # Only iterate up to num_leds to ensure correct output size
    for i in range(num_leds):
        led = positions[i]
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
            r, g, b = apply_brightness(avg[0], avg[1], avg[2], brightness)
        else:
            r, g, b = 0, 0, 0

        led_colors.extend([r, g, b])

    return led_colors
