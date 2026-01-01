/*
 * ESP32 Multi-Mode Ambilight Controller
 * 
 * Supports receiving LED data via:
 * - USB Serial (115200 baud)
 * - WebSocket (port 81)
 * - Bluetooth Classic SPP
 * 
 * All settings configurable via internal AP web interface.
 * 
 * Hardware:
 * - ESP32 Dev Board
 * - WS2812B LED Strip
 * 
 * USB Protocol (Binary Frame):
 * - Byte 0: 0xAD (magic start byte)
 * - Byte 1: 0xDA (sync byte)
 * - Bytes 2-N: LED_COUNT * 3 bytes (R,G,B for each LED)
 * - Last Byte: checksum (XOR of all RGB bytes)
 * 
 * WebSocket: Same as existing main.ino (JSON commands + binary RGB data)
 * Bluetooth: Same binary protocol as USB
 */

#include <WiFi.h>
#include <WebSocketsServer.h>
#include <ESPAsyncWebServer.h>
#include <FastLED.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <BluetoothSerial.h>

// ============================================================================
// CONFIGURATION CONSTANTS
// ============================================================================

#define LED_PIN         5           // GPIO for LED data
#define LED_TYPE        WS2812B
#define COLOR_ORDER     GRB
#define MAX_LEDS        300         // Maximum possible LEDs (memory limit)

// Protocol constants
#define MAGIC_BYTE_1    0xAD        // Start of binary frame
#define MAGIC_BYTE_2    0xDA        // Sync confirmation
#define SERIAL_BAUD     115200

// AP Configuration
#define AP_SSID         "ESP32-Ambilight"
#define DEFAULT_AP_PASS ""          // Empty = open, can be changed in settings

// ============================================================================
// GLOBAL VARIABLES
// ============================================================================

// LED Strip
CRGB leds[MAX_LEDS];
int numLeds = 60;                   // Current active LED count

// Configuration (stored in NVS)
String wifiSsid = "";
String wifiPass = "";
String apPassword = "";
String btName = "ESP32-Ambilight";
uint8_t defaultBrightness = 255;
uint8_t colorOrder = 0;             // 0=GRB, 1=RGB, 2=BGR, 3=RBG, 4=BRG, 5=GBR

// Connection mode flags (which sources are enabled)
bool enableUsb = true;
bool enableWebSocket = true;
bool enableBluetooth = true;

// Runtime state
bool calibrationMode = false;
int highlightLED = -1;
uint8_t currentBrightness = 255;
bool wifiConnected = false;
bool btConnected = false;

// Server instances
AsyncWebServer webServer(80);
WebSocketsServer wsServer(81);
BluetoothSerial btSerial;
Preferences prefs;

// LED screen mapping storage
struct LEDMapping {
    uint8_t screenX;
    uint8_t screenY;
};
LEDMapping ledMap[MAX_LEDS];

// Serial protocol state machine
int serialSyncState = 0;            // 0=idle, 1=got 0xAD, 2=got 0xDA, 3=reading RGB
int serialBufferIndex = 0;
uint8_t serialRgbBuffer[MAX_LEDS * 3];
unsigned long lastSerialByte = 0;
const unsigned long SERIAL_TIMEOUT_MS = 50;

// Bluetooth protocol state machine (same as serial)
int btSyncState = 0;
int btBufferIndex = 0;
uint8_t btRgbBuffer[MAX_LEDS * 3];
unsigned long lastBtByte = 0;

// Signal loss detection
unsigned long lastValidFrame = 0;
const unsigned long SIGNAL_TIMEOUT_MS = 3000;  // 3 seconds without data = turn off
bool ledsActive = false;
String lastActiveSource = "";

// ============================================================================
// FUNCTION PROTOTYPES
// ============================================================================

void loadSettings();
void saveSettings();
void saveLEDMapping();
void loadLEDMapping();
void setupWebServer();
void startAPMode();
void handleSerialData();
void handleBluetoothData();
void webSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length);
void applyLedColors(uint8_t* rgbData, int dataLen, const char* source);
void testPattern();
void startupAnimation();
String getConfigPageHtml();

// ============================================================================
// SETUP
// ============================================================================

