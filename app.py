import streamlit as st
import os
from config import GEMINI_API_KEY, DEFAULT_MODEL
from chatbot_engine import ChatbotEngine

# Configuración de la interfaz en Streamlit
st.set_page_config(page_title="Tutor Académico - Gemini", layout="centered", page_icon="📚")
st.title("📚 Tutor de Cátedra Inteligente")

# --- BARRA LATERAL (CONFIGURACIÓN) ---
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Priorizar la API Key del .env, pero permitir sobreescribirla
    api_key_input = st.text_input(
        "Gemini API Key", 
        value=GEMINI_API_KEY if GEMINI_API_KEY and "TU_API_KEY" not in GEMINI_API_KEY else "",
        type="password", 
        help="Configurada en el archivo .env o ingresala aquí."
    )
    
    model_name = st.selectbox(
        "Modelo",
        ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash", "gemini-2.5-flash"],
        index=0,
        help="Si recibes error de cuota (429), intenta usar gemini-1.5-flash."
    )
    
    st.divider()
    st.header("📄 Material de Estudio")
    uploaded_file = st.file_uploader("Sube el PDF de la materia", type=["pdf"])
    
    st.divider()
    st.header("💾 Gestión de Sesión")
    save_name = st.text_input("Nombre del archivo", value="sesion_estudio.json")
    
    col1, col2 = st.columns(2)
    with col1:
        btn_save = st.button("💾 Guardar")
    with col2:
        btn_load = st.button("📂 Cargar")

# --- INICIALIZACIÓN DEL MOTOR ---
if api_key_input:
    # Usamos session_state para mantener la instancia del motor
    if "engine" not in st.session_state or st.session_state.engine.model_name != model_name:
        st.session_state.engine = ChatbotEngine(api_key=api_key_input, model_name=model_name)
    
    engine = st.session_state.engine

    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    if "current_file_name" not in st.session_state:
        st.session_state.current_file_name = None

    # --- LÓGICA DE ARCHIVOS ---
    if uploaded_file and uploaded_file.name != st.session_state.current_file_name:
        try:
            # Guardar temporalmente para subir a la API
            temp_path = f"temp_{uploaded_file.name}"
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            with st.spinner("Subiendo y analizando material..."):
                gemini_file = engine.upload_pdf(temp_path, uploaded_file.name)
                engine.initialize_with_pdf(gemini_file)
                
            os.remove(temp_path)
            st.session_state.current_file_name = uploaded_file.name
            st.session_state.messages.append({
                "role": "assistant", 
                "content": f"✅ Material **{uploaded_file.name}** cargado con éxito. ¿En qué puedo ayudarte hoy?"
            })
            st.rerun()
        except Exception as e:
            st.error(f"Error al procesar el PDF: {e}")

    # --- GUARDAR / CARGAR ---
    if btn_save:
        if engine.save_history(save_name, st.session_state.messages):
            st.sidebar.success("¡Chat guardado!")
        else:
            st.sidebar.error("Error al guardar.")

    if btn_load:
        chat, ui_msgs = engine.load_history(save_name)
        if chat:
            st.session_state.messages = ui_msgs
            st.sidebar.success("¡Chat cargado!")
            st.rerun()
        else:
            st.sidebar.error("Archivo no encontrado.")

    # --- INTERFAZ DE CHAT ---
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Haz una pregunta sobre la materia..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Pensando..."):
                try:
                    response = engine.send_message(prompt)
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Error: {e}")
else:
    st.warning("⚠️ Falta la API Key. Por favor configúrala en el archivo `.env` o en la barra lateral.")
    st.info("Puedes obtener una en [Google AI Studio](https://aistudio.google.com/app/apikey)")
