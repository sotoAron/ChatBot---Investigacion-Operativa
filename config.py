import os
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuración intrínseca
MODEL_NAME = "gemini-2.5-flash"

# Rutas del sistema
CONTEXT_DIR = "context"
SESSIONS_DIR = "sessions"

# Asegurar que los directorios existan
os.makedirs(CONTEXT_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)