void setup() {
    Serial.begin(SERIAL_BAUD);
    Serial.println("\n=== ESP32 Multi-Mode Ambilight ===");
    
    // Initialize Preferences (NVS storage)
    prefs.begin("ambilight", false);
    loadSettings();
    loadLEDMapping();
    
    // Initialize LED strip
    FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, MAX_LEDS);
    FastLED.setBrightness(currentBrightness);
    FastLED.clear();
    FastLED.show();
    
    // WiFi Connection Strategy
    if (wifiSsid.length() > 0) {
        Serial.printf("Connecting to WiFi: %s\n", wifiSsid.c_str());
        WiFi.mode(WIFI_STA);
        WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
        
        int attempts = 0;
        while (WiFi.status() != WL_CONNECTED && attempts < 20) {
            delay(500);
            Serial.print(".");
            attempts++;
        }
        
        if (WiFi.status() == WL_CONNECTED) {
            wifiConnected = true;
            Serial.printf("\nWiFi Connected! IP: %s\n", WiFi.localIP().toString().c_str());
            
            // Also start AP for configuration access
            WiFi.softAP(AP_SSID, apPassword.c_str());
            Serial.printf("Config AP also available at: %s\n", WiFi.softAPIP().toString().c_str());
        } else {
            Serial.println("\nWiFi connection failed. Starting AP mode only.");
            startAPMode();
        }
    } else {
        Serial.println("No WiFi configured. Starting AP mode.");
        startAPMode();
    }
    
    // Initialize Bluetooth if enabled
    if (enableBluetooth) {
        if (btSerial.begin(btName)) {
            Serial.printf("Bluetooth started: %s\n", btName.c_str());
        } else {
            Serial.println("Bluetooth initialization failed!");
            enableBluetooth = false;
        }
    }
    
    // Start WebSocket server if enabled
    if (enableWebSocket) {
        wsServer.begin();
        wsServer.onEvent(webSocketEvent);
        Serial.println("WebSocket server started on port 81");
    }
    
    // Setup web configuration server
    setupWebServer();
    
    // Startup animation
    startupAnimation();
    
    Serial.println("\n=== Ready ===");
    Serial.printf("USB: %s | WebSocket: %s | Bluetooth: %s\n",
        enableUsb ? "ON" : "OFF",
        enableWebSocket ? "ON" : "OFF",
        enableBluetooth ? "ON" : "OFF");
}

// ============================================================================
// MAIN LOOP
// ============================================================================

void loop() {
    unsigned long currentTime = millis();
    
    // Handle WebSocket events
    if (enableWebSocket) {
        wsServer.loop();
    }
    
    // Handle USB Serial data
    if (enableUsb) {
        handleSerialData();
    }
    
    // Handle Bluetooth data
    if (enableBluetooth && btSerial.hasClient()) {
        handleBluetoothData();
    }
    
    // Calibration blink logic
    if (calibrationMode && highlightLED >= 0 && highlightLED < numLeds) {
        static unsigned long lastBlink = 0;
        static bool blinkState = false;
        
        if (currentTime - lastBlink > 500) {
            FastLED.clear();
            if (blinkState) {
                leds[highlightLED] = CRGB::White;
            }
            FastLED.show();
            blinkState = !blinkState;
            lastBlink = currentTime;
        }
    }
    
    // Signal loss detection - turn off LEDs if no data received for a while
    if (ledsActive && (currentTime - lastValidFrame > SIGNAL_TIMEOUT_MS)) {
        FastLED.clear();
        FastLED.show();
        ledsActive = false;
        Serial.printf("Signal lost from %s - LEDs off\n", lastActiveSource.c_str());
    }
    
    // Small delay to prevent watchdog issues
    delay(1);
}

// ============================================================================
// SERIAL (USB) DATA HANDLING
// ============================================================================

