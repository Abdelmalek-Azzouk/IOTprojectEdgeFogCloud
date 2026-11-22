import os

import cv2
import numpy as np
import tensorflow as tf


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_SIZE = (160, 160)

TASKS = {
    "driver_state": {
        "model_file": "driver_state_best.keras",
        "classes": ("ACTIVE", "FATIGUE"),
        "threshold": 0.6,
        "display_name": "State",
    },
    "eye_state": {
        "model_file": "eye_state_best.keras",
        "classes": ("OPEN", "CLOSED"),
        "threshold": 0.5,
        "display_name": "Eyes",
    },
    "mouth_state": {
        "model_file": "mouth_state_best.keras",
        "classes": ("NO YAWN", "YAWN"),
        "threshold": 0.5,
        "display_name": "Mouth",
    },
}

print("Loading models...")
models = {
    task: tf.keras.models.load_model(
        os.path.join(BASE_DIR, task_config["model_file"])
    )
    for task, task_config in TASKS.items()
}
print("All three models loaded successfully!")


face_cascade = cv2.CascadeClassifier(
    os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
)
if face_cascade.empty():
    raise RuntimeError("Could not load OpenCV's frontal-face detector.")


def prepare_image(image):
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb_image, IMG_SIZE)
    return np.expand_dims(resized.astype(np.float32), axis=0)


def extract_eye_crops(frame):
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray_frame,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(80, 80),
    )
    if len(faces) == 0:
        return []

    x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
    eye_size = max(1, int(round(width * 0.30)))
    eye_center_y = y + int(round(height * 0.40))
    eye_crops = []

    for eye_center_x in (
        x + int(round(width * 0.32)),
        x + int(round(width * 0.68)),
    ):
        left = max(x, eye_center_x - eye_size // 2)
        top = max(y, eye_center_y - eye_size // 2)
        right = min(x + width, left + eye_size)
        bottom = min(y + height, top + eye_size)
        if right > left and bottom > top:
            eye_crops.append(
                (frame[top:bottom, left:right], (left, top, right, bottom))
            )

    return eye_crops


def predict_positive_score(model, image_batch):
    return float(model.predict(image_batch, verbose=0)[0, 0])


def classify_binary_score(positive_score, classes, threshold):
    positive = positive_score >= threshold
    label = classes[1] if positive else classes[0]
    confidence = positive_score if positive else 1.0 - positive_score
    return label, confidence


def predict_binary(model, image_batch, classes, threshold):
    positive_score = predict_positive_score(model, image_batch)
    return classify_binary_score(positive_score, classes, threshold)


cap = cv2.VideoCapture(0)

if not cap.isOpened():
    raise RuntimeError("Cannot access webcam. Check your camera connection.")

print("Webcam started! Press Q to quit.")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Cannot read webcam frame.")
            break

        frame = cv2.flip(frame, 1)
        image_batch = prepare_image(frame)
        eye_crops = extract_eye_crops(frame)
        if eye_crops:
            eye_scores = [
                predict_positive_score(models["eye_state"], prepare_image(eye_crop))
                for eye_crop, _ in eye_crops
            ]
            eye_label, eye_confidence = classify_binary_score(
                float(np.mean(eye_scores)),
                TASKS["eye_state"]["classes"],
                TASKS["eye_state"]["threshold"],
            )
            for _, (left, top, right, bottom) in eye_crops:
                cv2.rectangle(frame, (left, top), (right, bottom), (255, 255, 0), 2)
        else:
            eye_label, eye_confidence = "FACE NOT FOUND", None

        for index, (task, task_config) in enumerate(TASKS.items()):
            if task == "eye_state":
                label, confidence = eye_label, eye_confidence
            else:
                label, confidence = predict_binary(
                    models[task],
                    image_batch,
                    task_config["classes"],
                    task_config["threshold"],
                )

            if confidence is None:
                text_color = (0, 165, 255)
                result_text = f"{task_config['display_name']}: {label}"
            else:
                text_color = (
                    (0, 0, 255)
                    if label in ("FATIGUE", "CLOSED", "YAWN")
                    else (0, 255, 0)
                )
                result_text = f"{task_config['display_name']}: {label} ({confidence * 100:.1f}%)"

            cv2.putText(
                frame,
                result_text,
                (20, 40 + index * 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                text_color,
                2,
            )

        cv2.putText(
            frame,
            "EXPERIMENTAL MODEL TEST",
            (20, 155),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 165, 255),
            2,
        )
        cv2.imshow("Driver Drowsiness - Webcam Test", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
finally:
    cap.release()
    cv2.destroyAllWindows()

print("Webcam test finished.")
