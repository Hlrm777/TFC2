import cv2
import mediapipe as mp
import numpy as np


# Función para calcular los ángulos de giro del brazo
def calculate_arm_angles(lndmrks, left=True):
    # Puntos para el cálculo de los ángulos
    shoulder = lndmrks[mp_pose.PoseLandmark.LEFT_SHOULDER.value if left else mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
    hip = lndmrks[mp_pose.PoseLandmark.LEFT_HIP.value if left else mp_pose.PoseLandmark.RIGHT_HIP.value]
    wrist = lndmrks[mp_pose.PoseLandmark.LEFT_WRIST.value if left else mp_pose.PoseLandmark.RIGHT_WRIST.value]

    # Cálculo del ángulo verde (balanceo lateral)
    angle_green = np.arctan2(wrist.y - hip.y, wrist.x - hip.x) * 180. / np.pi

    # Cálculo del ángulo rojo (movimiento anterior/posterior)
    arm_length = np.linalg.norm([wrist.x - shoulder.x, wrist.y - shoulder.y])
    max_arm_length = np.linalg.norm([hip.x - shoulder.x, hip.y - shoulder.y])
    angle_red = np.arccos(arm_length / max_arm_length) * 180. / np.pi

    return angle_red, angle_green


# Inicializamos MediaPipe Pose
mp_pose = mp.solutions.pose

# Cargamos la cámara
capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)

with mp_pose.Pose(min_detection_confidence=0.50, min_tracking_confidence=0.50) as pose:
    while True:
        ret, image = capture.read()
        image = cv2.flip(image, 1)
        if not ret:
            break

        # Convertimos la imagen a RGB para MediaPipe
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # Procesamos la imagen para detectar la pose
        result = pose.process(image_rgb)

        if result.pose_landmarks:
            landmarks = result.pose_landmarks.landmark
            left_red_angle, left_green_angle = calculate_arm_angles(landmarks, left=True)
            right_red_angle, right_green_angle = calculate_arm_angles(landmarks, left=False)
            print("--------------------------DERECHO--------------------------------")
            print(f"Ángulo 1: {left_red_angle} grados")
            print(f"Ángulo 2: {left_green_angle} grados")
            print("-------------------------IZQUIERDO-------------------------------")
            print(f"Angulo 1: {right_red_angle} grados")
            print(f"Ángulo 2: {right_green_angle} grados")
            print("<===============================================================>")
            print("<===============================================================>")

            # Calculamos los valores necesarios para las lineas
            image_height, image_width, _ = image.shape
            shoulder_left = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
            hip_left = landmarks[mp_pose.PoseLandmark.LEFT_HIP.value]
            shoulder_right = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
            hip_right = landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value]
            wrist_left = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
            wrist_right = landmarks[mp_pose.PoseLandmark.RIGHT_WRIST.value]
            neck = landmarks[mp_pose.PoseLandmark.NOSE.value]

            # Dibujamos una línea desde el hombro hasta la muñeca para cada brazo
            cv2.line(image, (int(shoulder_left.x * image_width), int(shoulder_left.y * image_height)),
                     (int(wrist_left.x * image_width), int(wrist_left.y * image_height)), (255, 0, 0), 2)
            cv2.line(image, (int(shoulder_right.x * image_width), int(shoulder_right.y * image_height)),
                     (int(wrist_right.x * image_width), int(wrist_right.y * image_height)), (0, 0, 255), 2)

            # Dibujamos una línea desde el hombro hasta la cadera (tronco) para cada lado
            cv2.line(image, (int(shoulder_left.x * image_width), int(shoulder_left.y * image_height)),
                     (int(hip_left.x * image_width), int(hip_left.y * image_height)), (0, 255, 255), 2)
            cv2.line(image, (int(shoulder_right.x * image_width), int(shoulder_right.y * image_height)),
                     (int(hip_right.x * image_width), int(hip_right.y * image_height)), (0, 255, 255), 2)

        # Mostramos la imagen
        cv2.imshow('Hands Tracking', image)

        # Salimos del bucle si se presiona la tecla 'q'
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

# Liberamos la cámara y cerramos las ventanas
capture.release()
cv2.destroyAllWindows()
