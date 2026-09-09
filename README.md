# Banana Sense â€” local freshness dashboard

This Flask website receives raw MQ135 values from your ESP32 and pairs the latest reading with a camera/uploaded banana image. It saves both sensor readings and inspections locally in `data/freshness.db`.

The corrected ESP32 sketch is in [`hardware/banana_mq135.ino`](hardware/banana_mq135.ino). It deliberately contains only placeholder Wi-Fi credentials, so the public source code does not expose your network password.

## Run it

The simplest option is to double-click `start.bat`. It creates the Python
environment, installs the packages and starts the server.

Alternatively, open PowerShell in this folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

On Windows installations that provide the Python launcher, `py` can be used in
place of `python` in those commands.

Open `http://127.0.0.1:5000` on the laptop. The server also listens on your local Wi-Fi network so the ESP32 can reach it.

## Connect the ESP32 sketch

In the supplied sketch, set:

```cpp
const char *WIFI_SSID = "your Wi-Fi name";
const char *WIFI_PASSWORD = "your Wi-Fi password";
const char *SERVER_IP = "your laptop's IPv4 address";
```

Find the laptop address with `ipconfig`; use the **IPv4 Address** of the active Wi-Fi adapter (for example `192.168.1.42`). Keep the ESP32 and laptop on the same Wi-Fi network. If Windows asks about firewall access when Flask starts, allow it on **Private networks**.

Your current JSON is already exactly what this site accepts:

```json
{"mq135": 320}
```

The endpoint is `POST http://<SERVER_IP>:5000/api/sensor`. A successful ESP32 serial log will show HTTP `201`.

## Important model note

The dashboard runs end-to-end now, but its displayed â€œprototype estimateâ€ is deliberately a basic RGB + raw-sensor heuristic. It is **not** a trained CNN/fusion model and must not be used as a food-safety decision. Replace `estimate_freshness()` in `app.py` once you have collected labelled image/sensor data, normalized/calibrated your sensors, trained and validated the multimodal model.

The supplied review notes mention MQ3 as a future second modality. The present ESP32 sketch sends MQ135 only, so the application correctly works with one sensor and leaves an obvious upgrade path for MQ3.