void handleSerialData() {
    unsigned long currentTime = millis();
    
    // Timeout - reset state machine if data stream interrupted
    if (serialSyncState > 0 && (currentTime - lastSerialByte > SERIAL_TIMEOUT_MS)) {
        serialSyncState = 0;
        serialBufferIndex = 0;
    }
    
    while (Serial.available() > 0) {
        uint8_t b = Serial.read();
        lastSerialByte = currentTime;
        
        switch (serialSyncState) {
            case 0:  // Waiting for start byte
                if (b == MAGIC_BYTE_1) {
                    serialSyncState = 1;
                } else if (b == '{') {
                    // JSON command - read until '}'
                    String cmd = "{";
                    unsigned long jsonStart = millis();
                    while (millis() - jsonStart < 100) {
                        if (Serial.available()) {
                            char c = Serial.read();
                            cmd += c;
                            if (c == '}') break;
                        }
                    }
                    processJsonCommand(cmd, "USB");
                }
                break;
                
            case 1:  // Got 0xAD, waiting for 0xDA
                if (b == MAGIC_BYTE_2) {
                    serialSyncState = 2;
                    serialBufferIndex = 0;
                } else {
                    serialSyncState = 0;  // False start, reset
                }
                break;
                
            case 2:  // Reading RGB data
                serialRgbBuffer[serialBufferIndex++] = b;
                if (serialBufferIndex >= numLeds * 3) {
                    serialSyncState = 3;  // Move to checksum verification
                }
                break;
                
            case 3:  // Verify checksum
                {
                    uint8_t checksum = 0;
                    for (int i = 0; i < numLeds * 3; i++) {
                        checksum ^= serialRgbBuffer[i];
                    }
                    
                    if (checksum == b) {
                        applyLedColors(serialRgbBuffer, numLeds * 3, "USB");
                    }
                    // else: checksum mismatch, discard frame
                    
                    serialSyncState = 0;
                    serialBufferIndex = 0;
                }
                break;
        }
    }
}

// ============================================================================
// BLUETOOTH DATA HANDLING
// ============================================================================

void handleBluetoothData() {
    unsigned long currentTime = millis();
    
    // Timeout - reset state machine
    if (btSyncState > 0 && (currentTime - lastBtByte > SERIAL_TIMEOUT_MS)) {
        btSyncState = 0;
        btBufferIndex = 0;
    }
    
    while (btSerial.available() > 0) {
        uint8_t b = btSerial.read();
        lastBtByte = currentTime;
        
        switch (btSyncState) {
            case 0:  // Waiting for start byte
                if (b == MAGIC_BYTE_1) {
                    btSyncState = 1;
                } else if (b == '{') {
                    // JSON command
                    String cmd = "{";
                    unsigned long jsonStart = millis();
                    while (millis() - jsonStart < 100) {
                        if (btSerial.available()) {
                            char c = btSerial.read();
                            cmd += c;
                            if (c == '}') break;
                        }
                    }
                    processJsonCommand(cmd, "Bluetooth");
                }
                break;
                
            case 1:  // Got 0xAD, waiting for 0xDA
                if (b == MAGIC_BYTE_2) {
                    btSyncState = 2;
                    btBufferIndex = 0;
                } else {
                    btSyncState = 0;
                }
                break;
                
            case 2:  // Reading RGB data
                btRgbBuffer[btBufferIndex++] = b;
                if (btBufferIndex >= numLeds * 3) {
                    btSyncState = 3;
                }
                break;
                
            case 3:  // Verify checksum
                {
                    uint8_t checksum = 0;
                    for (int i = 0; i < numLeds * 3; i++) {
                        checksum ^= btRgbBuffer[i];
                    }
                    
                    if (checksum == b) {
                        applyLedColors(btRgbBuffer, numLeds * 3, "Bluetooth");
                    }
                    
                    btSyncState = 0;
                    btBufferIndex = 0;
                }
                break;
        }
    }
}

// ============================================================================
// WEBSOCKET EVENT HANDLING
// ============================================================================

void webSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_DISCONNECTED:
            Serial.printf("[WS:%u] Disconnected\n", num);
            break;
            
        case WStype_CONNECTED:
            {
                IPAddress ip = wsServer.remoteIP(num);
                Serial.printf("[WS:%u] Connected from %s\n", num, ip.toString().c_str());
                
                // Send device info
                StaticJsonDocument<256> doc;
                doc["type"] = "info";
                doc["ledCount"] = numLeds;
                doc["brightness"] = currentBrightness;
                doc["usbEnabled"] = enableUsb;
                doc["wsEnabled"] = enableWebSocket;
                doc["btEnabled"] = enableBluetooth;
                
                String response;
                serializeJson(doc, response);
                wsServer.sendTXT(num, response);
            }
            break;
            
        case WStype_TEXT:
            {
                String cmd((char*)payload);
                processJsonCommand(cmd, "WebSocket", num);
            }
            break;
            
        case WStype_BIN:
            // Binary RGB data (no checksum for WebSocket - it has its own integrity)
            if (!calibrationMode && length >= (size_t)(numLeds * 3)) {
                applyLedColors(payload, length, "WebSocket");
            }
            break;
    }
}

