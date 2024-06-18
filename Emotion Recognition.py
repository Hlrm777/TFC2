from keras.models import load_model
from keras.preprocessing.image import img_to_array
import cv2
import numpy as np
from collections import deque

# Load the pre-trained models
face_classifier = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
classifier = load_model("Emotion_Detection.h5")

# Define the class labels
class_labels = ["angry", "happy", "neutral", "sad", "surprise"]

# Define a deque to store the last ten predictions
predictions_history = deque(maxlen=5)

# Start video capture
cap = cv2.VideoCapture(0)

while True:
    ret, image = cap.read()
    if not ret:
        break
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = face_classifier.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        cv2.rectangle(image, (x, y), (x + w, y + h), (255, 0, 0), 2)
        roi_gray = gray[y:y + h, x:x + w]
        roi_gray = cv2.resize(roi_gray, (48, 48), interpolation=cv2.INTER_AREA)

        if np.sum([roi_gray]) != 0:
            roi = roi_gray.astype("float") / 255.0
            roi = img_to_array(roi)
            roi = np.expand_dims(roi, axis=0)

            preds = classifier.predict(roi)[0]

            # Store the latest prediction in the deque
            predictions_history.append(preds)

            # Compute the mean of the last ten predictions
            mean_preds = np.mean(predictions_history, axis=0)
            label = class_labels[mean_preds.argmax()]

            label_position = (x, y)
            cv2.putText(image, label, label_position, cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 255, 0), 3)
        else:
            cv2.putText(image, "No Face Found", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 3)

    cv2.imshow("Emotion Recognition", image)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
