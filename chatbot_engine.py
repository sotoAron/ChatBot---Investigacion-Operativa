"""
chatbot_engine.py
=================
Motor del chatbot 4B. Integra el modelo Gemini con las herramientas de cálculo.
"""

import json
import os
import time
import uuid
import google.generativeai as genai

from config import CONTEXT_DIR, MODEL_NAME, SESSIONS_DIR
from prompts import SYSTEM_PROMPT
import solver

# ---------------------------------------------------------------------------
# Declaraciones de herramientas para Gemini (function calling)
# ---------------------------------------------------------------------------

_TOOLS = [
    genai.protos.Tool(
        function_declarations=[
            genai.protos.FunctionDeclaration(
                name="solve_quadratic_programming",
                description=(
                    "Resuelve un problema de Programación Cuadrática: "
                    "Z = C·X + (1/2) X^T D X  sujeto a  A X <= b, X >= 0. "
                    "Devuelve la solución óptima, el valor óptimo, la definitud "
                    "de D y si las condiciones KKT garantizan optimalidad global."
                ),
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        "variables": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.STRING)),
                        "c": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                        "D": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                        "A": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                        "b": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                        "sense": genai.protos.Schema(type=genai.protos.Type.STRING),
                    },
                    required=["variables", "c", "D", "sense"],
                ),
            ),
            genai.protos.FunctionDeclaration(
                name="solve_geometric_programming",
                description="Resuelve Programación Geométrica sin restricciones.",
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        "variables": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.STRING)),
                        "terms": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.OBJECT, properties={"c": genai.protos.Schema(type=genai.protos.Type.NUMBER), "exponents": genai.protos.Schema(type=genai.protos.Type.OBJECT)}, required=["c", "exponents"])),
                        "sense": genai.protos.Schema(type=genai.protos.Type.STRING),
                    },
                    required=["variables", "terms", "sense"],
                ),
            ),
            genai.protos.FunctionDeclaration(
                name="solve_separable_programming",
                description="Resuelve Programación Convexa Separable.",
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        "variables": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.STRING)),
                        "objective_terms": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.OBJECT, properties={"expr": genai.protos.Schema(type=genai.protos.Type.STRING), "lower": genai.protos.Schema(type=genai.protos.Type.NUMBER), "upper": genai.protos.Schema(type=genai.protos.Type.NUMBER)}, required=["expr", "lower", "upper"])),
                        "A": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                        "b": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                        "sense": genai.protos.Schema(type=genai.protos.Type.STRING),
                        "n_segments": genai.protos.Schema(type=genai.protos.Type.INTEGER),
                    },
                    required=["variables", "objective_terms", "sense"],
                ),
            ),
            genai.protos.FunctionDeclaration(
                name="solve_linear_combinations_method",
                description="Resuelve max/min f(X) con restricciones LINEALES usando Combinaciones Lineales.",
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        "variables": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.STRING)),
                        "objective": genai.protos.Schema(type=genai.protos.Type.STRING),
                        "A": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                        "b": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                        "sense": genai.protos.Schema(type=genai.protos.Type.STRING),
                        "x0": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                    },
                    required=["variables", "objective", "A", "b", "sense"],
                ),
            ),
            genai.protos.FunctionDeclaration(
                name="stochastic_to_deterministic",
                description="Convierte una restricción estocástica a determinista.",
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        "coef_deterministicos": genai.protos.Schema(type=genai.protos.Type.OBJECT),
                        "parametro_aleatorio": genai.protos.Schema(type=genai.protos.Type.STRING),
                        "media": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                        "desviacion_estandar": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                        "tipo_restriccion": genai.protos.Schema(type=genai.protos.Type.STRING),
                        "rhs": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                        "nivel_confianza": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                        "posicion": genai.protos.Schema(type=genai.protos.Type.STRING),
                    },
                    required=["coef_deterministicos", "parametro_aleatorio", "media", "desviacion_estandar", "tipo_restriccion", "rhs", "nivel_confianza"],
                ),
            ),
        ]
    )
]