// ============================================================================
// JSON COMMAND PROCESSING
// ============================================================================

void processJsonCommand(String& cmdStr, const char* source, int wsNum) {
    StaticJsonDocument<1024> doc;
    DeserializationError error = deserializeJson(doc, cmdStr);
    
    if (error) {
        Serial.printf("[%s] JSON parse error: %s\n", source, error.c_str());
        return;
    }
    
    String cmd = doc["cmd"] | "";
    
    if (cmd == "info") {
        // Request device info
        StaticJsonDocument<256> resp;
        resp["type"] = "info";
        resp["ledCount"] = numLeds;
        resp["brightness"] = currentBrightness;
        
        String response;
        serializeJson(resp, response);
        
        if (strcmp(source, "WebSocket") == 0 && wsNum >= 0) {
            wsServer.sendTXT(wsNum, response);
        } else if (strcmp(source, "USB") == 0) {
            Serial.println(response);
        } else if (strcmp(source, "Bluetooth") == 0) {
            btSerial.println(response);
        }
    }
    else if (cmd == "calibrate_start") {
        calibrationMode = true;
        highlightLED = -1;
        sendAck(source, "calibrate_start", wsNum);
        Serial.printf("[%s] Calibration started\n", source);
    }
    else if (cmd == "calibrate_end") {
        calibrationMode = false;
        highlightLED = -1;
        FastLED.clear();
        FastLED.show();
        sendAck(source, "calibrate_end", wsNum);
        Serial.printf("[%s] Calibration ended\n", source);
    }
    else if (cmd == "highlight") {
        highlightLED = doc["led"] | -1;
    }
    else if (cmd == "save_map") {
        JsonArray mapping = doc["mapping"];
        if (mapping) {
            for (int i = 0; i < numLeds && i < (int)mapping.size(); i++) {
                ledMap[i].screenX = mapping[i]["x"] | 0;
                ledMap[i].screenY = mapping[i]["y"] | 0;
            }
            saveLEDMapping();
            sendAck(source, "save_map", wsNum);
            Serial.printf("[%s] LED mapping saved\n", source);
        }
    }
    else if (cmd == "test_pattern") {
        testPattern();
        sendAck(source, "test_pattern", wsNum);
    }
    else if (cmd == "brightness") {
        currentBrightness = doc["value"] | 255;
        FastLED.setBrightness(currentBrightness);
        FastLED.show();
    }
    else if (cmd == "clear") {
        FastLED.clear();
        FastLED.show();
        ledsActive = false;
        sendAck(source, "clear", wsNum);
    }
}

// Helper to send acknowledgment
void sendAck(const char* source, const char* cmd, int wsNum) {
    StaticJsonDocument<128> doc;
    doc["type"] = "ack";
    doc["cmd"] = cmd;
    
    String response;
    serializeJson(doc, response);
    
    if (strcmp(source, "WebSocket") == 0 && wsNum >= 0) {
        wsServer.sendTXT(wsNum, response);
    } else if (strcmp(source, "USB") == 0) {
        Serial.println(response);
    } else if (strcmp(source, "Bluetooth") == 0) {
        btSerial.println(response);
    }
}

// ============================================================================
// LED COLOR APPLICATION
// ============================================================================

void applyLedColors(uint8_t* rgbData, int dataLen, const char* source) {
    if (calibrationMode) return;  // Don't update during calibration
    
    int ledCount = dataLen / 3;
    if (ledCount > numLeds) ledCount = numLeds;
    
    for (int i = 0; i < ledCount; i++) {
        int idx = i * 3;
        leds[i].r = rgbData[idx];
        leds[i].g = rgbData[idx + 1];
        leds[i].b = rgbData[idx + 2];
    }
    
    FastLED.show();
    lastValidFrame = millis();
    ledsActive = true;
    lastActiveSource = source;
}

// ============================================================================
// SETTINGS PERSISTENCE
// ============================================================================

