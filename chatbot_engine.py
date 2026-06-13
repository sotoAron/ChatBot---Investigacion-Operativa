import google.generativeai as genai
import time
import os
import json
import uuid
from prompts import SYSTEM_PROMPT
from config import MODEL_NAME, CONTEXT_DIR, SESSIONS_DIR

class ChatbotEngine:
    def __init__(self, api_key):
        self.api_key = api_key
        if api_key:
            genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(MODEL_NAME)
        self.chat = None
        self.current_session_id = None
        self.gemini_file = None

    def _get_context_file(self):
        """Busca el primer PDF en la carpeta context."""
        if not os.path.exists(CONTEXT_DIR):
            return None
        files = [f for f in os.listdir(CONTEXT_DIR) if f.endswith(".pdf")]
        return os.path.join(CONTEXT_DIR, files[0]) if files else None

    def start_new_chat(self, session_id=None):
        """Inicia un nuevo chat. Si no hay session_id, genera uno nuevo."""
        self.current_session_id = session_id or str(uuid.uuid4())[:8]
        self.chat = self.model.start_chat(history=[])
        
        # Cargar contexto automáticamente si existe
        pdf_path = self._get_context_file()
        if pdf_path:
            file_name = os.path.basename(pdf_path)
            self.gemini_file = genai.upload_file(path=pdf_path, mime_type="application/pdf", display_name=file_name)
            while self.gemini_file.state.name == "PROCESSING":
                time.sleep(1)
                self.gemini_file = genai.get_file(self.gemini_file.name)
            
            # Inicializar con el PDF y el prompt del sistema
            self.chat.send_message([self.gemini_file, SYSTEM_PROMPT])
        
        return self.current_session_id

    def send_message(self, message):
        """Envía un mensaje y retorna la respuesta."""
        if not self.chat:
            self.start_new_chat()
        return self.chat.send_message(message)

    def save_session(self, ui_messages):
        """Guarda la sesión actual automáticamente."""
        if not self.current_session_id or not self.chat:
            return
            
        raw_history = []
        for msg in self.chat.history:
            parts_data = [part.text for part in msg.parts if hasattr(part, 'text') and part.text]
            raw_history.append({"role": msg.role, "parts": parts_data})
            
        data = {
            "session_id": self.current_session_id,
            "ui_messages": ui_messages,
            "raw_history": raw_history,
            "timestamp": time.time()
        }
        
        file_path = os.path.join(SESSIONS_DIR, f"{self.current_session_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def load_session(self, session_id):
        """Carga una sesión existente."""
        file_path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if not os.path.exists(file_path):
            return None
            
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        self.current_session_id = session_id
        reconstructed_history = [{"role": m["role"], "parts": m["parts"]} for m in data["raw_history"]]
        self.chat = self.model.start_chat(history=reconstructed_history)
        return data["ui_messages"]

    def list_sessions(self):
        """Lista todas las sesiones guardadas, ordenadas por fecha."""
        if not os.path.exists(SESSIONS_DIR):
            return []
        files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
        sessions = []
        for f in files:
            path = os.path.join(SESSIONS_DIR, f)
            with open(path, "r", encoding="utf-8") as s:
                data = json.load(s)
                # Usar el primer mensaje del usuario como título si existe
                title = "Nueva Conversación"
                for msg in data["ui_messages"]:
                    if msg["role"] == "user":
                        title = msg["content"][:30] + "..."
                        break
                sessions.append({"id": data["session_id"], "title": title, "time": data["timestamp"]})
        
        return sorted(sessions, key=lambda x: x["time"], reverse=True)
