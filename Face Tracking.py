import cv2
import dlib
import mediapipe as mp
import numpy as np


# python -m pip install dlib-19.22.99-cp39-cp39-win_amd64.whl

# Función de escalimiento de valores angulares
def convert_value(val, min_in, max_in, min_out, max_out, step):
    # Normaliza el valor de entrada dentro del rango [0, 1]
    normalized_val = (val - min_in) / (max_in - min_in)
    # Escala el valor normalizado al rango de salida [min_out, max_out]
    scaled_val = min_out + normalized_val * (max_out - min_out)
    # Ajusta el valor escalado al valor más cercano múltiplo de `step`
    rounded_val = round((scaled_val - min_out) / step) * step + min_out
    # Asegura que el valor ajustado no exceda los límites de salida
    final_val = max(min_out, min(max_out, rounded_val))
    return final_val


# Función para extraer las coordenadas de los puntos de referencia
def get_landmarks(img, fc):
    shape = predictor(img, fc)
    return np.array([(shape.part(i).x, shape.part(i).y) for i in range(shape.num_parts)])


# Función para calcular el ángulo de giro de la cabeza del plano sagital
def calculate_angle(lndmrks):
    p1, p2 = lndmrks[1], lndmrks[15]
    angl = np.arctan2(p2[1] - p1[1], p2[0] - p1[0]) * 180. / np.pi
    return angl


# Función para calcular el ángulo de giro de la cabeza alrededor del eje craneocaudal
def calculate_yaw_angle(lndmrks):
    p1 = ((lndmrks[36][0] + lndmrks[45][0]) // 2, (lndmrks[36][1] + lndmrks[45][1]) // 2)
    p2 = lndmrks[30]
    angl = np.arctan2(p2[1] - p1[1], p2[0] - p1[0]) * 180. / np.pi
    return angl


# Inicializamos MediaPipe Face Mesh
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh()

# Ruta al archivo del predictor de dlib
predictor_path = "shape_predictor_68_face_landmarks.dat"

try:
    detector = dlib.get_frontal_face_detector()
    predictor = dlib.shape_predictor(predictor_path)
except RuntimeError as e:
    print(f"Error al cargar el predictor de forma: {e}")
    exit(1)

# Creación de los detectores de rostros
detector_faces = mp.solutions.face_detection

# Cargamos la cámara
capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)

with detector_faces.FaceDetection(0.75, 0):
    while True:
        ret, image = capture.read()
        image = cv2.flip(image, 1)
        if not ret:
            break

        # Convertimos la imagen a RGB para MediaPipe y a escala de grises para dlib
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Procesamos la imagen para detectar las caras con dlib
        faces = detector(gray)

        for face in faces:
            landmarks = get_landmarks(gray, face)
            yaw_angle = calculate_yaw_angle(landmarks)
            angle = calculate_angle(landmarks)

            print(f"ANGULO MIRAR DERECHA-IZQUIERDA: {convert_value(yaw_angle,60,120, -30, 30, 5)}°")
            print(f"ANGULO ROTACIÓN DERECHA-IZQUIERDA: {convert_value(angle,-20,20, -15, 15, 5)}°")
            print("<===============================================================>")
            print("<===============================================================>")

            # Mostramos la imagen
            cv2.imshow('Face Tracking', image)

        # Salimos del bucle si se presiona la letra "q"
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
