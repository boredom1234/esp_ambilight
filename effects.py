"""
Gradient effect generators for LED animations.
Each function returns a bytearray of RGB values for all LEDs.
"""

import math
import random


def hsv_to_rgb(h, s, v):
    """Convert HSV (0-1 range) to RGB (0-255 range)."""
    if s == 0:
        r = g = b = int(v * 255)
        return r, g, b

    i = int(h * 6)
    f = (h * 6) - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))

    i %= 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q

    return int(r * 255), int(g * 255), int(b * 255)


def apply_brightness(r, g, b, brightness):
    """Apply brightness (0-255) to RGB values."""
    factor = brightness / 255.0
    return int(r * factor), int(g * factor), int(b * factor)


def generate_rainbow(num_leds, brightness, phase):
    """
    Generate smooth rainbow gradient that moves across LEDs.
    Phase: 0.0 to 1.0 controls animation position.
    """
    led_colors = bytearray()

    for i in range(num_leds):
        # Each LED gets a different hue, offset by phase
        hue = (i / num_leds + phase) % 1.0
        r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
        r, g, b = apply_brightness(r, g, b, brightness)
        led_colors.extend([r, g, b])

    return led_colors


def generate_fire(num_leds, brightness, phase):
    """
    Generate fire/flame effect with warm colors and flickering.
    Phase controls the random seed for consistent animation.
    """
    led_colors = bytearray()

    # Use phase to create smooth variation
    random.seed(int(phase * 1000) % 1000)

    for i in range(num_leds):
        # Base flame color (red-orange-yellow)
        base_heat = 0.6 + 0.4 * math.sin(phase * 10 + i * 0.5)
        flicker = random.uniform(0.7, 1.0)
        heat = base_heat * flicker

        # Map heat to color (black -> red -> orange -> yellow -> white)
        if heat < 0.33:
            r = int(heat * 3 * 255)
            g = 0
            b = 0
        elif heat < 0.66:
            r = 255
            g = int((heat - 0.33) * 3 * 200)
            b = 0
        else:
            r = 255
            g = 200 + int((heat - 0.66) * 3 * 55)
            b = int((heat - 0.66) * 3 * 100)

        r, g, b = apply_brightness(r, g, b, brightness)
        led_colors.extend([r, g, b])

    return led_colors


def generate_ocean(num_leds, brightness, phase):
    """
    Generate ocean wave effect with blues and teals.
    Phase controls wave position.
    """
    led_colors = bytearray()

    for i in range(num_leds):
        # Multiple overlapping waves
        wave1 = math.sin(phase * 4 + i * 0.3) * 0.5 + 0.5
        wave2 = math.sin(phase * 6 + i * 0.5 + 2) * 0.3 + 0.5
        wave3 = math.sin(phase * 2 + i * 0.1) * 0.2 + 0.5

        combined = (wave1 + wave2 + wave3) / 3

        # Ocean colors: deep blue to teal to light blue
        r = int(combined * 50)
        g = int(100 + combined * 100)
        b = int(150 + combined * 105)

        r, g, b = apply_brightness(r, g, b, brightness)
        led_colors.extend([r, g, b])

    return led_colors


def generate_aurora(num_leds, brightness, phase):
    """
    Generate aurora borealis effect with greens, blues, and purples.
    Phase controls the flowing animation.
    """
    led_colors = bytearray()

    for i in range(num_leds):
        # Slow flowing waves with color transitions
        pos = i / num_leds
        wave = math.sin(phase * 2 + pos * 8) * 0.5 + 0.5
        shimmer = math.sin(phase * 5 + pos * 15) * 0.3 + 0.7

        # Cycle through aurora colors (green -> teal -> blue -> purple -> green)
        hue_base = (phase * 0.5 + pos * 0.5) % 1.0

        # Aurora hue range: green (0.33) to purple (0.8)
        hue = 0.33 + hue_base * 0.47

        # Vary saturation and value based on waves
        sat = 0.7 + wave * 0.3
        val = 0.5 + shimmer * 0.5

        r, g, b = hsv_to_rgb(hue, sat, val)
        r, g, b = apply_brightness(r, g, b, brightness)
        led_colors.extend([r, g, b])

    return led_colors


def generate_static_color(num_leds, brightness, r, g, b):
    """
    Generate solid color for all LEDs.
    """
    r, g, b = apply_brightness(r, g, b, brightness)
    led_colors = bytearray()
    for _ in range(num_leds):
        led_colors.extend([r, g, b])
    return led_colors


# Effect registry for easy lookup
EFFECTS = {
    "Rainbow": generate_rainbow,
    "Fire": generate_fire,
    "Ocean Wave": generate_ocean,
    "Aurora": generate_aurora,
}
