import cv2
import os

# Directorio donde se guardarán las imágenes etiquetadas
output_dir = "emotions_dataset"
# os.makedirs(output_dir, exist_ok=True)

# Tamaño deseado para las imágenes (puedes ajustarlo según tus necesidades)
# target_width = 400
# target_height = 300

# Inicializa la cámara
cap = cv2.VideoCapture(0)

# Mapeo de etiquetas de emociones
emotions = {
    0: "feliz",
    1: "triste",
    2: "sorprendido",
    3: "neutral",
    4: "enojado"
}

count = 1

while count <= 2000:
    ret, frame = cap.read()
    # if not ret:
    #     break

    # Convierte la imagen a escala de grises
    # gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Redimensiona la imagen al tamaño deseado
    # resized_frame = cv2.resize(gray_frame, (target_width, target_height))

    # CAMBIAR LA EMOCION
    emotion_label = emotions[4]

    # Muestra la imagen en una ventana
    cv2.imshow("Captura de Emocion", frame)

    filename = f"{emotion_label}_{count}.jpg"
    filepath = os.path.join(output_dir, filename)
    cv2.imwrite(filepath, frame)
    print(f"{filename}")

    count += 1

# Limpieza
cap.release()
cv2.destroyAllWindows()
