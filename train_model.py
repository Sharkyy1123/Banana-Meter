"""Train the five-class red-banana freshness image classifier.

Usage (after installing TensorFlow):
    python train_model.py --epochs 25

The script keeps a held-out validation split and exports both the Keras model
and its class-label order.  With only 109 images this is a prototype model,
not a food-safety classifier.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import tensorflow as tf
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "training_data" / "red_banana"
MODEL_DIR = BASE_DIR / "models"
IMAGE_SIZE = (224, 224)
SEED = 20261014


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the red-banana freshness CNN.")
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def save_banana_reference_embeddings(model: tf.keras.Model) -> None:
    """Save one-class banana references used to reject unrelated photos."""
    paths = [path for path in DATASET_DIR.rglob("*") if path.is_file()]
    images = []
    for path in paths:
        with Image.open(path) as image:
            images.append(np.asarray(image.convert("RGB").resize(IMAGE_SIZE), dtype="float32"))
    encoder = tf.keras.Model(model.input, model.get_layer("global_average_pooling2d").output)
    embeddings = encoder.predict(np.asarray(images), batch_size=16, verbose=0)
    embeddings /= np.maximum(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-12)
    similarities = embeddings @ embeddings.T
    np.fill_diagonal(similarities, -1)
    nearest = similarities.max(axis=1)
    threshold = float(np.percentile(nearest, 5) - 0.03)
    np.savez_compressed(
        MODEL_DIR / "banana_reference_embeddings.npz",
        embeddings=embeddings.astype("float32"),
        threshold=np.float32(threshold),
    )


def main() -> None:
    args = parse_args()
    if not DATASET_DIR.exists():
        raise SystemExit(f"Dataset missing: {DATASET_DIR}")

    tf.keras.utils.set_random_seed(SEED)
    random.seed(SEED)
    np.random.seed(SEED)

    train_ds = tf.keras.utils.image_dataset_from_directory(
        DATASET_DIR,
        validation_split=0.2,
        subset="training",
        seed=SEED,
        image_size=IMAGE_SIZE,
        batch_size=args.batch_size,
        label_mode="int",
    )
    validation_ds = tf.keras.utils.image_dataset_from_directory(
        DATASET_DIR,
        validation_split=0.2,
        subset="validation",
        seed=SEED,
        image_size=IMAGE_SIZE,
        batch_size=args.batch_size,
        label_mode="int",
    )
    class_names = train_ds.class_names
    if len(class_names) != 5:
        raise SystemExit(f"Expected 5 class folders, found {len(class_names)}: {class_names}")

    autotune = tf.data.AUTOTUNE
    train_ds = train_ds.shuffle(128, seed=SEED).prefetch(autotune)
    validation_ds = validation_ds.prefetch(autotune)

    augmentation = tf.keras.Sequential(
        [
            tf.keras.layers.RandomFlip("horizontal"),
            tf.keras.layers.RandomRotation(0.08),
            tf.keras.layers.RandomZoom(0.12),
            tf.keras.layers.RandomContrast(0.12),
        ],
        name="augmentation",
    )
    base = tf.keras.applications.MobileNetV2(
        input_shape=IMAGE_SIZE + (3,), include_top=False, weights="imagenet"
    )
    base.trainable = False
    inputs = tf.keras.Input(shape=IMAGE_SIZE + (3,))
    x = augmentation(inputs)
    x = tf.keras.applications.mobilenet_v2.preprocess_input(x)
    x = base(x, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    x = tf.keras.layers.Dropout(0.35)(x)
    outputs = tf.keras.layers.Dense(len(class_names), activation="softmax")(x)
    model = tf.keras.Model(inputs, outputs, name="red_banana_freshness")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss=tf.keras.losses.SparseCategoricalCrossentropy(),
        metrics=["accuracy"],
    )

    MODEL_DIR.mkdir(exist_ok=True)
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=6, restore_best_weights=True
        ),
        tf.keras.callbacks.ModelCheckpoint(
            MODEL_DIR / "best_red_banana.keras", monitor="val_loss", save_best_only=True
        ),
    ]
    history = model.fit(
        train_ds,
        validation_data=validation_ds,
        epochs=args.epochs,
        callbacks=callbacks,
        verbose=2,
    )

    model.save(MODEL_DIR / "red_banana_freshness.keras")
    save_banana_reference_embeddings(model)
    (MODEL_DIR / "red_banana_labels.json").write_text(
        json.dumps(class_names, indent=2), encoding="utf-8"
    )
    (MODEL_DIR / "training_metrics.json").write_text(
        json.dumps({key: [float(item) for item in values] for key, values in history.history.items()}, indent=2),
        encoding="utf-8",
    )
    loss, accuracy = model.evaluate(validation_ds, verbose=0)
    print(f"Saved model to {MODEL_DIR / 'red_banana_freshness.keras'}")
    print(f"Validation accuracy: {accuracy:.3f}; validation loss: {loss:.3f}")


if __name__ == "__main__":
    main()
