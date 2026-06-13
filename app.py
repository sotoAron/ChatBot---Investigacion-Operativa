import streamlit as st
import os
from config import GEMINI_API_KEY, MODEL_NAME
from chatbot_engine import ChatbotEngine

# Configuración de la interfaz
st.set_page_config(
    page_title="Chatbot de PNL", 
    layout="wide", 
    page_icon=":material/school:"
)

# --- ESTILO PERSONALIZADO ---
st.markdown("""
    <style>
    /* Estilo general y fuentes */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    /* Contenedor de entrada de chat */
    .stChatFloatingInputContainer { 
        background-color: transparent !important; 
        padding-bottom: 20px;
    }
    
    /* Botones de inicio (Starters) */
    .starter-card {
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 15px;
        transition: all 0.3s ease;
        cursor: pointer;
        background-color: white;
        height: 100px;
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
    }
    .starter-card:hover {
        border-color: #4A90E2;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        transform: translateY(-2px);
    }
    
    /* Sidebar styling - Customized Colors */
    section[data-testid="stSidebar"] {
        background-color: #F0F2F6 !important; /* Volvemos al color claro solicitado */
        border-right: 1px solid #e0e0e0;
    }
    
    /* Primary button (Nueva Conversación) - Target by data-testid */
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
        background-color: #FF4B4B !important;
        color: white !important;
        border: none !important;
        width: 100%;
    }
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover {
        background-color: #D32F2F !important;
    }

    /* Secondary buttons (History) - Target by data-testid */
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] {
        background-color: white !important;
        color: #31333F !important;
        border: 1px solid #4A90E2 !important;
        text-align: left !important;
        justify-content: flex-start !important;
        width: 100%;
    }
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover {
        background-color: #f0f7ff !important;
        border-color: #1E88E5 !important;
    }

    /* Text contrast in sidebar (Dark text on light background) */
    section[data-testid="stSidebar"] .stMarkdown h3,
    section[data-testid="stSidebar"] .stMarkdown h5 {
        color: #1e293b !important;
        font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] .stCaption {
        color: #475569 !important;
    }
    section[data-testid="stSidebar"] p {
        color: #31333F !important;
    }
    
    /* Ajustes de títulos */
    .main-title {
        font-weight: 600;
        color: #1e293b;
        margin-bottom: 0px;
    }
    .sub-title {
        color: #64748b;
        margin-bottom: 30px;
    }
    </style>
""", unsafe_allow_html=True)

# --- INICIALIZACIÓN ---
if "engine" not in st.session_state:
    if not GEMINI_API_KEY or "TU_API_KEY" in GEMINI_API_KEY:
        st.error("❌ No se detectó una API Key válida. Si estás en local, revisa tu archivo .env. Si estás en la nube, configura los 'Secrets' en Streamlit Cloud.")
        st.stop()
    st.session_state.engine = ChatbotEngine(api_key=GEMINI_API_KEY)

engine = st.session_state.engine

if "messages" not in st.session_state:
    st.session_state.messages = []

# --- BARRA LATERAL ---
with st.sidebar:
    st.markdown("### CONVERSACIONES")
    
    if st.button("Nueva Conversación", icon=":material/add:", type="primary", use_container_width=True):
        st.session_state.messages = []
        engine.start_new_chat()
        st.rerun()

    st.divider()
    st.markdown("##### :material/history: Historial")
    
    sessions = engine.list_sessions()
    if not sessions:
        st.caption("No hay chats previos")
    
    for session in sessions:
        if st.button(session["title"], key=session["id"], icon=":material/chat_bubble:", use_container_width=True):
            ui_msgs = engine.load_session(session["id"])
            if ui_msgs:
                st.session_state.messages = ui_msgs
                st.rerun()

# --- ÁREA PRINCIPAL ---

# Header
st.markdown('<h1 class="main-title">Asistente de Investigación Operativa</h1>', unsafe_allow_html=True)
st.markdown('<p class="sub-title">Consulta la teoría y resuelve problemas de Programacion No Lineal</p>', unsafe_allow_html=True)

# Mostrar mensajes existentes
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user" else ":material/smart_toy:"):
        st.markdown(msg["content"])

# --- LÓGICA DE STARTER BUTTONS ---
# Solo se muestran si no hay mensajes
placeholder_input = "Haz tu consulta sobre la materia..."
selected_starter = None

if not st.session_state.messages:
    st.markdown("#### ¿Cómo puedo ayudarte hoy?")
    cols = st.columns(2)
    
    starters = [
        {
            "icon": ":material/account_tree:", 
            "text": "Clasificar mi problema de PNL", 
            "prompt": "Hola, necesito ayuda para identificar qué método de Programación No Lineal restringida debo usar. ¿Podemos empezar con las preguntas de clasificación?"
        },
        {
            "icon": ":material/query_stats:", 
            "text": "Parámetros con incertidumbre", 
            "prompt": "Tengo un problema donde algunos coeficientes son aleatorios (distribución normal). ¿Cómo lo planteo mediante Programación Estocástica?"
        },
        {
            "icon": ":material/functions:", 
            "text": "Programación Cuadrática", 
            "prompt": "Mi función objetivo es cuadrática y las restricciones son lineales. ¿Cómo aplico las condiciones KKT y el método de las dos fases?"
        },
        {
            "icon": ":material/rebase_edit:", 
            "text": "Combinaciones Lineales", 
            "prompt": "¿Cómo funciona el procedimiento iterativo del método de Combinaciones Lineales (Zoutendijk) para restricciones lineales?"
        }
    ]
    
    for i, starter in enumerate(starters):
        with cols[i % 2]:
            if st.button(f"{starter['icon']} {starter['text']}", use_container_width=True, key=f"starter_{i}"):
                selected_starter = starter['prompt']

# Entrada de usuario (Input)
prompt = st.chat_input(placeholder_input)

# Si se seleccionó un starter, lo tratamos como un prompt
if selected_starter:
    prompt = selected_starter

# --- PROCESAMIENTO DEL MENSAJE ---
if prompt:
    # Si es el primer mensaje, iniciamos motor
    if not engine.current_session_id:
        engine.start_new_chat()

    # Agregar mensaje de usuario
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(prompt)

    # Generar respuesta
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        with st.spinner("Analizando..."):
            try:
                response = engine.send_message(prompt)
                st.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                
                # GUARDADO AUTOMÁTICO
                engine.save_session(st.session_state.messages)
                
                # Forzar refresh si fue un starter para que desaparezcan los botones
                if selected_starter:
                    st.rerun()
                    
            except Exception as e:
                if "429" in str(e):
                    st.error("Límite de cuota alcanzado. Espera un momento antes de volver a intentar.")
                else:
                    st.error(f"Se produjo un error: {e}")
