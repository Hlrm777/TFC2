import os
import cv2
import numpy as np
import tensorflow as tf
from tensorflow.keras.utils import to_categorical
from sklearn.model_selection import train_test_split

# Ruta del dataset
dataset_path = r"C:\Users\harol\PycharmProjects\Tesis\emotions_dataset"

# Emociones y asignación de etiquetas
emotions = ['feliz', 'triste', 'sorprendido', 'neutral', 'enojado']
emotion_dict = {emotion: idx for idx, emotion in enumerate(emotions)}

# Función para detectar y extraer rostros
def detect_face(image):
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
    for (x, y, w, h) in faces:
        return image[y:y + h, x:x + w]
    return None

# Cargar y preprocesar imágenes
images = []
labels = []

for filename in os.listdir(dataset_path):
    filepath = os.path.join(dataset_path, filename)
    emotion = filename.split('_')[0]
    if emotion in emotion_dict:
        label = emotion_dict[emotion]
        image = cv2.imread(filepath)
        face = detect_face(image)
        if face is not None:
            face = cv2.resize(face, (48, 48))
            face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
            images.append(face)
            labels.append(label)

images = np.array(images)
labels = np.array(labels)

# Asegurarse de que tenemos suficientes datos en cada clase para los conjuntos de entrenamiento y prueba
min_class_samples = 300
for emotion in emotions:
    emotion_index = emotion_dict[emotion]
    if np.sum(labels == emotion_index) < min_class_samples:
        raise ValueError(f"No hay suficientes imágenes de la emoción {emotion} para separar un conjunto de prueba adecuado.")

# Normalizar las imágenes
images = images / 255.0
images = np.expand_dims(images, -1)

# Convertir las etiquetas a one-hot encoding
labels = to_categorical(labels, num_classes=len(emotions))

# Separar el dataset en entrenamiento y prueba
X_train, X_test, y_train, y_test = train_test_split(images, labels, test_size=0.15, stratify=labels)

# Crear el modelo CNN
model = tf.keras.models.Sequential([
    tf.keras.layers.Conv2D(32, (3, 3), activation='relu', input_shape=(48, 48, 1)),
    tf.keras.layers.MaxPooling2D((2, 2)),
    tf.keras.layers.Conv2D(64, (3, 3), activation='relu'),
    tf.keras.layers.MaxPooling2D((2, 2)),
    tf.keras.layers.Conv2D(128, (3, 3), activation='relu'),
    tf.keras.layers.MaxPooling2D((2, 2)),
    tf.keras.layers.Flatten(),
    tf.keras.layers.Dense(128, activation='relu'),
    tf.keras.layers.Dropout(0.5),
    tf.keras.layers.Dense(len(emotions), activation='softmax')
])

model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])

# Entrenar el modelo
history = model.fit(X_train, y_train, epochs=30, validation_data=(X_test, y_test), batch_size=32)

# Guardar el modelo
model.save('emotion_model.keras')

# Evaluar el modelo
test_loss, test_acc = model.evaluate(X_test, y_test, verbose=2)
print(f'\nTest accuracy: {test_acc}')