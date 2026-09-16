/*
  Banana Sense ESP32 + MQ135 + SH1106 OLED

  MQ135 wiring:
  MQ135 VCC  -> ESP32 5V/VIN
  MQ135 GND  -> ESP32 GND
  MQ135 AOUT -> potentiometer outer pin
  Potentiometer other outer pin -> GND
  Potentiometer middle/wiper -> GPIO34
  MQ135 DOUT -> not used

  SH1106 OLED wiring:
  OLED VCC -> ESP32 3V3
  OLED GND -> ESP32 GND
  OLED SDA -> GPIO21
  OLED SCL -> GPIO22

  Required Arduino libraries:
  - Adafruit GFX Library
  - Adafruit SH110X
*/

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SH110X.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define OLED_ADDRESS 0x3C
#define MQ135_PIN 34

Adafruit_SH1106G display(
  SCREEN_WIDTH,
  SCREEN_HEIGHT,
  &Wire,
  OLED_RESET
);

void setup() {
  Serial.begin(115200);

  Wire.begin(21, 22);
  pinMode(MQ135_PIN, INPUT);
  analogReadResolution(12);

  if (!display.begin(OLED_ADDRESS, true)) {
    Serial.println("OLED not found!");
    while (true) {
      delay(1000);
    }
  }

  display.clearDisplay();
  display.setTextColor(SH110X_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.println("BANANA");
  display.setCursor(0, 25);
  display.println("SENSE");
  display.display();
  delay(2000);
}

void loop() {
  int rawValue = analogRead(MQ135_PIN);
  float voltage = rawValue * (3.3 / 4095.0);

  Serial.print("MQ135 Raw: ");
  Serial.print(rawValue);
  Serial.print(" | Voltage: ");
  Serial.print(voltage, 2);
  Serial.println(" V");

  display.clearDisplay();

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("MQ-135 LIVE DATA");
  display.drawLine(0, 12, 128, 12, SH110X_WHITE);

  display.setTextSize(2);
  display.setCursor(0, 20);
  display.print("RAW: ");
  display.println(rawValue);

  display.setCursor(0, 42);
  display.print("V: ");
  display.print(voltage, 2);
  display.println("V");

  display.display();
  delay(1000);
}
