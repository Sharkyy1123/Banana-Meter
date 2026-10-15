/*
  ==================================================================================
  Banana Meter — Multi-Modal IoT Freshness & E-Nose Station  (fixed version)
  ==================================================================================
  Hardware:
    - ESP32 DevKit
    - MQ-135 Gas/VOC Sensor
    - DHT11 Temperature & Humidity Sensor (change DHT_TYPE if you use DHT22)
    - OLED 128x64 I2C (SSD1306 or SH1106)
    - Potentiometer (screen select)
    - NPN transistor (2N2222 / BC547) driving alarm LED/buzzer on GPIO 25

  Required Arduino Libraries:
    - Adafruit GFX Library
    - Adafruit SSD1306  OR  Adafruit SH110X
    - DHT sensor library by Adafruit
  ==================================================================================
*/

#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Adafruit_GFX.h>
#include <DHT.h>

// ---------------------------------------------------------------------------------
// 1. OLED selection
// ---------------------------------------------------------------------------------
#define USE_SH1106   true   // true = 1.3" SH1106, false = 0.96" SSD1306

#if USE_SH1106
  #include <Adafruit_SH110X.h>
  Adafruit_SH1106G display(128, 64, &Wire, -1);
  #define OLED_COLOR SH110X_WHITE
#else
  #include <Adafruit_SSD1306.h>
  Adafruit_SSD1306 display(128, 64, &Wire, -1);
  #define OLED_COLOR SSD1306_WHITE
#endif

#define OLED_I2C_ADDR 0x3C

// ---------------------------------------------------------------------------------
// 2. Pins
// ---------------------------------------------------------------------------------
#define I2C_SDA_PIN    21
#define I2C_SCL_PIN    22
#define MQ135_PIN      34
#define POT_PIN        35
#define DHT_PIN        4
#define ALARM_PIN      25

// FIX: your header says DHT11, but the code was set to DHT22.
// A wrong type gives wrong or NaN readings. Set this to match your sensor.
#define DHT_TYPE       DHT11
DHT dht(DHT_PIN, DHT_TYPE);

// ---------------------------------------------------------------------------------
// 3. Wi-Fi
// ---------------------------------------------------------------------------------
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL    = "http://172.17.109.87:5000/api/sensor";

const bool ENABLE_WIFI = false;

// ---------------------------------------------------------------------------------
// 4. Calibration constants
// ---------------------------------------------------------------------------------
float r0_baseline = 76.0;     // clean-air resistance (kOhm) - tune for your sensor
const float RL_VAL = 10.0;    // load resistor on MQ-135 board (kOhm)

// If your MQ-135 module is powered from 5V, its output can reach 5V, which is
// too high for the ESP32 pin (max 3.3V). Use a voltage divider, then set:
//   MQ_VCC = 5.0, ADC_DIVIDER = (R1+R2)/R2  (e.g. 1.5 for 10k + 20k)
// Defaults below keep the old behaviour (3.3V module, no divider).
const float MQ_VCC      = 3.3;
const float ADC_DIVIDER = 1.0;

float smoothedMqRaw   = 800.0;
float smoothedTemp    = 24.0;
float smoothedHum     = 60.0;
float vocIndex        = 0.0;
float shelfLifeDays   = 4.0;
String ripenessState  = "Checking...";

unsigned long lastSensorRead = 0;
unsigned long lastHttpSync   = 0;
unsigned long lastDraw       = 0;
unsigned long lastWifiTry    = 0;
bool alarmActive = false;

// UI state
float potSmoothed = 0;
int currentScreen = 0;

// ---------------------------------------------------------------------------------
// MQ-135 temperature & humidity compensation
// ---------------------------------------------------------------------------------
float getMqCorrectionFactor(float tempC, float humPct) {
  if (isnan(tempC) || tempC < -10 || tempC > 60) tempC = 20.0;
  if (isnan(humPct) || humPct < 0 || humPct > 100) humPct = 65.0;

  float factor = 1.0 + 0.02 * (tempC - 20.0) - 0.005 * (humPct - 65.0);
  if (factor < 0.4) factor = 0.4;
  if (factor > 1.8) factor = 1.8;
  return factor;
}

// ---------------------------------------------------------------------------------
// Ripeness & shelf-life model (Q10)
// ---------------------------------------------------------------------------------
void calculateRipenessAndShelfLife(float vocCompensated, float tempC, float humPct) {
  if (vocCompensated < 25.0) {
    ripenessState = "UNRIPE";
    shelfLifeDays = 6.0;
  } else if (vocCompensated < 50.0) {
    ripenessState = "FRESH/RIPE";
    shelfLifeDays = 3.5;
  } else if (vocCompensated < 75.0) {
    ripenessState = "OVERRIPE";
    shelfLifeDays = 1.5;
  } else {
    ripenessState = "SPOILED";
    shelfLifeDays = 0.2;
  }

  float deltaT = tempC - 20.0;
  float tempFactor = pow(2.2, deltaT / 10.0);
  if (tempFactor < 0.5) tempFactor = 0.5;
  if (tempFactor > 3.5) tempFactor = 3.5;

  float humFactor = 1.0;
  if (humPct > 80.0) humFactor = 1.4;
  else if (humPct < 45.0) humFactor = 1.2;

  shelfLifeDays = shelfLifeDays / (tempFactor * humFactor);
  if (shelfLifeDays < 0.1) shelfLifeDays = 0.1;
}

