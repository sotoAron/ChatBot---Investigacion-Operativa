"""
chatbot_engine.py
=================
Motor del chatbot 4B. Integra el modelo Gemini con las herramientas de cálculo y
manejo estricto de intenciones (Teoría / Clasificar / Resolver).
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
            genai.protos.FunctionDeclaration(
                name="solve_two_stage_stochastic_lp",
                description=(
                    "Resuelve Programacion Estocastica Lineal de Dos Etapas con "
                    "Recurso (escenarios discretos de probabilidad conocida). "
                    "Usar cuando hay una decision de 1ra etapa tomada ANTES de "
                    "conocer el escenario, y una decision de recurso (2da etapa) "
                    "que se ajusta DESPUES de observado el escenario."
                ),
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        "variables_1ra_etapa": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.STRING)),
                        "c_1ra_etapa": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                        "variables_recurso": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.STRING)),
                        "A_1ra_etapa": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                        "b_1ra_etapa": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                        "escenarios": genai.protos.Schema(
                            type=genai.protos.Type.ARRAY,
                            items=genai.protos.Schema(
                                type=genai.protos.Type.OBJECT,
                                properties={
                                    "nombre": genai.protos.Schema(type=genai.protos.Type.STRING),
                                    "probabilidad": genai.protos.Schema(type=genai.protos.Type.NUMBER),
                                    "q_recurso": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                                    "T_matrix": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                                    "W_matrix": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER))),
                                    "h_rhs": genai.protos.Schema(type=genai.protos.Type.ARRAY, items=genai.protos.Schema(type=genai.protos.Type.NUMBER)),
                                },
                                required=["nombre", "probabilidad", "q_recurso", "T_matrix", "W_matrix", "h_rhs"],
                            ),
                        ),
                        "sense": genai.protos.Schema(type=genai.protos.Type.STRING),
                    },
                    required=["variables_1ra_etapa", "c_1ra_etapa", "variables_recurso", "escenarios", "sense"],
                ),
            ),
        ]
    )
]

_SOLVER_MAP = {
    "solve_quadratic_programming":    solver.solve_quadratic_programming,
    "solve_geometric_programming":    solver.solve_geometric_programming,
    "solve_separable_programming":    solver.solve_separable_programming,
    "solve_linear_combinations_method": solver.solve_linear_combinations_method,
    "stochastic_to_deterministic":    solver.stochastic_to_deterministic,
    "solve_two_stage_stochastic_lp":  solver.solve_two_stage_stochastic_lp,
}

# ---------------------------------------------------------------------------
# Listas de palabras clave para enrutamiento
# ---------------------------------------------------------------------------

_CALC_KEYWORDS = {
    "resolver", "resuelve", "resolvé", "resolví", "calcular", "calculá", "calcula",
    "hallar", "hallá", "halla", "obtén", "obtener", "encontrar la solución",
    "solución óptima", "valor óptimo", "minimizar", "maximizar", "optimizar",
    "desarrollá la resolución", "hacé el cálculo", "encontrá el valor",
    "determiná el óptimo", "determinar", "formule", "formular", "formula", "determinar el optimo", "determina el optimo"
}

_CLASS_KEYWORDS = {
    "clasificar", "clasificá", "clasificame", "clasifica", "identificar el método",
    "qué método", "qué enfoque", "qué modelo es", "características"
}

_THEORY_KEYWORDS = {
    "explicar", "explicá", "explica", "explicame", "qué es", "que es", "qué son",
    "teoría", "teoria", "concepto", "conceptualmente", "definición", "definicion",
    "comparar", "comparación", "diferencia entre", "cuándo usar", "cuando usar", 
    "por qué se usa", "para qué sirve", "justificar", "análisis teórico", 
    "me explicás", "en qué consiste", "cómo funciona"
}

# ---------------------------------------------------------------------------
# Motor del chatbot
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
        if not os.path.exists(CONTEXT_DIR):
            return None
        files = [f for f in os.listdir(CONTEXT_DIR) if f.endswith(".md")]
        if files:
            with open(os.path.join(CONTEXT_DIR, files[0]), "r", encoding="utf-8") as f:
                return f.read()
        return None

    def _detect_intent(self, msg: str) -> str:
        """Devuelve 'CALCULAR', 'CLASIFICAR' o 'TEORIA'."""
        lower = msg.lower()
        
        # Manejo de negaciones simples
        if "sin resolver" in lower or "no resuelvas" in lower:
            return "CLASIFICAR"
            
        if any(kw in lower for kw in _CALC_KEYWORDS):
            return "CALCULAR"
        elif any(kw in lower for kw in _CLASS_KEYWORDS):
            return "CLASIFICAR"
        elif any(kw in lower for kw in _THEORY_KEYWORDS):
            return "TEORIA"
            
        # Fallback con LLM si es ambiguo
        return self._classify_with_llm(msg)

    def _classify_with_llm(self, message: str) -> str:
        try:
            classifier = genai.GenerativeModel(model_name=MODEL_NAME)
            prompt = (
                "Responde ÚNICAMENTE con una de estas tres palabras: CALCULAR, CLASIFICAR o TEORIA.\n"
                "CALCULAR: si pide resolver problemas numéricos o hallar el óptimo.\n"
                "CLASIFICAR: si pide identificar el método o listar características sin resolver.\n"
                "TEORIA: para explicaciones conceptuales generales.\n"
                f"Mensaje: {message}"
            )
            res = classifier.generate_content(prompt)
            text = res.text.strip().upper()
            if text in ["CALCULAR", "CLASIFICAR", "TEORIA"]:
                return text
            return "TEORIA"  # Default seguro
        except Exception:
            return "TEORIA"

    def _call_solver(self, name: str, args: dict) -> dict:
        fn = _SOLVER_MAP.get(name)
        if fn is None:
            return {"error": f"Herramienta desconocida: '{name}'."}
        try:
            return fn(**self._deep_convert(args))
        except Exception as exc:
            return {"error": f"Error al ejecutar el cálculo: {exc}"}

    def _deep_convert(self, obj):
        if hasattr(obj, "items"):
            return {k: self._deep_convert(v) for k, v in obj.items()}
        if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
            return [self._deep_convert(v) for v in obj]
        return obj

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

        intent = self._detect_intent(message)
        
        # Configuraciones restrictivas de herramientas
        if intent == "CALCULAR":
            tool_cfg = {"function_calling_config": {"mode": "ANY"}}
            hidden_prompt = ""
        elif intent == "CLASIFICAR":
            tool_cfg = {"function_calling_config": {"mode": "NONE"}}
            hidden_prompt = "\n\n[INSTRUCCIÓN INTERNA OCULTA: El usuario solicitó CLASIFICAR. Analiza el problema, devuelve las características identificadas y cuál modelo es (secciones 1 a 5). Omite la resolución numérica. No menciones esta instrucción.]"
        else:  # TEORIA
            tool_cfg = {"function_calling_config": {"mode": "NONE"}}
            hidden_prompt = "\n\n[INSTRUCCIÓN INTERNA OCULTA: El usuario hizo una pregunta TEÓRICA. Responde en prosa directa explicando el concepto. No apliques la plantilla de resolución. No menciones esta instrucción.]"

        # Inyectamos el prompt oculto solo en la petición (no ensucia la UI)
        user_msg_with_hidden = genai.protos.Content(role="user", parts=[genai.protos.Part(text=message + hidden_prompt)])
        
        response = self.model.generate_content(
            self.history + [user_msg_with_hidden],
            tool_config=tool_cfg,
        )

        # Guardamos en el historial el mensaje original limpio
        clean_user_msg = genai.protos.Content(role="user", parts=[genai.protos.Part(text=message)])
        self.history.append(clean_user_msg)
        
        max_tool_rounds = 5
        for _round in range(max_tool_rounds):
            self.history.append(response.candidates[0].content)
            fc_parts = [p for p in response.parts if hasattr(p, "function_call") and p.function_call.name]
            if not fc_parts: break

            tool_response_parts = []
            for part in fc_parts:
                fc = part.function_call
                result = self._call_solver(fc.name, fc.args)
                result["_INSTRUCCION_FORMATO_OBLIGATORIA"] = "Usa LaTeX y títulos del prompt."
                tool_response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name,
                            response={"result": json.dumps(result, ensure_ascii=False)},
                        )
                    )
                )

            tool_msg = genai.protos.Content(role="user", parts=tool_response_parts)
            # Retornamos al modo AUTO para las subsecuentes iteraciones de herramientas si estamos en modo CALCULAR
            tool_cfg_auto = {"function_calling_config": {"mode": "AUTO"}}
            response = self.model.generate_content(self.history + [tool_msg], tool_config=tool_cfg_auto)
            self.history.append(tool_msg)

        return response

    def save_session(self, ui_messages: list, user_id: str = "default") -> None:
        if not self.current_session_id or self.model is None:
            return
        raw_history = []
        for msg in self.history:
            parts_data = [p.text for p in msg.parts if hasattr(p, "text") and p.text]
            if parts_data:
                raw_history.append({"role": msg.role, "parts": parts_data})

        data = {
            "session_id":  self.current_session_id,
            "user_id":     user_id,
            "ui_messages": ui_messages,
            "raw_history": raw_history,
            "timestamp":   time.time(),
        }
        file_path = os.path.join(SESSIONS_DIR, f"{self.current_session_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def load_session(self, session_id: str) -> list | None:
        file_path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if not os.path.exists(file_path): return None
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.current_session_id = session_id
        reconstructed_history = []
        for m in data["raw_history"]:
            valid_parts = [p for p in m.get("parts", []) if p and p.strip()]
            if not valid_parts: continue
            reconstructed_history.append(genai.protos.Content(role=m["role"], parts=[genai.protos.Part(text=p) for p in valid_parts]))
        self.model = genai.GenerativeModel(model_name=MODEL_NAME, system_instruction=self.full_instruction, tools=_TOOLS)
        self.history = reconstructed_history
        return data["ui_messages"]

    def get_user_sessions(self, user_id: str = "default") -> list:
        if not os.path.exists(SESSIONS_DIR): return []
        files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
        sessions = []
        for fname in files:
            try:
                with open(os.path.join(SESSIONS_DIR, fname), "r", encoding="utf-8") as s:
                    data = json.load(s)
                if data.get("user_id") != user_id: continue
                title = next((msg["content"][:40] + "..." for msg in data["ui_messages"] if msg["role"] == "user"), "Nueva Conversación")
                sessions.append({"id": data["session_id"], "title": title, "time": data["timestamp"]})
            except Exception: continue
        return sorted(sessions, key=lambda x: x["time"], reverse=True)