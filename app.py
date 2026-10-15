"""Local dashboard for a banana freshness prototype.

It receives raw MQ135 readings from an ESP32 and lets the operator capture or
upload a banana image.  The included estimate is intentionally a transparent
prototype heuristic; replace `estimate_freshness` with a trained multimodal
model after collecting and labelling a dataset.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageStat
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
# Vercel functions may only write inside /tmp.  Local installations keep their
# data alongside this file, while the deployed demo uses Vercel's temporary
# function storage so an image upload never fails with a read-only filesystem.
IS_VERCEL = bool(os.environ.get("VERCEL"))
RUNTIME_DIR = Path(gettempdir()) / "banana-freshness" if IS_VERCEL else BASE_DIR
DATA_DIR = RUNTIME_DIR / "data"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
DB_PATH = DATA_DIR / "freshness.db"
MODEL_PATH = BASE_DIR / "models" / "red_banana_freshness.keras"
MODEL_LABELS_PATH = BASE_DIR / "models" / "red_banana_labels.json"
MODEL_REFERENCE_PATH = BASE_DIR / "models" / "banana_reference_embeddings.npz"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

_image_model = None
_image_model_labels: list[str] | None = None
_banana_reference_embeddings = None
_banana_similarity_threshold = None
_image_model_attempted = False

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=8 * 1024 * 1024)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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


# Vercel imports the Flask app instead of executing the __main__ block, so the
# folders and schema must be ready during import as well.
init_db()


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


def detect_banana_colour(image_bytes: bytes) -> str:
    """Classify the visible banana skin as red, yellow, or uncertain.

    The image is expected to be a close, well-lit banana photo.  Saturated
    pixels are used so the common white-paper background in this dataset does
    not dominate the colour estimate.
    """
    try:
        import numpy as np

        with Image.open(io.BytesIO(image_bytes)) as image:
            hsv = np.asarray(image.convert("HSV").resize((224, 224)), dtype="float32")
        hue, saturation, value = hsv[..., 0] / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
        mask = (saturation > 0.22) & (value > 0.15) & (value < 0.96)
        if int(mask.sum()) < 200:
            return "Uncertain-colour"
        red = (((hue < 0.075) | (hue > 0.93)) & mask).sum() / mask.sum()
        yellow = ((hue >= 0.075) & (hue <= 0.24) & mask).sum() / mask.sum()
        if red >= 0.20 and red > yellow * 1.25:
            return "Red"
        if yellow >= 0.18 and yellow > red * 1.25:
            return "Yellow"
    except Exception:
        pass
    return "Uncertain-colour"


def load_image_model():
    """Load the local CNN once, without making the public demo require TensorFlow."""
    global _image_model, _image_model_labels, _banana_reference_embeddings
    global _banana_similarity_threshold, _image_model_attempted
    if _image_model_attempted:
        return _image_model, _image_model_labels
    _image_model_attempted = True
    if not MODEL_PATH.exists() or not MODEL_LABELS_PATH.exists() or not MODEL_REFERENCE_PATH.exists():
        return None, None
    try:
        import tensorflow as tf

        _image_model = tf.keras.models.load_model(MODEL_PATH, compile=False)
        labels = json.loads(MODEL_LABELS_PATH.read_text(encoding="utf-8"))
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise ValueError("model labels must be a list of strings")
        import numpy as np

        reference = np.load(MODEL_REFERENCE_PATH)
        _banana_reference_embeddings = reference["embeddings"]
        _banana_similarity_threshold = float(reference["threshold"])
        _image_model_labels = labels
    except Exception as error:
        app.logger.warning("Could not load local image model: %s", error)
        _image_model, _image_model_labels = None, None
        _banana_reference_embeddings, _banana_similarity_threshold = None, None
    return _image_model, _image_model_labels


def image_pixels(image_bytes: bytes):
    import numpy as np

    with Image.open(io.BytesIO(image_bytes)) as image:
        return np.asarray(image.convert("RGB").resize((224, 224)), dtype="float32")


def banana_similarity(image_bytes: bytes) -> float | None:
    """Compare an image to the learned banana-image feature space.

    This one-class gate prevents the five-class ripeness softmax from assigning
    a banana label to an unrelated photo such as a face or a room.
    """
    model, _labels = load_image_model()
    if model is None or _banana_reference_embeddings is None:
        return None
    try:
        import numpy as np
        import tensorflow as tf

        encoder = tf.keras.Model(model.input, model.get_layer("global_average_pooling2d").output)
        embedding = encoder.predict(np.expand_dims(image_pixels(image_bytes), axis=0), verbose=0)[0]
        embedding /= max(float(np.linalg.norm(embedding)), 1e-12)
        return float(np.max(_banana_reference_embeddings @ embedding))
    except Exception as error:
        app.logger.warning("Banana likeness check failed: %s", error)
        return None


def classify_image(image_bytes: bytes) -> tuple[str, float] | None:
    """Return the CNN class and confidence when the local trained model exists."""
    model, labels = load_image_model()
    if model is None or labels is None:
        return None
    try:
        import numpy as np

        probabilities = model.predict(np.expand_dims(image_pixels(image_bytes), axis=0), verbose=0)[0]
        index = int(np.argmax(probabilities))
        return labels[index].replace("_", " ").title(), round(float(probabilities[index]) * 100, 1)
    except Exception as error:
        app.logger.warning("Local image-model prediction failed: %s", error)
        return None


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
    sensor_index = round((mq135 / 4095) * 100, 1) if mq135 is not None else None
    banana_colour = detect_banana_colour(image_bytes)

    if banana_colour == "Yellow":
        label = "This is a yellow banana, not a red banana"
        confidence = 0.0
        model_status = (
            "Yellow banana detected. The freshness classification model was trained "
            "only on red bananas, so ripeness cannot be evaluated for yellow bananas."
        )
    else:
        likeness = banana_similarity(image_bytes)
        if likeness is not None and likeness < _banana_similarity_threshold:
            return jsonify(
                error="Not a banana. Show one clear banana against a simple background, then try again.",
                banana_similarity=round(likeness, 3),
            ), 422
        prediction = classify_image(image_bytes)
        if prediction:
            ripeness, confidence = prediction
            prefix = "Red banana" if banana_colour == "Red" else "Banana"
            label = f"{prefix} - {ripeness}"
            model_status = (
                "Local five-class CNN image classification. MQ135 is shown as a separate "
                "signal; it has not yet been trained as paired fusion data."
            )
        else:
            label, confidence, sensor_index = estimate_freshness(visual_score, mq135)
            model_status = "Prototype heuristic - calibration and a labelled trained model are still required."
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
        model_status=model_status,
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
    # host=0.0.0.0 lets the ESP32 on the same Wi-Fi reach this laptop.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