// ---------------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[Banana Meter] Initializing...");

  pinMode(MQ135_PIN, INPUT);
  pinMode(POT_PIN, INPUT);
  pinMode(ALARM_PIN, OUTPUT);
  digitalWrite(ALARM_PIN, LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(MQ135_PIN, ADC_11db);
  analogSetPinAttenuation(POT_PIN, ADC_11db);

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  #if USE_SH1106
    if (!display.begin(OLED_I2C_ADDR, true)) {
      Serial.println("[ERR] SH1106 OLED not found!");
    }
  #else
    if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
      Serial.println("[ERR] SSD1306 OLED not found!");
    }
  #endif

  dht.begin();
  potSmoothed = analogRead(POT_PIN);

  display.clearDisplay();
  display.setTextColor(OLED_COLOR);
  display.setTextSize(2);
  display.setCursor(14, 8);
  display.println("BANANA");
  display.setCursor(20, 28);
  display.println("METER");
  display.setTextSize(1);
  display.setCursor(10, 50);
  display.println("E-Nose + Climate AI");
  display.display();
  delay(1800);

  if (ENABLE_WIFI) {
    Serial.printf("[WiFi] Connecting to %s...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// ---------------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------------
void drawProgressBar(int x, int y, int w, int h, float percent) {
  display.drawRect(x, y, w, h, OLED_COLOR);
  int fillW = (int)((w - 4) * (percent / 100.0));
  if (fillW < 0) fillW = 0;
  if (fillW > (w - 4)) fillW = w - 4;
  display.fillRect(x + 2, y + 2, fillW, h - 4, OLED_COLOR);
}

void renderScreen1_E_Nose() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("E-NOSE VOC MONITOR");
  display.setCursor(110, 0);
  display.print("1/3");
  display.drawLine(0, 10, 127, 10, OLED_COLOR);

  display.setCursor(0, 14);
  display.print("VOC Ripening Index:");
  display.setTextSize(2);
  display.setCursor(0, 25);
  display.print((int)vocIndex);
  display.print("%");

  display.setTextSize(1);
  display.setCursor(68, 29);
  if (vocIndex < 25)       display.print("UNRIPE");
  else if (vocIndex < 50)  display.print("FRESH");
  else if (vocIndex < 75)  display.print("RIPE+");
  else                     display.print("SPOILED!");

  // FIX: bar and footer overlapped and the footer went off the screen
  drawProgressBar(0, 43, 128, 10, vocIndex);

  // FIX: footer was too long (over 21 characters) and got cut off
  display.setCursor(0, 56);
  display.print(ripenessState);
  display.setCursor(80, 56);
  display.print("R:");
  display.print((int)smoothedMqRaw);
  display.display();
}

void renderScreen2_Microclimate() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("MICROCLIMATE & SHELF");
  display.setCursor(110, 0);
  display.print("2/3");
  display.drawLine(0, 10, 127, 10, OLED_COLOR);

  display.setCursor(0, 16);
  display.print("Temp: ");
  display.print(smoothedTemp, 1);
  display.print((char)247);
  display.print("C");

  display.setCursor(78, 16);
  display.print("RH:");
  display.print((int)smoothedHum);
  display.print("%");

  display.drawLine(0, 28, 127, 28, OLED_COLOR);
  display.setCursor(0, 32);
  display.print("Est. Shelf Life:");

  display.setTextSize(2);
  display.setCursor(0, 44);
  if (shelfLifeDays >= 1.0) {
    display.print(shelfLifeDays, 1);
    display.setTextSize(1);
    display.print(" Days");
  } else {
    int hours = (int)(shelfLifeDays * 24.0);
    display.print(hours);
    display.setTextSize(1);
    display.print(" Hours");
  }
  display.display();
}

void renderScreen3_Diagnostics() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("SYSTEM & SENSORS");
  display.setCursor(110, 0);
  display.print("3/3");
  display.drawLine(0, 10, 127, 10, OLED_COLOR);

  display.setCursor(0, 15);
  display.print("MQ-135 ADC: ");
  display.print((int)smoothedMqRaw);

  display.setCursor(0, 27);
  display.print("Pot: ");
  display.print((int)potSmoothed);

  display.setCursor(0, 39);
  display.print("Alarm: ");
  display.print(alarmActive ? "ACTIVE" : "OFF");

  display.setCursor(0, 51);
  if (ENABLE_WIFI && WiFi.status() == WL_CONNECTED) {
    display.print(WiFi.localIP());
  } else if (ENABLE_WIFI) {
    display.print("WiFi: Connecting...");
  } else {
    display.print("WiFi: Standalone");
  }
  display.display();
}

