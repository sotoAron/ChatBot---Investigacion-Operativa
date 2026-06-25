import os
import streamlit as st
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Intentar cargar desde Streamlit Secrets (ideal para Cloud)
# Si no existe, intentar desde variables de entorno / .env (ideal para local)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY") 

# Configuración intrínseca
MODEL_NAME = "gemini-2.5-flash"

# Rutas del sistema
CONTEXT_DIR = "context"
SESSIONS_DIR = "sessions"

# Asegurar que los directorios existan
os.makedirs(CONTEXT_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)