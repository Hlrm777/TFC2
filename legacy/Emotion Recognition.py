import cv2
import numpy as np
import tensorflow as tf

# Cargar el modelo entrenado
model = tf.keras.models.load_model('emotion_model.keras')

# Definir las emociones
emotions = ['feliz', 'triste', 'sorprendido', 'neutral', 'enojado']

# Función para detectar y extraer rostros
def detect_face(image):
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
    for (x, y, w, h) in faces:
        return image[y:y + h, x:x + w], (x, y, w, h)
    return None, None

# Iniciar la cámara
cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    if not ret:
        break

    face, bbox = detect_face(frame)
    if face is not None:
        x, y, w, h = bbox
        face = cv2.resize(face, (48, 48))
        face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        face = face / 255.0
        face = np.expand_dims(face, axis=-1)
        face = np.expand_dims(face, axis=0)

        # Predecir la emoción
        predictions = model.predict(face)
        emotion_index = np.argmax(predictions)
        emotion = emotions[emotion_index]

        # Dibujar el cuadro alrededor del rostro y la emoción detectada
        cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)
        cv2.putText(frame, emotion, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

    # Mostrar el frame
    cv2.imshow('Emotion Detection', frame)

    # Salir del bucle presionando 'q'
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Liberar la cámara y cerrar las ventanas
cap.release()
cv2.destroyAllWindows()
