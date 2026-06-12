import os
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Puedes agregar más configuraciones aquí si es necesario
DEFAULT_MODEL = "gemini-2.0-flash"
ALLOWED_EXTENSIONS = {"pdf"}