_SOLVER_MAP = {
    "solve_quadratic_programming": solver.solve_quadratic_programming,
    "solve_geometric_programming": solver.solve_geometric_programming,
    "solve_separable_programming": solver.solve_separable_programming,
    "solve_linear_combinations_method": solver.solve_linear_combinations_method,
    "stochastic_to_deterministic": solver.stochastic_to_deterministic,
}

def _call_solver(name: str, args: dict) -> dict:
    fn = _SOLVER_MAP.get(name)
    if fn is None: return {"error": f"Herramienta desconocida: '{name}'."}
    try: return fn(**_deep_convert(args))
    except Exception as exc: return {"error": f"Error: {exc}"}

def _deep_convert(obj):
    if hasattr(obj, "items"): return {k: _deep_convert(v) for k, v in obj.items()}
    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)): return [_deep_convert(v) for v in obj]
    return obj

# ---------------------------------------------------------------------------
# Motor del chatbot (Bajo Nivel)
# ---------------------------------------------------------------------------

class ChatbotEngine:
    def __init__(self, api_key: str):
        self.api_key = api_key
        if api_key:
            genai.configure(api_key=api_key)

        self.context_content = self._get_context_content()
        self.full_instruction = SYSTEM_PROMPT
        if self.context_content:
            self.full_instruction += "\n\n--- MATERIAL DE REFERENCIA ---\n" + self.context_content

        self.model = None
        self.history = []
        self.current_session_id = None

    def _get_context_content(self) -> str | None:
        if not os.path.exists(CONTEXT_DIR): return None
        files = [f for f in os.listdir(CONTEXT_DIR) if f.endswith(".md")]
        if files:
            with open(os.path.join(CONTEXT_DIR, files[0]), "r", encoding="utf-8") as f:
                return f.read()
        return None

    def _is_solvable_math_problem(self, message: str) -> bool:
        classifier = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
        prompt = (
            "Eres un enrutador estricto. Analiza el mensaje del usuario.\n\n"
            "Responde 'CALCULAR' si el usuario presenta un enunciado matemático que contiene:\n"
            "1. Una función objetivo o meta clara.\n"
            "2. Al menos una restricción numérica explícita.\n"
            "REGLA OBLIGATORIA: Si parece un problema típico de examen/guía de estudios con números, RESPONDE 'CALCULAR'. Asume cualquier restricción faltante de sentido común.\n\n"
            "Responde 'TEXTO' ÚNICAMENTE si es un saludo, una pregunta teórica pura sin números, o una solicitud explícita de no resolver.\n\n"
            f"Mensaje del usuario: {message}"
        )
        try:
            res = classifier.generate_content(prompt)
            decision = "CALCULAR" in res.text.upper()
            print(f"\n[DEBUG ROUTER] Decisión: {'FORZAR CÁLCULO (ANY)' if decision else 'MODO TEXTO (AUTO)'}")
            return decision
        except Exception as e:
            print(f"\n[DEBUG ROUTER] ERROR crítico en el clasificador: {e}. Forzando cálculo.\n")
            return True

    def start_new_chat(self, session_id: str | None = None) -> str:
        self.current_session_id = session_id or str(uuid.uuid4())[:8]
        self.model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=self.full_instruction,
            tools=_TOOLS,
        )
        self.history = []
        return self.current_session_id

    def send_message(self, message: str):
        if self.model is None:
            self.start_new_chat()

        is_math_problem = self._is_solvable_math_problem(message)

        # Diccionarios puros (Cepo Inquebrantable)
        tool_cfg_any = {"function_calling_config": {"mode": "ANY"}}
        tool_cfg_auto = {"function_calling_config": {"mode": "AUTO"}}

        user_msg = genai.protos.Content(role="user", parts=[genai.protos.Part(text=message)])
        
        # Llamada estructural de bajo nivel
        response = self.model.generate_content(
            self.history + [user_msg],
            tool_config=tool_cfg_any if is_math_problem else tool_cfg_auto
        )
        
        self.history.append(user_msg)

        max_tool_rounds = 5
        for round_idx in range(max_tool_rounds):
            self.history.append(response.candidates[0].content)
            
            fc_parts = [p for p in response.parts if hasattr(p, "function_call") and p.function_call.name]
            if not fc_parts:
                break

            tool_response_parts = []
            for part in fc_parts:
                fc = part.function_call
                print(f"\n[DEBUG TOOL] El LLM solicitó ejecutar: {fc.name}")
                try:
                    print(f"[DEBUG TOOL] Argumentos recibidos: {fc.args}")
                except:
                    pass
                result = _call_solver(fc.name, fc.args)
                if "error" in result:
                    print(f"[DEBUG TOOL] ❌ ERROR DEL MOTOR MATEMÁTICO: {result['error']}")
                else:
                    print(f"[DEBUG TOOL] ✅ CÁLCULO EXITOSO. Z* = {result.get('valor_optimo', 'N/A')}\n")
                result["_RECORDATORIO_INTERNO_DEL_SISTEMA"] = "¡ATENCIÓN! OBLIGATORIO: 1) Genera TODAS las 6 secciones usando los TÍTULOS EXACTOS del System Prompt. 2) NUNCA uses nombres de funciones en código. 3) Redacta paso a paso sin resumir."
                tool_response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name, 
                            response={"result": json.dumps(result, ensure_ascii=False)}
                        )
                    )
                )

            tool_msg = genai.protos.Content(role="user", parts=tool_response_parts)
            
            response = self.model.generate_content(
                self.history + [tool_msg],
                tool_config=tool_cfg_auto
            )
            self.history.append(tool_msg)

        return response

    def save_session(self, ui_messages: list) -> None:
        if not self.current_session_id or self.model is None:
            return

        raw_history = []
        for msg in self.history:
            parts_data = []
            for part in msg.parts:
                if hasattr(part, "text") and part.text:
                    parts_data.append(part.text)
                elif hasattr(part, "function_call") and part.function_call.name:
                    parts_data.append("[Llamada a función interna delegada al backend]")
                elif hasattr(part, "function_response") and part.function_response.name:
                    parts_data.append("[Respuesta de función interna recibida]")
            raw_history.append({"role": msg.role, "parts": parts_data})

        data = {
            "session_id": self.current_session_id,
            "ui_messages": ui_messages,
            "raw_history": raw_history,
            "timestamp": time.time(),
        }

        file_path = os.path.join(SESSIONS_DIR, f"{self.current_session_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def load_session(self, session_id: str) -> list | None:
        file_path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if not os.path.exists(file_path):
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.current_session_id = session_id
        
        reconstructed_history = []
        for m in data["raw_history"]:
            parts = [genai.protos.Part(text=p) for p in m["parts"]]
            reconstructed_history.append(genai.protos.Content(role=m["role"], parts=parts))

        if self.model is None:
            self.model = genai.GenerativeModel(
                model_name=MODEL_NAME,
                system_instruction=self.full_instruction,
                tools=_TOOLS,
            )
            
        self.history = reconstructed_history
        return data["ui_messages"]

    def list_sessions(self) -> list:
        if not os.path.exists(SESSIONS_DIR): return []
        files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
        sessions = []
        for fname in files:
            try:
                with open(os.path.join(SESSIONS_DIR, fname), "r", encoding="utf-8") as s: data = json.load(s)
                title = next((msg["content"][:30] + "..." for msg in data["ui_messages"] if msg["role"] == "user"), "Nueva Conversación")
                sessions.append({"id": data["session_id"], "title": title, "time": data["timestamp"]})
            except Exception: continue
        return sorted(sessions, key=lambda x: x["time"], reverse=True)