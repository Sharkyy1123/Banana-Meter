"""Train the camera-only banana freshness classifier.

Expected dataset layout:

dataset/
    train/{unripe,ripe,overripe,rotten}/
    valid/{unripe,ripe,overripe,rotten}/
    test/{unripe,ripe,overripe,rotten}/

Run on Windows from the project folder:
    python train_camera_model.py --data "C:\\path\\to\\Banana Ripeness Classification Dataset"

The trained model is saved as models/banana_model.keras, which app.py loads
automatically.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import tensorflow as tf


IMAGE_SIZE = (224, 224)
BATCH_SIZE = 32
CLASS_NAMES = ["unripe", "ripe", "overripe", "rotten"]
SEED = 42


def load_split(path: Path, shuffle: bool):
    """Load one folder split while enforcing the model's class order."""
    return tf.keras.utils.image_dataset_from_directory(
        path,
        labels="inferred",
        label_mode="int",
        class_names=CLASS_NAMES,
        image_size=IMAGE_SIZE,
        batch_size=BATCH_SIZE,
        shuffle=shuffle,
        seed=SEED,
    ).prefetch(tf.data.AUTOTUNE)


def build_model() -> tf.keras.Model:
    """Build a lightweight transfer-learning classifier."""
    augmentation = tf.keras.Sequential(
        [
            tf.keras.layers.RandomFlip("horizontal"),
            tf.keras.layers.RandomRotation(0.08),
            tf.keras.layers.RandomZoom(0.12),
            tf.keras.layers.RandomContrast(0.10),
        ],
        name="augmentation",
    )

    base = tf.keras.applications.MobileNetV2(
        input_shape=(*IMAGE_SIZE, 3),
        include_top=False,
        weights="imagenet",
    )
    base.trainable = False

    inputs = tf.keras.Input(shape=(*IMAGE_SIZE, 3), name="image")
    x = augmentation(inputs)
    x = tf.keras.applications.mobilenet_v2.preprocess_input(x)
    x = base(x, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    x = tf.keras.layers.Dropout(0.25)(x)
    outputs = tf.keras.layers.Dense(len(CLASS_NAMES), activation="softmax")(x)

    model = tf.keras.Model(inputs, outputs, name="banana_camera_classifier")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Dataset root containing train, valid and test")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--output", default="models/banana_model.keras")
    args = parser.parse_args()

    data_root = Path(args.data).expanduser().resolve()
    train_dir = data_root / "train"
    valid_dir = data_root / "valid"
    test_dir = data_root / "test"

    for split in (train_dir, valid_dir, test_dir):
        if not split.is_dir():
            raise FileNotFoundError(f"Missing dataset folder: {split}")

    print("Loading dataset...")
    train_ds = load_split(train_dir, shuffle=True)
    valid_ds = load_split(valid_dir, shuffle=False)
    test_ds = load_split(test_dir, shuffle=False)

    print("Detected class order:", train_ds.class_names)
    model = build_model()
    model.summary()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_accuracy", patience=4, mode="max", restore_best_weights=True
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.3, patience=2, min_lr=1e-6
        ),
        tf.keras.callbacks.ModelCheckpoint(
            output_path, monitor="val_accuracy", mode="max", save_best_only=True
        ),
    ]

    print("Training classifier head...")
    model.fit(train_ds, validation_data=valid_ds, epochs=args.epochs, callbacks=callbacks)

    print("Evaluating on the test set...")
    loss, accuracy = model.evaluate(test_ds, verbose=1)
    print(f"Test loss: {loss:.4f}")
    print(f"Test accuracy: {accuracy:.4%}")
    print(f"Saved model to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