void loadSettings() {
    wifiSsid = prefs.getString("ssid", "");
    wifiPass = prefs.getString("pass", "");
    apPassword = prefs.getString("appass", "");
    btName = prefs.getString("btname", "ESP32-Ambilight");
    numLeds = prefs.getInt("leds", 60);
    defaultBrightness = prefs.getUChar("bright", 255);
    colorOrder = prefs.getUChar("order", 0);
    enableUsb = prefs.getBool("usb", true);
    enableWebSocket = prefs.getBool("ws", true);
    enableBluetooth = prefs.getBool("bt", true);
    
    // Validate
    if (numLeds > MAX_LEDS) numLeds = MAX_LEDS;
    if (numLeds < 1) numLeds = 1;
    
    currentBrightness = defaultBrightness;
    
    Serial.printf("Settings: SSID='%s', LEDs=%d, USB=%d, WS=%d, BT=%d\n",
        wifiSsid.c_str(), numLeds, enableUsb, enableWebSocket, enableBluetooth);
}

void saveSettings() {
    prefs.putString("ssid", wifiSsid);
    prefs.putString("pass", wifiPass);
    prefs.putString("appass", apPassword);
    prefs.putString("btname", btName);
    prefs.putInt("leds", numLeds);
    prefs.putUChar("bright", defaultBrightness);
    prefs.putUChar("order", colorOrder);
    prefs.putBool("usb", enableUsb);
    prefs.putBool("ws", enableWebSocket);
    prefs.putBool("bt", enableBluetooth);
    
    Serial.println("Settings saved to NVS");
}

void saveLEDMapping() {
    // Store LED mappings as packed bytes
    for (int i = 0; i < numLeds && i < MAX_LEDS; i++) {
        String key = "m" + String(i);
        uint16_t value = ((uint16_t)ledMap[i].screenX << 8) | ledMap[i].screenY;
        prefs.putUShort(key.c_str(), value);
    }
    Serial.printf("LED mapping saved (%d LEDs)\n", numLeds);
}

void loadLEDMapping() {
    for (int i = 0; i < MAX_LEDS; i++) {
        String key = "m" + String(i);
        uint16_t value = prefs.getUShort(key.c_str(), 0);
        ledMap[i].screenX = (value >> 8) & 0xFF;
        ledMap[i].screenY = value & 0xFF;
    }
}

// ============================================================================
// ACCESS POINT MODE
// ============================================================================

void startAPMode() {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, apPassword.c_str());
    Serial.printf("AP Mode Started: %s\n", AP_SSID);
    Serial.printf("Config URL: http://%s\n", WiFi.softAPIP().toString().c_str());
}

// ============================================================================
// WEB CONFIGURATION SERVER
// ============================================================================

