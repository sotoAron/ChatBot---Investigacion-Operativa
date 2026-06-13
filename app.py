import streamlit as st
import os
from config import GEMINI_API_KEY, MODEL_NAME
from chatbot_engine import ChatbotEngine

# Configuración de la interfaz
st.set_page_config(page_title="Tutor Académico - Gemini 2.5", layout="wide", page_icon="📚")

# --- ESTILO PERSONALIZADO ---
st.markdown("""
    <style>
    .stChatFloatingInputContainer { background-color: rgba(0,0,0,0) !important; }
    .sidebar-content { padding: 10px; }
    .new-chat-btn { width: 100%; margin-bottom: 20px; }
    </style>
""", unsafe_allow_html=True)

# --- INICIALIZACIÓN ---
if "engine" not in st.session_state:
    if not GEMINI_API_KEY or "TU_API_KEY" in GEMINI_API_KEY:
        st.error("❌ No se encontró una API Key válida en el archivo .env")
        st.stop()
    st.session_state.engine = ChatbotEngine(api_key=GEMINI_API_KEY)

engine = st.session_state.engine

if "messages" not in st.session_state:
    st.session_state.messages = []

# --- BARRA LATERAL (NAVEGACIÓN) ---
with st.sidebar:
    st.title("📚 Tutor IO")
    st.subheader(f"Modelo: {MODEL_NAME}")
    
    if st.button("➕ Nueva Conversación", type="primary", use_container_width=True):
        st.session_state.messages = []
        engine.start_new_chat()
        st.rerun()

    st.divider()
    st.subheader("🕒 Historial de Chats")
    
    sessions = engine.list_sessions()
    for session in sessions:
        if st.button(session["title"], key=session["id"], use_container_width=True):
            ui_msgs = engine.load_session(session["id"])
            if ui_msgs:
                st.session_state.messages = ui_msgs
                st.rerun()

# --- ÁREA PRINCIPAL ---
st.title("👨‍🏫 Asistente de Investigación Operativa")

# Mostrar mensajes
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Entrada de usuario
if prompt := st.chat_input("Haz tu consulta sobre la materia..."):
    # Si es el primer mensaje de una sesión vacía, iniciamos motor si no existe ID
    if not engine.current_session_id:
        engine.start_new_chat()

    # Agregar mensaje de usuario
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Generar respuesta
    with st.chat_message("assistant"):
        with st.spinner("Consultando material..."):
            try:
                response = engine.send_message(prompt)
                st.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                
                # GUARDADO AUTOMÁTICO
                engine.save_session(st.session_state.messages)
            except Exception as e:
                if "429" in str(e):
                    st.error("⚠️ Límite de cuota alcanzado. Espera un momento.")
                else:
                    st.error(f"Error: {e}")
