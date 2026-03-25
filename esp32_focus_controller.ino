/*
  Focus Tracker -> ESP32 -> 12V Fan + 12V LED (PWM via MOSFET)

  Endpoints:
    GET  /set?focus=75
    POST /iot/control   (JSON compatibility with existing Flask backend)
    GET  /status
    GET  /health
    OPTIONS (CORS)

  Mapping:
    fan_pwm   = map(focus, 0..100 -> 0..PWM_MAX)
    light_pwm = map(focus, 0..100 -> PWM_MAX..0)

  Smoothing:
    - EMA filter on focus input
    - Slew-rate ramp to target PWM to avoid flicker/jumps
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <math.h>
#include <stdlib.h>

// -------------------- WiFi --------------------
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// -------------------- Pins --------------------
const uint8_t PIN_FAN_PWM = 18;     // MOSFET gate for 12V fan
const uint8_t PIN_LIGHT_PWM = 19;   // MOSFET gate for 12V LED strip/light

// -------------------- LEDC PWM --------------------
const uint8_t FAN_PWM_CHANNEL = 0;
const uint8_t LIGHT_PWM_CHANNEL = 1;
const uint32_t PWM_FREQ_HZ = 25000;   // high frequency: less audible noise/flicker
const uint8_t PWM_RESOLUTION_BITS = 10; // 0..1023
const int PWM_MAX = (1 << PWM_RESOLUTION_BITS) - 1;

// Optional minimum duty to prevent fan stall when very low
const int FAN_MIN_DUTY = (int)(PWM_MAX * 0.18f);

// Smoothing settings
const float FOCUS_EMA_ALPHA = 0.25f;
const uint16_t RAMP_INTERVAL_MS = 10;
const int FAN_RAMP_STEP = 8;
const int LIGHT_RAMP_STEP = 8;

// Safety fallback if no focus updates arrive
const uint32_t OFFLINE_TIMEOUT_MS = 5000;
const int SAFE_FAN_DUTY = (int)(PWM_MAX * 0.30f);
const int SAFE_LIGHT_DUTY = (int)(PWM_MAX * 0.70f);

WebServer server(80);

int currentFocusRaw = 0;
int currentFocusFiltered = 0;
bool hasFocus = false;
float filteredFocus = 0.0f;

int targetFanDuty = 0;
int targetLightDuty = PWM_MAX;
int currentFanDuty = 0;
int currentLightDuty = PWM_MAX;

unsigned long lastFocusUpdateMs = 0;
unsigned long lastRampTickMs = 0;
unsigned long lastWifiRetryMs = 0;

void addCorsHeaders() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

void sendJson(int statusCode, const String& body) {
  addCorsHeaders();
  server.send(statusCode, "application/json", body);
}

int clampDuty(int v) {
  if (v < 0) return 0;
  if (v > PWM_MAX) return PWM_MAX;
  return v;
}

int focusToFanDuty(int focus) {
  int duty = map(focus, 0, 100, 0, PWM_MAX);
  if (duty > 0 && duty < FAN_MIN_DUTY) {
    duty = FAN_MIN_DUTY;
  }
  return clampDuty(duty);
}

int focusToLightDuty(int focus) {
  return clampDuty(map(focus, 0, 100, PWM_MAX, 0));
}

void setTargetsFromFocus(int focus) {
  int safeFocus = constrain(focus, 0, 100);
  targetFanDuty = focusToFanDuty(safeFocus);
  targetLightDuty = focusToLightDuty(safeFocus);
}

int rampToTarget(int current, int target, int step) {
  if (abs(target - current) <= step) {
    return target;
  }
  return (target > current) ? (current + step) : (current - step);
}

void applySmoothingTick() {
  unsigned long now = millis();
  if (now - lastRampTickMs < RAMP_INTERVAL_MS) {
    return;
  }
  lastRampTickMs = now;

  if (lastFocusUpdateMs == 0 || (now - lastFocusUpdateMs) > OFFLINE_TIMEOUT_MS) {
    targetFanDuty = SAFE_FAN_DUTY;
    targetLightDuty = SAFE_LIGHT_DUTY;
  }

  currentFanDuty = rampToTarget(currentFanDuty, targetFanDuty, FAN_RAMP_STEP);
  currentLightDuty = rampToTarget(currentLightDuty, targetLightDuty, LIGHT_RAMP_STEP);

  ledcWrite(FAN_PWM_CHANNEL, currentFanDuty);
  ledcWrite(LIGHT_PWM_CHANNEL, currentLightDuty);
}

bool parseFocusString(const String& value, int& outFocus) {
  if (value.length() == 0) {
    return false;
  }
  char* endPtr = nullptr;
  double parsed = strtod(value.c_str(), &endPtr);
  if (endPtr == value.c_str() || *endPtr != '\0') {
    return false;
  }
  int focus = (int)round(parsed);
  if (focus < 0 || focus > 100) {
    return false;
  }
  outFocus = focus;
  return true;
}

void updateFocusInput(int focus) {
  currentFocusRaw = constrain(focus, 0, 100);
  if (!hasFocus) {
    filteredFocus = (float)currentFocusRaw;
    hasFocus = true;
  } else {
    filteredFocus = (FOCUS_EMA_ALPHA * (float)currentFocusRaw) + ((1.0f - FOCUS_EMA_ALPHA) * filteredFocus);
  }
  currentFocusFiltered = constrain((int)round(filteredFocus), 0, 100);
  setTargetsFromFocus(currentFocusFiltered);
  lastFocusUpdateMs = millis();
}

int fanDutyFromSpeedLabel(const String& speedRaw) {
  String speed = speedRaw;
  speed.toLowerCase();
  if (speed == "off") return 0;
  if (speed == "low") return (int)(PWM_MAX * 0.35f);
  if (speed == "medium") return (int)(PWM_MAX * 0.55f);
  if (speed == "normal") return (int)(PWM_MAX * 0.70f);
  if (speed == "high") return (int)(PWM_MAX * 0.90f);
  return -1;
}

int lightDutyFromColorLabel(const String& colorRaw) {
  String color = colorRaw;
  color.toLowerCase();
  // brightness approximation for legacy color commands
  if (color == "off") return 0;
  if (color == "red") return PWM_MAX;
  if (color == "yellow") return (int)(PWM_MAX * 0.70f);
  if (color == "green") return (int)(PWM_MAX * 0.35f);
  if (color == "blue") return (int)(PWM_MAX * 0.45f);
  return -1;
}

String focusBand(int focus) {
  if (focus <= 30) return "LOW";
  if (focus <= 70) return "MEDIUM";
  return "HIGH";
}

void handleSetFocusGet() {
  if (!server.hasArg("focus")) {
    sendJson(400, "{\"ok\":false,\"error\":\"missing focus query param\"}");
    return;
  }

  int focus = 0;
  if (!parseFocusString(server.arg("focus"), focus)) {
    sendJson(400, "{\"ok\":false,\"error\":\"focus must be a number in range 0..100\"}");
    return;
  }

  updateFocusInput(focus);

  String response = "{";
  response += "\"ok\":true,";
  response += "\"focus_raw\":" + String(currentFocusRaw) + ",";
  response += "\"focus_filtered\":" + String(currentFocusFiltered) + ",";
  response += "\"focus_band\":\"" + focusBand(currentFocusFiltered) + "\",";
  response += "\"target_fan_pwm\":" + String(targetFanDuty) + ",";
  response += "\"target_light_pwm\":" + String(targetLightDuty);
  response += "}";
  sendJson(200, response);
}

void handleIotControlPost() {
  if (!server.hasArg("plain")) {
    sendJson(400, "{\"ok\":false,\"error\":\"json body required\"}");
    return;
  }

  DynamicJsonDocument doc(768);
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) {
    sendJson(400, "{\"ok\":false,\"error\":\"invalid json\"}");
    return;
  }

  // Primary mode: focus input
  if (doc.containsKey("focus")) {
    int focus = doc["focus"].as<int>();
    updateFocusInput(focus);
  }

  // Optional explicit override (0..PWM_MAX)
  if (doc.containsKey("fan_pwm")) {
    targetFanDuty = clampDuty(doc["fan_pwm"].as<int>());
  }
  if (doc.containsKey("light_pwm")) {
    targetLightDuty = clampDuty(doc["light_pwm"].as<int>());
  }

  // Compatibility with existing Flask fields
  if (doc.containsKey("fan_speed")) {
    int fanDuty = fanDutyFromSpeedLabel(doc["fan_speed"].as<String>());
    if (fanDuty >= 0) {
      targetFanDuty = clampDuty(fanDuty);
    }
  }
  if (doc.containsKey("light_color")) {
    int lightDuty = lightDutyFromColorLabel(doc["light_color"].as<String>());
    if (lightDuty >= 0) {
      targetLightDuty = clampDuty(lightDuty);
    }
  }

  String response = "{";
  response += "\"ok\":true,";
  response += "\"focus_raw\":" + String(currentFocusRaw) + ",";
  response += "\"focus_filtered\":" + String(currentFocusFiltered) + ",";
  response += "\"target_fan_pwm\":" + String(targetFanDuty) + ",";
  response += "\"target_light_pwm\":" + String(targetLightDuty);
  response += "}";
  sendJson(200, response);
}

void handleStatusGet() {
  int fanPercent = (int)round((currentFanDuty * 100.0f) / PWM_MAX);
  int lightPercent = (int)round((currentLightDuty * 100.0f) / PWM_MAX);
  unsigned long age = (lastFocusUpdateMs == 0) ? 999999 : (millis() - lastFocusUpdateMs);

  String response = "{";
  response += "\"ok\":true,";
  response += "\"wifi_connected\":" + String((WiFi.status() == WL_CONNECTED) ? "true" : "false") + ",";
  response += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  response += "\"focus_raw\":" + String(currentFocusRaw) + ",";
  response += "\"focus_filtered\":" + String(currentFocusFiltered) + ",";
  response += "\"focus_band\":\"" + focusBand(currentFocusFiltered) + "\",";
  response += "\"fan_pwm\":" + String(currentFanDuty) + ",";
  response += "\"light_pwm\":" + String(currentLightDuty) + ",";
  response += "\"fan_percent\":" + String(fanPercent) + ",";
  response += "\"light_percent\":" + String(lightPercent) + ",";
  response += "\"last_focus_age_ms\":" + String(age);
  response += "}";
  sendJson(200, response);
}

void handleHealthGet() {
  sendJson(200, "{\"ok\":true}");
}

void handleOptions() {
  addCorsHeaders();
  server.send(204);
}

void ensureWiFiConnected() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  unsigned long now = millis();
  if (now - lastWifiRetryMs < 5000) {
    return;
  }
  lastWifiRetryMs = now;
  WiFi.disconnect(true, true);
  delay(120);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void setup() {
  Serial.begin(115200);

  ledcSetup(FAN_PWM_CHANNEL, PWM_FREQ_HZ, PWM_RESOLUTION_BITS);
  ledcAttachPin(PIN_FAN_PWM, FAN_PWM_CHANNEL);
  ledcSetup(LIGHT_PWM_CHANNEL, PWM_FREQ_HZ, PWM_RESOLUTION_BITS);
  ledcAttachPin(PIN_LIGHT_PWM, LIGHT_PWM_CHANNEL);

  currentFanDuty = SAFE_FAN_DUTY;
  currentLightDuty = SAFE_LIGHT_DUTY;
  targetFanDuty = currentFanDuty;
  targetLightDuty = currentLightDuty;
  ledcWrite(FAN_PWM_CHANNEL, currentFanDuty);
  ledcWrite(LIGHT_PWM_CHANNEL, currentLightDuty);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to WiFi");
  unsigned long startMs = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startMs < 20000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());

  server.on("/set", HTTP_GET, handleSetFocusGet);
  server.on("/set", HTTP_OPTIONS, handleOptions);

  server.on("/iot/control", HTTP_POST, handleIotControlPost);
  server.on("/iot/control", HTTP_OPTIONS, handleOptions);

  server.on("/status", HTTP_GET, handleStatusGet);
  server.on("/status", HTTP_OPTIONS, handleOptions);

  server.on("/iot/status", HTTP_GET, handleStatusGet);
  server.on("/iot/status", HTTP_OPTIONS, handleOptions);

  server.on("/health", HTTP_GET, handleHealthGet);
  server.on("/health", HTTP_OPTIONS, handleOptions);

  server.onNotFound([]() {
    sendJson(404, "{\"ok\":false,\"error\":\"not found\"}");
  });

  server.begin();
  Serial.println("HTTP server started on port 80");
}

void loop() {
  server.handleClient();
  ensureWiFiConnected();
  applySmoothingTick();
}
