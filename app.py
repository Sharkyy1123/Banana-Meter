"""Local dashboard for banana freshness inspection.

The camera path uses a trained image classifier when ``models/banana_model.keras``
is present. Until a model is trained, the app keeps the original heuristic as
a clearly labelled fallback so the rest of the dashboard remains usable.
"""

from __future__ import annotations

from banana_gate import is_banana
import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageStat, ImageEnhance, ImageOps
from werkzeug.utils import secure_filename

try:
    import tensorflow as tf
except Exception:  # pragma: no cover - optional until model is installed
    tf = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
MODEL_DIR = BASE_DIR / "models"
MODEL_PATH = MODEL_DIR / "banana_model.keras"
DB_PATH = DATA_DIR / "freshness.db"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
IMAGE_SIZE = (224, 224)
MODEL_CLASSES = ["Unripe", "Ripe", "Overripe", "Rotten"]

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=8 * 1024 * 1024)
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

_classifier = None
_classifier_mtime = None


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


def _prepare_image(image_bytes: bytes) -> np.ndarray:
    """Normalize a camera image to the model's expected 224x224 RGB input.

    EXIF orientation is applied, the longer side is fit to a square crop,
    and mild contrast/color normalization reduces variation between webcams.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, IMAGE_SIZE, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        image = ImageEnhance.Contrast(image).enhance(1.05)
        image = ImageEnhance.Color(image).enhance(1.03)
        return np.asarray(image, dtype=np.float32) / 255.0


def _load_classifier():
    global _classifier, _classifier_mtime
    if tf is None or not MODEL_PATH.exists():
        return None
    mtime = MODEL_PATH.stat().st_mtime
    if _classifier is None or _classifier_mtime != mtime:
        _classifier = tf.keras.models.load_model(MODEL_PATH, compile=False)
        _classifier_mtime = mtime
    return _classifier


def model_visual_prediction(image_bytes: bytes) -> tuple[str, float, float] | None:
    """Return (label, confidence, visual_score) from the trained classifier."""
    model = _load_classifier()
    if model is None:
        return None

    image = _prepare_image(image_bytes)
    probabilities = np.asarray(model.predict(image[None, ...], verbose=0))[0]
    if probabilities.ndim != 1 or len(probabilities) != len(MODEL_CLASSES):
        raise ValueError("banana_model.keras must output exactly four class probabilities")

    index = int(np.argmax(probabilities))
    confidence = float(probabilities[index] * 100)
    # Higher score means fresher. Keep this as a continuous visual metric for UI.
    freshness_weights = np.array([92.0, 78.0, 45.0, 8.0], dtype=np.float32)
    visual_score = float(np.dot(probabilities, freshness_weights))
    return MODEL_CLASSES[index], confidence, round(max(0.0, min(100.0, visual_score)), 1)


def image_visual_score(image_bytes: bytes) -> float:
    """Fallback RGB heuristic used only when no trained model is installed."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((224, 224))
        red, green, blue = ImageStat.Stat(rgb).mean
    yellowness = max(0, min(100, ((red + green) / 2 - blue) / 155 * 100))
    brownness = max(0, min(100, (red - green) / 120 * 100))
    return round(max(0, min(100, yellowness * 0.72 + (100 - brownness) * 0.28)), 1)


def estimate_freshness(visual_score: float, mq135: int | None) -> tuple[str, float, float | None]:
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
    confidence = round(55 + min(32, abs(combined - 52) * 0.55), 1)
    return label, confidence, sensor_index


def normalize_model_label(model_label: str) -> str:
    return {
        "Unripe": "Unripe",
        "Ripe": "Fresh / ripe",
        "Overripe": "Overripe",
        "Rotten": "Rotten",
    }.get(model_label, model_label)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/sensor")
def ingest_sensor():
    payload = request.get_json(silent=True) or {}
    value = payload.get("mq135")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return jsonify(error='Send JSON in the form {"mq135": 0..4095}.'), 400
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
        return jsonify(error="The selected image is empty."),400
        
    banana_detected, banana_confidence = is_banana(image_bytes)

    if not banana_detected:
        return jsonify(
            label="Not a banana",
            confidence=round(100 - banana_confidence, 1),
            visual_score=0,
            sensor_index=None,
            mq135=None,
            model_type="banana_gate",
            model_status="The image does not appear to contain a banana."
    )
    
    if not image_bytes:
        return jsonify(error="The selected image is empty."), 400

    try:
        prediction = model_visual_prediction(image_bytes)
        if prediction is None:
            visual_score = image_visual_score(image_bytes)
            latest = recent_sensor_reading()
            mq135 = latest["mq135"] if latest else None
            label, confidence, sensor_index = estimate_freshness(visual_score, mq135)
            model_status = "Fallback heuristic — train models/banana_model.keras for camera AI."
            model_type = "heuristic"
        else:
            model_label, confidence, visual_score = prediction
            latest = recent_sensor_reading()
            mq135 = latest["mq135"] if latest else None
            sensor_index = round((mq135 / 4095) * 100, 1) if mq135 is not None else None
            label = normalize_model_label(model_label)
            model_status = "Trained camera classifier — prediction uses the banana image only."
            model_type = "camera_model"
    except Exception as exc:
        app.logger.exception("Vision inference failed")
        return jsonify(error=f"Camera model failed: {exc}"), 500

    safe_original = secure_filename(image_file.filename)
    saved_name = f"{uuid4().hex}_{safe_original}"
    (UPLOAD_DIR / saved_name).write_bytes(image_bytes)
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
        confidence=round(confidence, 1),
        created_at=created_at,
        model_status=model_status,
        model_type=model_type,
    )


@app.get("/api/model-status")
def model_status():
    model = _load_classifier()
    return jsonify(
        available=model is not None,
        model_path=str(MODEL_PATH.relative_to(BASE_DIR)),
        classes=MODEL_CLASSES,
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


@app.errorhandler(500)
def internal_server_error(error):
    app.logger.error("Internal server error on %s: %s", request.path, error)
    if request.path.startswith("/api/"):
        return jsonify(error="Server error. Check the PowerShell window running start.bat, then try again."), 500
    return "Internal server error", 500


if __name__ == "__main__":
    init_db()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
