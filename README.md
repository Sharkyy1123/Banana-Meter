# Banana Meter — Local Banana Freshness Detection

Banana Meter is a local AI-based banana freshness prototype that combines:

- A camera/image-based TensorFlow model for banana ripeness classification.
- An ESP32 with an MQ-135 gas sensor for live gas/VOC readings.
- A SH1106 128×64 OLED connected to the ESP32 for displaying the MQ-135 readings.

The camera analysis runs through a local Flask website. The MQ-135 data is displayed directly on the OLED and is **not sent to the website**.

## Current System

### Camera AI

The camera model classifies bananas into four classes:

- Unripe
- Ripe
- Overripe
- Rotten

The trained model was evaluated on a separate test set with approximately **96.1% accuracy**.

The trained model file is kept locally and is not committed to GitHub.

### ESP32 + MQ-135 + OLED

The ESP32 reads the MQ-135 through its analog output and displays the live reading on the SH1106 OLED:

- Raw ADC value
- Voltage
- Live update every second

The current hardware sketch is available at [`hardware/banana_mq135.ino`](hardware/banana_mq135.ino).

### Important MQ-135 note

The MQ-135 measures changes in gas/VOC concentration. It does **not** directly identify a banana or determine ripeness by itself. Banana-specific freshness thresholds require calibration using labelled banana samples.

## Hardware Wiring

### SH1106 OLED

| OLED | ESP32 |
|---|---|
| VCC | 3.3V |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

The OLED uses I2C address `0x3C` in the current sketch.

### MQ-135

The MQ-135 is powered from the ESP32 5V/VIN supply. Its analog output is connected through a potentiometer voltage divider before reaching GPIO34.

| MQ-135 / Potentiometer | ESP32 |
|---|---|
| MQ-135 VCC | 5V / VIN |
| MQ-135 GND | GND |
| Potentiometer wiper | GPIO 34 |
| MQ-135 DOUT | Not used |

**Do not connect a voltage above 3.3V directly to an ESP32 GPIO.**

## Required Arduino Libraries

Install these libraries through the Arduino IDE Library Manager:

- Adafruit GFX Library
- Adafruit SH110X

## Run the Camera Website

The website is intended to run locally on the computer.

The simplest option on Windows is to double-click `start.bat`.

Alternatively, open PowerShell in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

The website supports camera capture and image upload for banana analysis.

## Project Structure

```text
banana/
├── app.py
├── banana_gate.py
├── train_camera_model.py
├── requirements.txt
├── start.bat
├── hardware/
│   └── banana_mq135.ino
├── static/
│   └── app.js
├── templates/
│   └── index.html
├── data/
└── uploads/
```

## Deployment

This project is **not deployed on Vercel or any other cloud hosting platform**.

The intended setup is:

```text
Camera / Image
      ↓
Local Flask Website
      ↓
TensorFlow Banana Model
      ↓
Freshness Classification

MQ-135 → ESP32 → SH1106 OLED
```

GitHub is used for source-code storage and version control only.

## Safety / Prototype Notice

This is an experimental food-freshness prototype. The AI classification and MQ-135 readings should not be treated as a certified food-safety test or a substitute for proper food-safety assessment.
