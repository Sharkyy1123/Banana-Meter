"""Reject images that are clearly not bananas before ripeness inference."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

try:
    import tensorflow as tf
except Exception:  # pragma: no cover
    tf = None

_detector = None


def _load_detector():
    global _detector
    if tf is None:
        return None
    if _detector is None:
        _detector = tf.keras.applications.MobileNetV2(
            weights="imagenet",
            include_top=True,
        )
    return _detector


def is_banana(image_bytes: bytes, threshold: float = 0.12) -> tuple[bool, float]:
    """Return whether ImageNet's classifier sees a banana in the image.

    This is an object-presence guard, not a ripeness classifier. The threshold
    is intentionally modest because webcam images may be dark or cropped.
    """
    model = _load_detector()
    if model is None:
        return True, 0.0  # Do not block the app if TensorFlow is unavailable.

    with Image.open(io.BytesIO(image_bytes)) as image:
        image = ImageOps.fit(image.convert("RGB"), (224, 224))
        array = np.asarray(image, dtype=np.float32)

    array = tf.keras.applications.mobilenet_v2.preprocess_input(array)
    predictions = model.predict(array[None, ...], verbose=0)
    banana_probability = float(predictions[0][954])  # ImageNet class 954 = banana
    return banana_probability >= threshold, banana_probability * 100.0
