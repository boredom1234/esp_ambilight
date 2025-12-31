#include <WiFi.h>
#include <WebSocketsServer.h>
#include <ESPAsyncWebServer.h>
#include <FastLED.h>
#include <Preferences.h>
#include <ArduinoJson.h>

// --- Configuration Constants ---
#define LED_PIN     5
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB
#define MAX_LEDS    300       // Maximum possible LEDs (memory limit)
#define AP_SSID     "ESP32-Ambilight-Config"
#define AP_PASS     ""        // Open AP for configuration

// --- Global Variables ---
CRGB leds[MAX_LEDS];
int numLedsConfig = 60;       // Current active LED count
String wifi_ssid = "";
String wifi_pass = "";

// State
bool calibrationMode = false;
int highlightLED = -1;
uint8_t currentBrightness = 255;

// Server instances
AsyncWebServer server(80);
WebSocketsServer webSocket(81);
Preferences preferences;

// LED mapping
struct LEDMapping {
  uint8_t screenX;
  uint8_t screenY;
};
LEDMapping ledMap[MAX_LEDS];

// Function Prototypes
void loadSettings();
void saveSettings(); 
void setupWebServer();
void startAPMode();

void setup() {
  Serial.begin(115200);
  
  // Initialize Preferences
  preferences.begin("ambilight", false);
  loadSettings();
  loadLEDMapping(); // Load mapping separately
  
  // Initialize LED strip
  // Note: We initialize to MAX_LEDS, but only draw up to numLedsConfig
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, MAX_LEDS);
  FastLED.setBrightness(currentBrightness);
  FastLED.clear();
  FastLED.show();
  
  // WiFi Connection Strategy
  if (wifi_ssid == "") {
    Serial.println("No WiFi Configured. Starting AP Mode.");
    startAPMode();
  } else {
    Serial.print("Connecting to WiFi: ");
    Serial.println(wifi_ssid);
    WiFi.begin(wifi_ssid.c_str(), wifi_pass.c_str());
    
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
    } else {
      Serial.println("\nConnection Failed. Starting AP Mode.");
      startAPMode();
    }
  }
  
  // Setup Services
  webSocket.begin();
  webSocket.onEvent(webSocketEvent);
  
  setupWebServer();
  
  // Startup Animation (only on active LEDs)
  startupAnimation();
  
  Serial.println("ESP32 Ambilight Ready!");
}

void loop() {
  webSocket.loop();
  
  // Calibration Blink Logic
  if (calibrationMode && highlightLED >= 0 && highlightLED < numLedsConfig) {
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

// --- Configuration Persistence ---

void loadSettings() {
  wifi_ssid = preferences.getString("ssid", "");
  wifi_pass = preferences.getString("pass", "");
  numLedsConfig = preferences.getInt("leds", 60);
  
  if (numLedsConfig > MAX_LEDS) numLedsConfig = MAX_LEDS;
  if (numLedsConfig < 1) numLedsConfig = 1; # Safetyl
  
  Serial.printf("Loaded Settings: SSID='%s', LEDs=%d\n", wifi_ssid.c_str(), numLedsConfig);
}

void saveConfig(String newSsid, String newPass, int newLeds) {
  preferences.putString("ssid", newSsid);
  preferences.putString("pass", newPass);
  preferences.putInt("leds", newLeds);
  Serial.println("Settings saved to preferences.");
}

void saveLEDMapping() {
  for (int i = 0; i < MAX_LEDS; i++) {
    String key = "l" + String(i); // Short key to save space
    uint16_t value = (ledMap[i].screenX << 8) | ledMap[i].screenY;
    preferences.putUShort(key.c_str(), value);
  }
}

void loadLEDMapping() {
  for (int i = 0; i < MAX_LEDS; i++) {
    String key = "l" + String(i);
    uint16_t value = preferences.getUShort(key.c_str(), 0);
    ledMap[i].screenX = (value >> 8) & 0xFF;
    ledMap[i].screenY = value & 0xFF;
  }
}

void startAPMode() {
  WiFi.softAP(AP_SSID, AP_PASS);
  Serial.println("AP Mode Started");
  Serial.print("AP IP: ");
  Serial.println(WiFi.softAPIP());
}

// --- Web Server ---

void setupWebServer() {
  // Main Config Page
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request){
    String html = "<!DOCTYPE html><html><head><title>ESP32 Setup</title>";
    html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
    html += "<style>body{font-family:Arial;margin:20px;background:#222;color:#eee;}";
    html += "input,button{display:block;width:100%;padding:10px;margin:10px 0;box-sizing:border-box;}";
    html += "button{background:#00ff88;border:none;cursor:pointer;color:#000;font-weight:bold;}";
    html += ".card{background:#333;padding:20px;border-radius:8px;}";
    html += "h1{color:#00ff88;}</style></head><body>";
    
    html += "<div class='card'><h1>⚙️ Device Setup</h1>";
    
    // Status Info
    html += "<p>Mode: <strong>" + String(WiFi.getMode() == WIFI_AP ? "Access Point" : "Station") + "</strong></p>";
    if (WiFi.getMode() == WIFI_STA) {
       html += "<p>IP: " + WiFi.localIP().toString() + "</p>";
    }
    
    // Form
    html += "<form action='/save' method='POST'>";
    html += "<label>WiFi SSID</label>";
    html += "<input type='text' name='ssid' value='" + wifi_ssid + "' placeholder='SSID'>";
    
    html += "<label>WiFi Password</label>";
    html += "<input type='password' name='pass' value='" + wifi_pass + "' placeholder='Password'>";
    
    html += "<label>Number of LEDs (Max " + String(MAX_LEDS) + ")</label>";
    html += "<input type='number' name='leds' value='" + String(numLedsConfig) + "' min='1' max='" + String(MAX_LEDS) + "'>";
    
    html += "<button type='submit'>Save & Restart</button>";
    html += "</form></div>";
    
    html += "<br><div class='card'><h3>WebSocket Info</h3>";
    html += "<p>Port: 81</p><p>Status: " + String(webSocket.connectedClients() > 0 ? "Client Connected" : "Idle") + "</p></div>";
    
    html += "</body></html>";
    request->send(200, "text/html", html);
  });

  // Save Endpoint
  server.on("/save", HTTP_POST, [](AsyncWebServerRequest *request){
    String s, p, l;
    if (request->hasParam("ssid", true)) s = request->getParam("ssid", true)->value();
    if (request->hasParam("pass", true)) p = request->getParam("pass", true)->value();
    if (request->hasParam("leds", true)) l = request->getParam("leds", true)->value();
    
    saveConfig(s, p, l.toInt());
    
    request->send(200, "text/html", "<h1>Saved! Restarting...</h1><script>setTimeout(function(){window.location.href='/';}, 5000);</script>");
    
    delay(1000);
    ESP.restart();
  });
  
  server.begin();
}

