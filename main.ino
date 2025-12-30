#include <WiFi.h>
#include <WebSocketsServer.h>
#include <ESPAsyncWebServer.h>
#include <FastLED.h>
#include <Preferences.h>
#include <ArduinoJson.h>

// WiFi Configuration - CHANGE THESE!
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// LED Configuration
#define LED_PIN     5
#define NUM_LEDS    60
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB

// Server instances
AsyncWebServer server(80);
WebSocketsServer webSocket(81);
Preferences preferences;

// LED data
CRGB leds[NUM_LEDS];
bool calibrationMode = false;
int highlightLED = -1;
uint8_t currentBrightness = 255;

// LED mapping - stores normalized screen coordinates (0-255 for each axis)
// Supports any LED arrangement: strip, matrix, spiral, freeform, etc.
struct LEDMapping {
  uint8_t screenX;  // 0-255 normalized X position
  uint8_t screenY;  // 0-255 normalized Y position
};
LEDMapping ledMap[NUM_LEDS];

void setup() {
  Serial.begin(115200);
  
  // Initialize LED strip
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(currentBrightness);
  FastLED.clear();
  FastLED.show();
  
  // Initialize preferences
  preferences.begin("ambilight", false);
  loadLEDMapping();
  
  // Connect to WiFi
  Serial.println("Connecting to WiFi...");
  WiFi.begin(ssid, password);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi Connected!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
    Serial.print("WebSocket Port: 81\n");
  } else {
    Serial.println("\nWiFi Connection Failed!");
    Serial.println("Starting AP mode...");
    WiFi.softAP("ESP32-Ambilight", "12345678");
    Serial.print("AP IP: ");
    Serial.println(WiFi.softAPIP());
  }
  
  // Setup WebSocket
  webSocket.begin();
  webSocket.onEvent(webSocketEvent);
  
  // Setup Web Server
  setupWebServer();
  
  startupAnimation();
  
  Serial.println("ESP32 Ambilight Ready!");
}

void loop() {
  webSocket.loop();
  
  // Calibration mode: highlight specific LED
  if (calibrationMode && highlightLED >= 0) {
    static unsigned long lastBlink = 0;
    static bool blinkState = false;
    
    if (millis() - lastBlink > 500) {
      FastLED.clear();
      if (blinkState) {
        leds[highlightLED] = CRGB::White;
      }
      FastLED.show();
      blinkState = !blinkState;
      lastBlink = millis();
    }
  }
}

void webSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.printf("[%u] Disconnected!\n", num);
      break;
      
    case WStype_CONNECTED: {
      IPAddress ip = webSocket.remoteIP(num);
      Serial.printf("[%u] Connected from %d.%d.%d.%d\n", num, ip[0], ip[1], ip[2], ip[3]);
      
      // Send LED count on connection
      StaticJsonDocument<64> doc;
      doc["type"] = "info";
      doc["ledCount"] = NUM_LEDS;
      String response;
      serializeJson(doc, response);
      webSocket.sendTXT(num, response);
      break;
    }
      
    case WStype_TEXT: {
      // Handle JSON commands
      StaticJsonDocument<256> doc;
      DeserializationError error = deserializeJson(doc, payload);
      
      if (error) {
        Serial.println("JSON parse error");
        return;
      }
      
      String cmd = doc["cmd"];
      
      if (cmd == "calibrate_start") {
        calibrationMode = true;
        highlightLED = -1;
        Serial.println("Calibration mode started");
        webSocket.sendTXT(num, "{\"type\":\"ack\",\"cmd\":\"calibrate_start\"}");
      }
      else if (cmd == "calibrate_end") {
        calibrationMode = false;
        highlightLED = -1;
        FastLED.clear();
        FastLED.show();
        Serial.println("Calibration mode ended");
        webSocket.sendTXT(num, "{\"type\":\"ack\",\"cmd\":\"calibrate_end\"}");
      }
      else if (cmd == "highlight") {
        highlightLED = doc["led"];
        Serial.printf("Highlighting LED %d\n", highlightLED);
      }
      else if (cmd == "save_map") {
        JsonArray mapping = doc["mapping"];
        for (int i = 0; i < NUM_LEDS && i < mapping.size(); i++) {
          ledMap[i].screenX = mapping[i]["x"];
          ledMap[i].screenY = mapping[i]["y"];
        }
        saveLEDMapping();
        Serial.println("LED mapping saved!");
        webSocket.sendTXT(num, "{\"type\":\"ack\",\"cmd\":\"save_map\"}");
      }
      else if (cmd == "test_pattern") {
        testPattern();
        webSocket.sendTXT(num, "{\"type\":\"ack\",\"cmd\":\"test_pattern\"}");
      }
      else if (cmd == "brightness") {
        currentBrightness = doc["value"];
        FastLED.setBrightness(currentBrightness);
        FastLED.show();
      }
      else if (cmd == "clear") {
        FastLED.clear();
        FastLED.show();
      }
      
      break;
    }
      
    case WStype_BIN: {
      // Binary data: LED color data
      // Format: [R1,G1,B1,R2,G2,B2,...]
      if (!calibrationMode && length >= NUM_LEDS * 3) {
        for (int i = 0; i < NUM_LEDS; i++) {
          int idx = i * 3;
          leds[i].r = payload[idx];
          leds[i].g = payload[idx + 1];
          leds[i].b = payload[idx + 2];
        }
        FastLED.show();
      }
      break;
    }
  }
}