// ---------------------------------------------------------------------------------
// Wi-Fi telemetry
// ---------------------------------------------------------------------------------
void sendTelemetryHttp() {
  if (!ENABLE_WIFI || WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  http.setConnectTimeout(1000);  // FIX: default timeouts froze the screen for seconds
  http.setTimeout(1500);
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  String json = "{";
  json += "\"mq135\":" + String((int)smoothedMqRaw) + ",";
  json += "\"temperature\":" + String(smoothedTemp, 1) + ",";
  json += "\"humidity\":" + String(smoothedHum, 1) + ",";
  json += "\"voc_index\":" + String(vocIndex, 1) + ",";
  json += "\"pot_val\":" + String((int)potSmoothed) + ",";
  json += "\"shelf_life_days\":" + String(shelfLifeDays, 1);
  json += "}";

  int httpCode = http.POST(json);
  if (httpCode > 0) {
    Serial.printf("[HTTP] Sync OK (%d)\n", httpCode);
  } else {
    Serial.printf("[HTTP] Sync failed: %s\n", http.errorToString(httpCode).c_str());
  }
  http.end();
}

// ---------------------------------------------------------------------------------
// Loop
// ---------------------------------------------------------------------------------
void loop() {
  unsigned long now = millis();

  // 1. Sensors every 2 s
  if (now - lastSensorRead >= 2000) {
    lastSensorRead = now;

    int raw = analogRead(MQ135_PIN);
    smoothedMqRaw = 0.8 * smoothedMqRaw + 0.2 * raw;

    float t = dht.readTemperature();
    float h = dht.readHumidity();
    // FIX: old check "t > 0" threw away valid temperatures at or below 0
    if (!isnan(t)) smoothedTemp = 0.85 * smoothedTemp + 0.15 * t;
    if (!isnan(h)) smoothedHum  = 0.85 * smoothedHum  + 0.15 * h;

    float corrFactor = getMqCorrectionFactor(smoothedTemp, smoothedHum);

    // Voltage at the sensor output (undo the divider, if any)
    float vPin = (smoothedMqRaw * 3.3) / 4095.0;
    float vOut = vPin * ADC_DIVIDER;

    float rSens = 1.0;
    if (vOut > 0.05 && vOut < (MQ_VCC - 0.05)) {
      rSens = ((MQ_VCC - vOut) / vOut) * RL_VAL;
    }
    float compensatedRs = rSens / corrFactor;

    float ratio = r0_baseline / compensatedRs;
    vocIndex = constrain((ratio - 0.7) * 45.0, 0.0, 100.0);

    calculateRipenessAndShelfLife(vocIndex, smoothedTemp, smoothedHum);

    // FIX: alarm now has hysteresis so it does not buzz on and off near the limit
    if (!alarmActive && vocIndex > 72.0)      alarmActive = true;
    else if (alarmActive && vocIndex < 68.0)  alarmActive = false;
    digitalWrite(ALARM_PIN, alarmActive ? HIGH : LOW);

    Serial.printf("[DATA] Raw:%d | CompRs:%.1f | VOC:%.1f%% | T:%.1fC | RH:%.1f%% | %s | Shelf:%.1fd\n",
      (int)smoothedMqRaw, compensatedRs, vocIndex, smoothedTemp, smoothedHum,
      ripenessState.c_str(), shelfLifeDays);
  }

  // 2. Potentiometer: smoothed, with hysteresis so the screen does not flicker
  potSmoothed = 0.8 * potSmoothed + 0.2 * analogRead(POT_PIN);
  const int lo[3] = {0, 1365, 2730};
  const int hi[3] = {1365, 2730, 4096};
  const int HYST = 120;
  if (potSmoothed < lo[currentScreen] - HYST || potSmoothed >= hi[currentScreen] + HYST) {
    if (potSmoothed < 1365)      currentScreen = 0;
    else if (potSmoothed < 2730) currentScreen = 1;
    else                         currentScreen = 2;
  }

  // 3. Redraw only every 150 ms (was every 60 ms, which caused flicker)
  if (now - lastDraw >= 150) {
    lastDraw = now;
    if (currentScreen == 0)      renderScreen1_E_Nose();
    else if (currentScreen == 1) renderScreen2_Microclimate();
    else                         renderScreen3_Diagnostics();
  }

  // 4. Wi-Fi reconnect + telemetry
  if (ENABLE_WIFI) {
    if (WiFi.status() != WL_CONNECTED && now - lastWifiTry >= 10000) {
      lastWifiTry = now;
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }
    if (now - lastHttpSync >= 3000) {
      lastHttpSync = now;
      sendTelemetryHttp();
    }
  }

  delay(20);
}