void setupWebServer() {
    // Main configuration page
    webServer.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", getConfigPageHtml());
    });
    
    // API: Get current config as JSON
    webServer.on("/api/config", HTTP_GET, [](AsyncWebServerRequest *request) {
        StaticJsonDocument<512> doc;
        doc["ssid"] = wifiSsid;
        doc["leds"] = numLeds;
        doc["brightness"] = defaultBrightness;
        doc["btName"] = btName;
        doc["enableUsb"] = enableUsb;
        doc["enableWs"] = enableWebSocket;
        doc["enableBt"] = enableBluetooth;
        doc["apPassword"] = apPassword.length() > 0 ? "****" : "";
        doc["wifiConnected"] = wifiConnected;
        doc["ip"] = wifiConnected ? WiFi.localIP().toString() : WiFi.softAPIP().toString();
        
        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });
    
    // API: Save configuration
    webServer.on("/api/save", HTTP_POST, [](AsyncWebServerRequest *request) {
        bool needsRestart = false;
        
        // WiFi settings
        if (request->hasParam("ssid", true)) {
            String newSsid = request->getParam("ssid", true)->value();
            if (newSsid != wifiSsid) {
                wifiSsid = newSsid;
                needsRestart = true;
            }
        }
        if (request->hasParam("pass", true)) {
            String newPass = request->getParam("pass", true)->value();
            if (newPass.length() > 0 && newPass != "****") {
                wifiPass = newPass;
                needsRestart = true;
            }
        }
        if (request->hasParam("appass", true)) {
            String newApPass = request->getParam("appass", true)->value();
            if (newApPass != "****" && newApPass != apPassword) {
                apPassword = newApPass;
                needsRestart = true;
            }
        }
        
        // LED settings
        if (request->hasParam("leds", true)) {
            int newLeds = request->getParam("leds", true)->value().toInt();
            if (newLeds >= 1 && newLeds <= MAX_LEDS) {
                numLeds = newLeds;
            }
        }
        if (request->hasParam("brightness", true)) {
            defaultBrightness = request->getParam("brightness", true)->value().toInt();
            currentBrightness = defaultBrightness;
            FastLED.setBrightness(currentBrightness);
        }
        
        // Bluetooth settings
        if (request->hasParam("btname", true)) {
            String newBtName = request->getParam("btname", true)->value();
            if (newBtName.length() > 0 && newBtName != btName) {
                btName = newBtName;
                needsRestart = true;  // BT name change requires restart
            }
        }
        
        // Connection mode settings
        if (request->hasParam("enableUsb", true)) {
            enableUsb = request->getParam("enableUsb", true)->value() == "1";
        }
        if (request->hasParam("enableWs", true)) {
            enableWebSocket = request->getParam("enableWs", true)->value() == "1";
        }
        if (request->hasParam("enableBt", true)) {
            bool newBt = request->getParam("enableBt", true)->value() == "1";
            if (newBt != enableBluetooth) {
                enableBluetooth = newBt;
                needsRestart = true;  // BT enable/disable requires restart
            }
        }
        
        saveSettings();
        
        StaticJsonDocument<128> doc;
        doc["success"] = true;
        doc["needsRestart"] = needsRestart;
        
        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });
    
    // API: Restart device
    webServer.on("/api/restart", HTTP_POST, [](AsyncWebServerRequest *request) {
        request->send(200, "application/json", "{\"success\":true}");
        delay(500);
        ESP.restart();
    });
    
    // API: Test LEDs
    webServer.on("/api/test", HTTP_POST, [](AsyncWebServerRequest *request) {
        testPattern();
        request->send(200, "application/json", "{\"success\":true}");
    });
    
    // API: Device status
    webServer.on("/api/status", HTTP_GET, [](AsyncWebServerRequest *request) {
        StaticJsonDocument<256> doc;
        doc["ledsActive"] = ledsActive;
        doc["lastSource"] = lastActiveSource;
        doc["wifiConnected"] = wifiConnected;
        doc["wsClients"] = wsServer.connectedClients();
        doc["btConnected"] = btSerial.hasClient();
        doc["uptime"] = millis() / 1000;
        
        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });
    
    webServer.begin();
    Serial.println("Web config server started on port 80");
}

// ============================================================================
// WEB UI HTML
// ============================================================================

