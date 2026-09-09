/*
  Banana Sense ESP32 + MQ135 sender

  Wiring (use a voltage divider; ESP32 pins are NOT 5 V tolerant):
  MQ135 VCC  -> ESP32 5V/VIN
  MQ135 GND  -> ESP32 GND
  MQ135 AOUT -> 15k ohm resistor -> GPIO34
                                     |
                                  27k ohm resistor
                                     |
                                    GND
  MQ135 DOUT -> not used
*/

#include <WiFi.h>
#include <HTTPClient.h>

const char *WIFI_SSID = "YOUR_WIFI_NAME";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char *SERVER_IP = "192.168.1.42"; // Replace with the laptop Wi-Fi IPv4 address
const int SERVER_PORT = 5000;

const int MQ135_PIN = 34;
const unsigned long SEND_INTERVAL_MS = 2000;
unsigned long lastSendTime = 0;

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

void sendReading(int rawValue) {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    return;
  }

  HTTPClient http;
  String url = "http://" + String(SERVER_IP) + ":" + String(SERVER_PORT) + "/api/sensor";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String body = "{\"mq135\":" + String(rawValue) + "}";
  int httpCode = http.POST(body);

  Serial.print("Sent MQ135 = ");
  Serial.print(rawValue);
  Serial.print(" | HTTP status = ");
  Serial.println(httpCode);
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(MQ135_PIN, INPUT);
  analogReadResolution(12);
  WiFi.mode(WIFI_STA);
  connectWiFi();
}

void loop() {
  if (millis() - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = millis();
    sendReading(analogRead(MQ135_PIN));
  }
}

