/*
 * USB Ambilight - Arduino Nano
 * 
 * Receives RGB data over serial USB and drives WS2812B LEDs.
 * 
 * Hardware:
 * - Arduino Nano
 * - WS2812B 16-LED strip connected to D6
 * 
 * Protocol (Binary Frame):
 * - Byte 0: 0xAD (magic start byte)
 * - Byte 1: 0xDA (sync byte - confirms real frame)
 * - Bytes 2-49: 16 * 3 bytes (R,G,B for each LED)
 * - Byte 50: checksum (XOR of all RGB bytes)
 * Total: 51 bytes per frame
 *   
 * Baud rate: 115200
 */

#include <FastLED.h>

// --- Configuration ---
#define LED_PIN     6           // D6 on Arduino Nano
#define NUM_LEDS    16          // WS2812B 16-LED strip
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB
#define BAUD_RATE   115200

// Protocol constants
#define MAGIC_BYTE_1  0xAD      // Start of binary frame
#define MAGIC_BYTE_2  0xDA      // Sync confirmation
#define FRAME_SIZE    (NUM_LEDS * 3)  // 48 bytes of RGB data

// --- Global Variables ---
CRGB leds[NUM_LEDS];
uint8_t brightness = 255;
uint8_t rgbBuffer[FRAME_SIZE];
int bufferIndex = 0;
int syncState = 0;  // 0=idle, 1=got 0xAD, 2=got 0xDA, 3=reading RGB

unsigned long lastByteTime = 0;
const unsigned long TIMEOUT_MS = 30;

// JSON command buffer
char cmdBuffer[64];
int cmdIndex = 0;
bool inJsonCmd = false;

void setup() {
  Serial.begin(BAUD_RATE);
  
  // Initialize LED strip
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(brightness);
  FastLED.clear();
  FastLED.show();
  
  // Quick startup flash
  fill_solid(leds, NUM_LEDS, CRGB::Blue);
  FastLED.show();
  delay(200);
  FastLED.clear();
  FastLED.show();
  
  Serial.println("{\"type\":\"ready\",\"ledCount\":16}");
}

void loop() {
  // Timeout - reset state machine if data stream interrupted
  if (syncState > 0 && (millis() - lastByteTime > TIMEOUT_MS)) {
    syncState = 0;
    bufferIndex = 0;
  }
  
  while (Serial.available() > 0) {
    uint8_t b = Serial.read();
    lastByteTime = millis();
    
    // State machine for binary frame
    switch (syncState) {
      case 0:  // Waiting for start
        if (b == MAGIC_BYTE_1) {
          syncState = 1;
        } else if (b == '{') {
          // Start of JSON command
          cmdBuffer[0] = '{';
          cmdIndex = 1;
          inJsonCmd = true;
        } else if (inJsonCmd && cmdIndex > 0) {
          if (cmdIndex < sizeof(cmdBuffer) - 1) {
            cmdBuffer[cmdIndex++] = b;
            if (b == '}') {
              cmdBuffer[cmdIndex] = '\0';
              processCommand(cmdBuffer);
              cmdIndex = 0;
              inJsonCmd = false;
            }
          } else {
            cmdIndex = 0;
            inJsonCmd = false;
          }
        }
        break;
        
      case 1:  // Got 0xAD, waiting for 0xDA
        if (b == MAGIC_BYTE_2) {
          syncState = 2;
          bufferIndex = 0;
        } else {
          // False start, reset
          syncState = 0;
        }
        break;
        
      case 2:  // Reading RGB data
        rgbBuffer[bufferIndex++] = b;
        
        if (bufferIndex >= FRAME_SIZE) {
          // Read checksum byte
          syncState = 3;
        }
        break;
        
      case 3:  // Verify checksum
        {
          uint8_t checksum = 0;
          for (int i = 0; i < FRAME_SIZE; i++) {
            checksum ^= rgbBuffer[i];
          }
          
          if (checksum == b) {
            // Valid frame - update LEDs
            for (int i = 0; i < NUM_LEDS; i++) {
              int idx = i * 3;
              leds[i].r = rgbBuffer[idx];
              leds[i].g = rgbBuffer[idx + 1];
              leds[i].b = rgbBuffer[idx + 2];
            }
            FastLED.show();
          }
          // else: checksum mismatch, discard frame
          
          syncState = 0;
          bufferIndex = 0;
        }
        break;
    }
  }
}

void processCommand(const char* cmd) {
  if (strstr(cmd, "\"brightness\"")) {
    const char* valuePtr = strstr(cmd, "\"value\":");
    if (valuePtr) {
      brightness = atoi(valuePtr + 8);
      FastLED.setBrightness(brightness);
      FastLED.show();
      Serial.println("{\"type\":\"ack\",\"cmd\":\"brightness\"}");
    }
  }
  else if (strstr(cmd, "\"clear\"")) {
    FastLED.clear();
    FastLED.show();
    Serial.println("{\"type\":\"ack\",\"cmd\":\"clear\"}");
  }
  else if (strstr(cmd, "\"info\"")) {
    Serial.print("{\"type\":\"info\",\"ledCount\":");
    Serial.print(NUM_LEDS);
    Serial.println("}");
  }
  else if (strstr(cmd, "\"test_pattern\"")) {
    testPattern();
    Serial.println("{\"type\":\"ack\",\"cmd\":\"test_pattern\"}");
  }
}

void testPattern() {
  // Red chase
  for (int i = 0; i < NUM_LEDS; i++) {
    FastLED.clear();
    leds[i] = CRGB::Red;
    FastLED.show();
    delay(50);
  }
  
  // Green
  fill_solid(leds, NUM_LEDS, CRGB::Green);
  FastLED.show();
  delay(300);
  
  // Blue
  fill_solid(leds, NUM_LEDS, CRGB::Blue);
  FastLED.show();
  delay(300);
  
  FastLED.clear();
  FastLED.show();
}
