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
        
        self.context_content = self._get_context_content()
        
        full_instruction = SYSTEM_PROMPT
        if self.context_content:
            full_instruction += f"\n\n--- MATERIAL DE REFERENCIA ---\n{self.context_content}"
            
        self.model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=full_instruction
        )
        self.chat = None
        self.current_session_id = None

    def _get_context_content(self):
        if not os.path.exists(CONTEXT_DIR):
            return None
        files = [f for f in os.listdir(CONTEXT_DIR) if f.endswith(".md")]
        if files:
            with open(os.path.join(CONTEXT_DIR, files[0]), "r", encoding="utf-8") as f:
                return f.read()
        return None

    def start_new_chat(self, session_id=None):
        self.current_session_id = session_id or str(uuid.uuid4())[:8]
        self.chat = self.model.start_chat(history=[])
        return self.current_session_id

    def send_message(self, message):
        if not self.chat:
            self.start_new_chat()
        return self.chat.send_message(message)

    def save_session(self, ui_messages, user_id):
        """Guarda la sesión incluyendo el ID del usuario para privacidad."""
        if not self.current_session_id or not self.chat:
            return
            
        raw_history = []
        for msg in self.chat.history:
            parts_data = [part.text for part in msg.parts if hasattr(part, 'text') and part.text]
            raw_history.append({"role": msg.role, "parts": parts_data})
            
        data = {
            "session_id": self.current_session_id,
            "user_id": user_id,  # Agregamos el ID del usuario
            "ui_messages": ui_messages,
            "raw_history": raw_history,
            "timestamp": time.time()
        }
        
        file_path = os.path.join(SESSIONS_DIR, f"{self.current_session_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def load_session(self, session_id):
        file_path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if not os.path.exists(file_path):
            return None
            
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        self.current_session_id = session_id
        reconstructed_history = [{"role": m["role"], "parts": m["parts"]} for m in data["raw_history"]]
        self.chat = self.model.start_chat(history=reconstructed_history)
        return data["ui_messages"]

    def list_sessions(self, user_id):
        """Lista solo las sesiones que pertenecen al user_id actual."""
        if not os.path.exists(SESSIONS_DIR):
            return []
        files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
        sessions = []
        for f in files:
            path = os.path.join(SESSIONS_DIR, f)
            try:
                with open(path, "r", encoding="utf-8") as s:
                    data = json.load(s)
                    # FILTRADO POR USUARIO
                    if data.get("user_id") == user_id:
                        title = "Nueva Conversación"
                        for msg in data["ui_messages"]:
                            if msg["role"] == "user":
                                title = msg["content"][:30] + "..."
                                break
                        sessions.append({"id": data["session_id"], "title": title, "time": data["timestamp"]})
            except Exception:
                continue
        
        return sorted(sessions, key=lambda x: x["time"], reverse=True)