String getConfigPageHtml() {
    String html = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <title>ESP32 Ambilight Config</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 600px; margin: 0 auto; }
        h1 { 
            color: #00ff88;
            text-align: center;
            margin-bottom: 30px;
            font-size: 1.8em;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .card h2 {
            color: #00ff88;
            font-size: 1.1em;
            margin-bottom: 15px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            padding-bottom: 10px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #aaa;
            font-size: 0.9em;
        }
        input[type="text"], input[type="password"], input[type="number"] {
            width: 100%;
            padding: 12px;
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            background: rgba(0,0,0,0.3);
            color: #fff;
            font-size: 1em;
        }
        input:focus {
            outline: none;
            border-color: #00ff88;
        }
        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 0;
        }
        .checkbox-group input[type="checkbox"] {
            width: 20px;
            height: 20px;
            accent-color: #00ff88;
        }
        .checkbox-group label {
            margin: 0;
            color: #eee;
        }
        button {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 8px;
            font-size: 1em;
            font-weight: bold;
            cursor: pointer;
            margin-top: 10px;
            transition: transform 0.1s, opacity 0.2s;
        }
        button:active {
            transform: scale(0.98);
        }
        .btn-primary {
            background: linear-gradient(135deg, #00ff88 0%, #00cc6a 100%);
            color: #000;
        }
        .btn-secondary {
            background: rgba(255,255,255,0.1);
            color: #fff;
        }
        .btn-danger {
            background: linear-gradient(135deg, #ff4444 0%, #cc0000 100%);
            color: #fff;
        }
        .status {
            text-align: center;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
        .status.connected {
            background: rgba(0,255,136,0.1);
            border: 1px solid #00ff88;
        }
        .status.disconnected {
            background: rgba(255,68,68,0.1);
            border: 1px solid #ff4444;
        }
        .status-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-dot.green { background: #00ff88; }
        .status-dot.red { background: #ff4444; }
        .status-dot.yellow { background: #ffcc00; }
        .inline-status {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .inline-status:last-child { border: none; }
        .toast {
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #333;
            color: #fff;
            padding: 12px 24px;
            border-radius: 8px;
            display: none;
            z-index: 1000;
        }
        .toast.show { display: block; animation: fadeIn 0.3s; }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateX(-50%) translateY(20px); }
            to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ ESP32 Ambilight</h1>
        
        <div id="statusBox" class="status disconnected">
            <span class="status-dot red" id="statusDot"></span>
            <span id="statusText">Loading...</span>
        </div>
        
        <div class="card">
            <h2>📡 WiFi Settings</h2>
            <div class="form-group">
                <label>WiFi SSID</label>
                <input type="text" id="ssid" placeholder="Your WiFi name">
            </div>
            <div class="form-group">
                <label>WiFi Password</label>
                <input type="password" id="pass" placeholder="WiFi password">
            </div>
            <div class="form-group">
                <label>AP Password (leave empty for open)</label>
                <input type="password" id="appass" placeholder="Config AP password">
            </div>
        </div>
        
        <div class="card">
            <h2>💡 LED Settings</h2>
            <div class="form-group">
                <label>Number of LEDs (1-300)</label>
                <input type="number" id="leds" min="1" max="300" value="60">
            </div>
            <div class="form-group">
                <label>Default Brightness (0-255)</label>
                <input type="number" id="brightness" min="0" max="255" value="255">
            </div>
        </div>
        
        <div class="card">
            <h2>🔌 Connection Modes</h2>
            <div class="checkbox-group">
                <input type="checkbox" id="enableUsb" checked>
                <label for="enableUsb">USB Serial</label>
            </div>
            <div class="checkbox-group">
                <input type="checkbox" id="enableWs" checked>
                <label for="enableWs">WebSocket (WiFi)</label>
            </div>
            <div class="checkbox-group">
                <input type="checkbox" id="enableBt" checked>
                <label for="enableBt">Bluetooth Classic</label>
            </div>
            <div class="form-group" style="margin-top:15px">
                <label>Bluetooth Device Name</label>
                <input type="text" id="btname" placeholder="ESP32-Ambilight">
            </div>
        </div>
        
        <div class="card">
            <h2>📊 Live Status</h2>
            <div id="liveStatus">
                <div class="inline-status">
                    <span>LEDs Active</span>
                    <span id="ledsActive">-</span>
                </div>
                <div class="inline-status">
                    <span>Last Source</span>
                    <span id="lastSource">-</span>
                </div>
                <div class="inline-status">
                    <span>WebSocket Clients</span>
                    <span id="wsClients">-</span>
                </div>
                <div class="inline-status">
                    <span>Bluetooth</span>
                    <span id="btStatus">-</span>
                </div>
                <div class="inline-status">
                    <span>Uptime</span>
                    <span id="uptime">-</span>
                </div>
            </div>
        </div>
        
        <button class="btn-primary" onclick="saveConfig()">💾 Save Configuration</button>
        <button class="btn-secondary" onclick="testLeds()">🎨 Test LED Pattern</button>
        <button class="btn-danger" onclick="restart()">🔄 Restart Device</button>
    </div>
    
    <div id="toast" class="toast"></div>
    
    <script>
        // Load current config on page load
        async function loadConfig() {
            try {
                const resp = await fetch('/api/config');
                const cfg = await resp.json();
                
                document.getElementById('ssid').value = cfg.ssid || '';
                document.getElementById('leds').value = cfg.leds || 60;
                document.getElementById('brightness').value = cfg.brightness || 255;
                document.getElementById('btname').value = cfg.btName || 'ESP32-Ambilight';
                document.getElementById('enableUsb').checked = cfg.enableUsb;
                document.getElementById('enableWs').checked = cfg.enableWs;
                document.getElementById('enableBt').checked = cfg.enableBt;
                
                const statusBox = document.getElementById('statusBox');
                const statusDot = document.getElementById('statusDot');
                const statusText = document.getElementById('statusText');
                
                if (cfg.wifiConnected) {
                    statusBox.className = 'status connected';
                    statusDot.className = 'status-dot green';
                    statusText.textContent = 'Connected to WiFi: ' + cfg.ip;
                } else {
                    statusBox.className = 'status disconnected';
                    statusDot.className = 'status-dot yellow';
                    statusText.textContent = 'AP Mode: ' + cfg.ip;
                }
            } catch (e) {
                showToast('Failed to load config');
            }
        }
        
        // Update live status
        async function updateStatus() {
            try {
                const resp = await fetch('/api/status');
                const status = await resp.json();
                
                document.getElementById('ledsActive').textContent = status.ledsActive ? '✅ Yes' : '❌ No';
                document.getElementById('lastSource').textContent = status.lastSource || 'None';
                document.getElementById('wsClients').textContent = status.wsClients;
                document.getElementById('btStatus').textContent = status.btConnected ? '✅ Connected' : '❌ Not connected';
                document.getElementById('uptime').textContent = formatUptime(status.uptime);
            } catch (e) {}
        }
        
        function formatUptime(seconds) {
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = seconds % 60;
            return h + 'h ' + m + 'm ' + s + 's';
        }
        
        async function saveConfig() {
            const formData = new FormData();
            formData.append('ssid', document.getElementById('ssid').value);
            formData.append('pass', document.getElementById('pass').value);
            formData.append('appass', document.getElementById('appass').value);
            formData.append('leds', document.getElementById('leds').value);
            formData.append('brightness', document.getElementById('brightness').value);
            formData.append('btname', document.getElementById('btname').value);
            formData.append('enableUsb', document.getElementById('enableUsb').checked ? '1' : '0');
            formData.append('enableWs', document.getElementById('enableWs').checked ? '1' : '0');
            formData.append('enableBt', document.getElementById('enableBt').checked ? '1' : '0');
            
            try {
                const resp = await fetch('/api/save', { method: 'POST', body: formData });
                const result = await resp.json();
                
                if (result.success) {
                    if (result.needsRestart) {
                        showToast('Saved! Restart required for some changes.');
                    } else {
                        showToast('Configuration saved!');
                    }
                }
            } catch (e) {
                showToast('Save failed!');
            }
        }
        
        async function testLeds() {
            try {
                await fetch('/api/test', { method: 'POST' });
                showToast('Running LED test...');
            } catch (e) {
                showToast('Test failed!');
            }
        }
        
        async function restart() {
            if (confirm('Restart the device?')) {
                try {
                    await fetch('/api/restart', { method: 'POST' });
                    showToast('Restarting...');
                    setTimeout(() => location.reload(), 5000);
                } catch (e) {}
            }
        }
        
        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.className = 'toast show';
            setTimeout(() => toast.className = 'toast', 3000);
        }
        
        // Initial load
        loadConfig();
        setInterval(updateStatus, 2000);
    </script>
</body>
</html>
)rawliteral";
    
    return html;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

void testPattern() {
    // Quick LED test - chase red through all LEDs
    for (int i = 0; i < numLeds; i++) {
        FastLED.clear();
        leds[i] = CRGB::Red;
        FastLED.show();
        delay(20);
    }
    
    // Flash green
    fill_solid(leds, numLeds, CRGB::Green);
    FastLED.show();
    delay(200);
    
    // Flash blue
    fill_solid(leds, numLeds, CRGB::Blue);
    FastLED.show();
    delay(200);
    
    FastLED.clear();
    FastLED.show();
}

void startupAnimation() {
    // Rainbow sweep
    for (int j = 0; j < 256; j += 8) {
        for (int i = 0; i < numLeds; i++) {
            leds[i] = CHSV((i * 255 / numLeds + j) % 256, 255, 150);
        }
        FastLED.show();
        delay(10);
    }
    
    // Fade out
    for (int b = 150; b >= 0; b -= 10) {
        FastLED.setBrightness(b);
        FastLED.show();
        delay(20);
    }
    
    FastLED.clear();
    FastLED.setBrightness(currentBrightness);
    FastLED.show();
}