void setupWebServer() {
  // Serve a simple web page for status
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request){
    String html = "<!DOCTYPE html><html><head><title>ESP32 Ambilight</title>";
    html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
    html += "<style>body{font-family:Arial;margin:40px;background:#1a1a1a;color:#fff;}";
    html += "h1{color:#00ff88;}.info{background:#2a2a2a;padding:20px;border-radius:10px;margin:20px 0;}";
    html += ".status{color:#00ff88;font-weight:bold;}</style></head><body>";
    html += "<h1>🎨 ESP32 Ambilight</h1>";
    html += "<div class='info'>";
    html += "<p><strong>Status:</strong> <span class='status'>Online</span></p>";
    html += "<p><strong>IP Address:</strong> " + WiFi.localIP().toString() + "</p>";
    html += "<p><strong>WebSocket Port:</strong> 81</p>";
    html += "<p><strong>LED Count:</strong> " + String(NUM_LEDS) + "</p>";
    html += "<p><strong>WiFi Signal:</strong> " + String(WiFi.RSSI()) + " dBm</p>";
    html += "</div>";
    html += "<p>Use the desktop application to control this device.</p>";
    html += "<p><small>WebSocket URL: ws://" + WiFi.localIP().toString() + ":81</small></p>";
    html += "</body></html>";
    request->send(200, "text/html", html);
  });
  
  server.begin();
  Serial.println("Web server started");
}

void saveLEDMapping() {
  for (int i = 0; i < NUM_LEDS; i++) {
    String key = "led" + String(i);
    // Pack X (high byte) and Y (low byte) into 16 bits
    uint16_t value = (ledMap[i].screenX << 8) | ledMap[i].screenY;
    preferences.putUShort(key.c_str(), value);
  }
}

void loadLEDMapping() {
  for (int i = 0; i < NUM_LEDS; i++) {
    String key = "led" + String(i);
    uint16_t value = preferences.getUShort(key.c_str(), 0);
    ledMap[i].screenX = (value >> 8) & 0xFF;
    ledMap[i].screenY = value & 0xFF;
  }
}

void testPattern() {
  for (int i = 0; i < NUM_LEDS; i++) {
    FastLED.clear();
    leds[i] = CRGB::Red;
    FastLED.show();
    delay(30);
  }
  delay(200);
  FastLED.clear();
  FastLED.show();
}

void startupAnimation() {
  // Rainbow wave
  for (int j = 0; j < 255; j += 5) {
    for (int i = 0; i < NUM_LEDS; i++) {
      leds[i] = CHSV((i * 255 / NUM_LEDS + j) % 255, 255, 200);
    }
    FastLED.show();
    delay(10);
  }
  FastLED.clear();
  FastLED.show();
}