// --- WebSocket Logic ---

void webSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.printf("[%u] Disconnected!\n", num);
      break;
      
    case WStype_CONNECTED: {
      IPAddress ip = webSocket.remoteIP(num);
      Serial.printf("[%u] Connected from %d.%d.%d.%d\n", num, ip[0], ip[1], ip[2], ip[3]);
      
      // Send LED count
      StaticJsonDocument<128> doc;
      doc["type"] = "info";
      doc["ledCount"] = numLedsConfig;
      String response;
      serializeJson(doc, response);
      webSocket.sendTXT(num, response);
      break;
    }
      
    case WStype_TEXT: {
      StaticJsonDocument<512> doc; // Increased to 512 for larger mappings
      DeserializationError error = deserializeJson(doc, payload);
      
      if (error) return;
      
      String cmd = doc["cmd"];
      
      if (cmd == "calibrate_start") {
        calibrationMode = true;
        highlightLED = -1;
        webSocket.sendTXT(num, "{\"type\":\"ack\",\"cmd\":\"calibrate_start\"}");
      }
      else if (cmd == "calibrate_end") {
        calibrationMode = false;
        highlightLED = -1;
        FastLED.clear();
        FastLED.show();
        webSocket.sendTXT(num, "{\"type\":\"ack\",\"cmd\":\"calibrate_end\"}");
      }
      else if (cmd == "highlight") {
        highlightLED = doc["led"];
      }
      else if (cmd == "save_map") {
        JsonArray mapping = doc["mapping"];
        // Only save what we received, up to limit
        for (int i = 0; i < numLedsConfig && i < mapping.size(); i++) {
          ledMap[i].screenX = mapping[i]["x"];
          ledMap[i].screenY = mapping[i]["y"];
        }
        saveLEDMapping();
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
      if (!calibrationMode && length >= numLedsConfig * 3) {
        for (int i = 0; i < numLedsConfig; i++) {
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

void testPattern() {
  // Test only active LEDs
  for (int i = 0; i < numLedsConfig; i++) {
    FastLED.clear();
    leds[i] = CRGB::Red;
    FastLED.show();
    delay(20);
  }
  delay(100);
  FastLED.clear();
  FastLED.show();
}

void startupAnimation() {
  for (int j = 0; j < 255; j += 10) {
    for (int i = 0; i < numLedsConfig; i++) {
      leds[i] = CHSV((i * 255 / numLedsConfig + j) % 255, 255, 200);
    }
    FastLED.show();
    delay(10);
  }
  FastLED.clear();
  FastLED.show();
}