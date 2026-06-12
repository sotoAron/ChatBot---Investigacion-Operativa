import google.generativeai as genai
import time
import os
import json
from prompts import SYSTEM_PROMPT

class ChatbotEngine:
    def __init__(self, api_key, model_name="gemini-1.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        if api_key:
            genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
        self.chat = None

    def start_new_chat(self, history=None):
        # history puede ser una lista de diccionarios con la estructura:
        # [{"role": "user", "parts": ["texto"]}, {"role": "model", "parts": ["texto"]}]
        self.chat = self.model.start_chat(history=history or [])
        return self.chat

    def upload_pdf(self, file_path, file_name):
        """Sube un PDF a Gemini API y espera a que esté activo."""
        gemini_file = genai.upload_file(path=file_path, mime_type="application/pdf", display_name=file_name)
        
        while gemini_file.state.name == "PROCESSING":
            time.sleep(1)
            gemini_file = genai.get_file(gemini_file.name)
            
        return gemini_file

    def initialize_with_pdf(self, gemini_file):
        """Envía el prompt inicial con el archivo PDF vinculado."""
        if not self.chat:
            self.start_new_chat()
        
        # Enviamos el archivo y el prompt del sistema como primer mensaje
        response = self.chat.send_message([gemini_file, SYSTEM_PROMPT])
        return response.text

    def send_message(self, message):
        """Envía un mensaje de texto al chat actual."""
        if not self.chat:
            self.start_new_chat()
        return self.chat.send_message(message)

    def save_history(self, file_path, ui_messages):
        """Guarda el historial del chat en un archivo JSON."""
        if not self.chat:
            return False
            
        raw_history = []
        for msg in self.chat.history:
            parts_data = []
            for part in msg.parts:
                if hasattr(part, 'text') and part.text:
                    parts_data.append(part.text)
            raw_history.append({
                "role": msg.role,
                "parts": parts_data
            })
            
        data_to_save = {
            "ui_messages": ui_messages,
            "raw_history": raw_history
        }
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        return True

    def load_history(self, file_path):
        """Carga el historial desde un archivo JSON."""
        if not os.path.exists(file_path):
            return None, None
            
        with open(file_path, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)
            
        # El historial se puede reconstruir como una lista de diccionarios
        reconstructed_history = []
        for msg in loaded_data["raw_history"]:
            reconstructed_history.append({
                "role": msg["role"],
                "parts": msg["parts"]
            })
            
        self.chat = self.model.start_chat(history=reconstructed_history)
        return self.chat, loaded_data["ui_messages"]
