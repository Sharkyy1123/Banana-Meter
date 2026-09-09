"""Local dashboard for a banana freshness prototype.

It receives raw MQ135 readings from an ESP32 and lets the operator capture or
upload a banana image.  The included estimate is intentionally a transparent
prototype heuristic; replace `estimate_freshness` with a trained multimodal
model after collecting and labelling a dataset.
"""

from __future__ import annotations

import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageStat
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = DATA_DIR / "freshness.db"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=8 * 1024 * 1024)
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mq135 INTEGER NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inspections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_name TEXT NOT NULL,
                mq135 INTEGER,
                visual_score REAL NOT NULL,
                sensor_index REAL,
                result_label TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def recent_sensor_reading() -> dict | None:
    with db() as connection:
        row = connection.execute(
            "SELECT mq135, received_at FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def image_visual_score(image_bytes: bytes) -> float:
    """Return a simple 0-100 visible-ripeness proxy based on average RGB.

    This is deliberately not a CNN prediction. It provides a usable end-to-end
    prototype until a labelled model is added.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((224, 224))
        red, green, blue = ImageStat.Stat(rgb).mean
    yellowness = max(0, min(100, ((red + green) / 2 - blue) / 155 * 100))
    brownness = max(0, min(100, (red - green) / 120 * 100))
    # Yellow skin tends to increase the score, obvious brown dominance lowers it.
    return round(max(0, min(100, yellowness * 0.72 + (100 - brownness) * 0.28)), 1)


def estimate_freshness(visual_score: float, mq135: int | None) -> tuple[str, float, float | None]:
    """Prototype fusion estimate, not a medical/food-safety decision."""
    sensor_index = round((mq135 / 4095) * 100, 1) if mq135 is not None else None
    combined = visual_score if sensor_index is None else visual_score * 0.72 + (100 - sensor_index) * 0.28
    if combined >= 72:
        label = "Fresh"
    elif combined >= 52:
        label = "Ripe"
    elif combined >= 33:
        label = "Overripe"
    else:
        label = "Check / likely spoiled"
    # Confidence represents heuristic consistency only, not model probability.
    confidence = round(55 + min(32, abs(combined - 52) * 0.55), 1)
    return label, confidence, sensor_index


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/sensor")
def ingest_sensor():
    payload = request.get_json(silent=True) or {}
    value = payload.get("mq135")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return jsonify(error="Send JSON in the form {\"mq135\": 0..4095}."), 400
    value = int(value)
    if not 0 <= value <= 4095:
        return jsonify(error="mq135 must be between 0 and 4095."), 400
    timestamp = now_iso()
    with db() as connection:
        connection.execute(
            "INSERT INTO sensor_readings (mq135, received_at) VALUES (?, ?)", (value, timestamp)
        )
        connection.execute(
            "DELETE FROM sensor_readings WHERE id NOT IN (SELECT id FROM sensor_readings ORDER BY id DESC LIMIT 500)"
        )
    return jsonify(ok=True, mq135=value, received_at=timestamp), 201


@app.get("/api/sensor/latest")
def sensor_latest():
    latest = recent_sensor_reading()
    with db() as connection:
        rows = connection.execute(
            "SELECT mq135, received_at FROM sensor_readings ORDER BY id DESC LIMIT 24"
        ).fetchall()
    history = [dict(row) for row in reversed(rows)]
    return jsonify(latest=latest, history=history)


@app.post("/api/analyze")
def analyze():
    image_file = request.files.get("image")
    if not image_file or not image_file.filename:
        return jsonify(error="Choose or capture a banana image first."), 400
    suffix = image_file.filename.rsplit(".", 1)[-1].lower() if "." in image_file.filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify(error="Use a JPG, PNG, or WEBP image."), 400
    image_bytes = image_file.read()
    if not image_bytes:
        return jsonify(error="The selected image is empty."), 400
    try:
        visual_score = image_visual_score(image_bytes)
    except Exception:
        return jsonify(error="That file could not be read as an image."), 400

    safe_original = secure_filename(image_file.filename)
    saved_name = f"{uuid4().hex}_{safe_original}"
    (UPLOAD_DIR / saved_name).write_bytes(image_bytes)
    latest = recent_sensor_reading()
    mq135 = latest["mq135"] if latest else None
    label, confidence, sensor_index = estimate_freshness(visual_score, mq135)
    created_at = now_iso()
    with db() as connection:
        cursor = connection.execute(
            """INSERT INTO inspections
               (image_name, mq135, visual_score, sensor_index, result_label, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (saved_name, mq135, visual_score, sensor_index, label, confidence, created_at),
        )
        inspection_id = cursor.lastrowid
    return jsonify(
        id=inspection_id,
        image_url=f"/uploads/{saved_name}",
        mq135=mq135,
        visual_score=visual_score,
        sensor_index=sensor_index,
        label=label,
        confidence=confidence,
        created_at=created_at,
        model_status="Prototype heuristic â€” calibration and a labelled trained model are still required.",
    )


@app.get("/api/inspections")
def inspections():
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM inspections ORDER BY id DESC LIMIT 8"
        ).fetchall()
    return jsonify(items=[dict(row) for row in rows])


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="Image is too large. The maximum upload size is 8 MB."), 413


if __name__ == "__main__":
    init_db()
    # host=0.0.0.0 lets the ESP32 on the same Wi-Fi reach this laptop.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )

