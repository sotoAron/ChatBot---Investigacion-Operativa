"""
chatbot_engine.py
=================
Motor del chatbot 4B. Integra el modelo Gemini con las herramientas de cálculo.

CORRECCIONES APLICADAS (v2):
  - Clasificador LLM reformulado sin regla agresiva de examen/guía.
  - Lógica de enrutamiento en 3 capas: keywords explícitas → LLM → fallback conservador.
  - Fallback del clasificador cambiado de True (CALCULAR) a False (TEXTO).
  - Recordatorio interno actualizado con reglas de formato matemático y prohibición de backend.
  - load_session filtra partes vacías para evitar errores en la reconstrucción del historial.
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
    "solve_quadratic_programming":    solver.solve_quadratic_programming,
    "solve_geometric_programming":    solver.solve_geometric_programming,
    "solve_separable_programming":    solver.solve_separable_programming,
    "solve_linear_combinations_method": solver.solve_linear_combinations_method,
    "stochastic_to_deterministic":    solver.stochastic_to_deterministic,
}

# ---------------------------------------------------------------------------
# Listas de palabras clave para enrutamiento pre-LLM
# Razón de diseño: usar regex/keywords es O(1) y elimina la mayoría de los
# casos claros sin gastar una llamada al clasificador LLM.
# ---------------------------------------------------------------------------

# Indican EXPLÍCITAMENTE que el usuario quiere un cálculo/resolución.
_CALC_KEYWORDS = {
    "resolver", "resuelve", "resolvé", "resolví",
    "calcular", "calculá", "calcula",
    "hallar", "hallá", "halla",
    "obtén", "obtener",
    "encontrar la solución", "encontrá la solución",
    "solución óptima", "valor óptimo",
    "minimizar", "maximizar", "optimizar",
    "desarrollá la resolución", "desarrollar la resolución",
    "hacé el cálculo", "hacer el cálculo",
    "encontrá el valor", "encontrar el valor",
    "determiná el óptimo", "determinar el óptimo","minimcen", 
    "maximizcen", "optimizcen","determinar","formule","formular"
    ,"formula"
}

# Indican EXPLÍCITAMENTE que el usuario quiere teoría/clasificación.
# Tienen PRIORIDAD MÁXIMA: si están presentes y no hay keywords de cálculo,
# el modo es siempre TEXTO, sin importar qué diga el clasificador LLM.
_THEORY_KEYWORDS = {
    "explicar", "explicá", "explica", "explicame", "explicame",
    "qué es", "que es", "qué son", "que son",
    "teoría", "teoria", "concepto", "conceptualmente",
    "definición", "definicion",
    "clasificar", "clasificá", "clasificame", "clasifica",
    "identificar el método", "identificar método", "qué método", "que método",
    "qué metodo", "que metodo", "qué enfoque", "que enfoque",
    "comparar", "comparación", "comparacion", "diferencia entre",
    "cuándo usar", "cuando usar", "cuándo aplica", "cuando aplica",
    "por qué se usa", "para qué sirve", "para que sirve",
    "justificar", "justificá",
    "análisis teórico", "analisis teorico",
    "me explicás", "me explicas", "podrías explicar",
    "en qué consiste", "en que consiste",
    "cómo funciona", "como funciona",
}


def _has_theory_intent(msg: str) -> bool:
    lower = msg.lower()
    return any(kw in lower for kw in _THEORY_KEYWORDS)


def _has_calc_intent(msg: str) -> bool:
    lower = msg.lower()
    return any(kw in lower for kw in _CALC_KEYWORDS)


def _call_solver(name: str, args: dict) -> dict:
    fn = _SOLVER_MAP.get(name)
    if fn is None:
        return {"error": f"Herramienta desconocida: '{name}'."}
    try:
        return fn(**_deep_convert(args))
    except Exception as exc:
        return {"error": f"Error al ejecutar el cálculo: {exc}"}


def _deep_convert(obj):
    if hasattr(obj, "items"):
        return {k: _deep_convert(v) for k, v in obj.items()}
    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        return [_deep_convert(v) for v in obj]
    return obj


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

    # ------------------------------------------------------------------
    # Contexto externo (material de referencia opcional)
    # ------------------------------------------------------------------

    def _get_context_content(self) -> str | None:
        if not os.path.exists(CONTEXT_DIR):
            return None
        files = [f for f in os.listdir(CONTEXT_DIR) if f.endswith(".md")]
        if files:
            with open(os.path.join(CONTEXT_DIR, files[0]), "r", encoding="utf-8") as f:
                return f.read()
        return None

    # ------------------------------------------------------------------
    # Enrutador de intención (3 capas)
    # ------------------------------------------------------------------

    def _is_calculation_mode(self, message: str) -> bool:
        """
        Determina si el mensaje requiere herramientas en modo ANY (cálculo forzado).

        Arquitectura de decisión en 3 capas:

        CAPA 1 — Keywords de teoría explícitas (máxima prioridad):
            Si el usuario pide explicación/clasificación/comparación sin
            pedir resolución → modo TEXTO siempre. Ahorra la llamada LLM
            y evita que el clasificador se equivoque en el caso más claro.

        CAPA 2 — Keywords de cálculo explícitas:
            Si el usuario pide resolver/calcular/hallar explícitamente →
            modo CALCULAR directamente.

        CAPA 3 — Clasificador LLM para casos ambiguos:
            Solo cuando las capas 1 y 2 no dan una respuesta clara.
            Fallback conservador a TEXTO ante cualquier error.
        """
        theory_intent = _has_theory_intent(message)
        calc_intent   = _has_calc_intent(message)

        # Capa 1: intención teórica sin intención de cálculo → TEXTO
        if theory_intent and not calc_intent:
            print("\n[ROUTER] Capa 1 — Intención TEÓRICA detectada → TEXTO (AUTO)")
            return False

        # Capa 2: intención de cálculo explícita → CALCULAR
        if calc_intent:
            print("\n[ROUTER] Capa 2 — Intención de CÁLCULO detectada → CALCULAR (ANY)")
            return True

        # Capa 3: ambiguo → clasificador LLM con fallback conservador
        return self._classify_with_llm(message)

    def _classify_with_llm(self, message: str) -> bool:
        """
        Clasificador LLM para mensajes ambiguos.
        Prompt sin regla agresiva de 'examen/guía'.
        Fallback conservador: ante cualquier error o duda → TEXTO (False).
        """
        try:
            classifier = genai.GenerativeModel(model_name="gemini-2.5-flash-lite")
            prompt = (
                "Eres un clasificador de intenciones. "
                "Responde ÚNICAMENTE con una de estas dos palabras: CALCULAR o TEXTO.\n\n"
                "Responde CALCULAR solo si el usuario pide EXPLÍCITAMENTE:\n"
                "- resolver un problema numérico\n"
                "- calcular un resultado o un óptimo\n"
                "- hallar/encontrar/obtener la solución\n"
                "- desarrollar la resolución matemática\n\n"
                "Responde TEXTO en TODOS los demás casos, incluyendo:\n"
                "- preguntas teóricas o conceptuales\n"
                "- pedidos de clasificación o identificación de método\n"
                "- comparaciones entre métodos\n"
                "- preguntas sobre cuándo usar un método\n"
                "- saludos o consultas generales\n"
                "- cualquier ambigüedad\n\n"
                "REGLA DE ORO: ante cualquier duda, responde TEXTO.\n\n"
                f"Mensaje a clasificar: {message}"
            )
            res = classifier.generate_content(prompt)
            decision = "CALCULAR" in res.text.upper()
            print(f"\n[ROUTER] Capa 3 (LLM) → {'CALCULAR (ANY)' if decision else 'TEXTO (AUTO)'}")
            return decision

        except Exception as e:
            # Fallback conservador: error del clasificador → TEXTO
            # Razón: es mejor dar una respuesta teórica cuando se esperaba
            # cálculo, que forzar herramientas en un contexto teórico.
            print(f"\n[ROUTER] ERROR en clasificador LLM: {e}. Fallback → TEXTO")
            return False

    # ------------------------------------------------------------------
    # Sesión y envío de mensajes
    # ------------------------------------------------------------------

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

        use_calc_mode = self._is_calculation_mode(message)

        tool_cfg_any  = {"function_calling_config": {"mode": "ANY"}}
        tool_cfg_auto = {"function_calling_config": {"mode": "AUTO"}}

        user_msg = genai.protos.Content(
            role="user",
            parts=[genai.protos.Part(text=message)]
        )

        response = self.model.generate_content(
            self.history + [user_msg],
            tool_config=tool_cfg_any if use_calc_mode else tool_cfg_auto,
        )

        self.history.append(user_msg)

        max_tool_rounds = 5
        for _round in range(max_tool_rounds):
            self.history.append(response.candidates[0].content)

            fc_parts = [
                p for p in response.parts
                if hasattr(p, "function_call") and p.function_call.name
            ]
            if not fc_parts:
                break

            tool_response_parts = []
            for part in fc_parts:
                fc = part.function_call
                print(f"\n[TOOL] Ejecutando: {fc.name}")
                try:
                    print(f"[TOOL] Args: {dict(fc.args)}")
                except Exception:
                    pass

                result = _call_solver(fc.name, fc.args)

                if "error" in result:
                    print(f"[TOOL] ❌ {result['error']}")
                else:
                    print(f"[TOOL] ✅ Z* = {result.get('valor_optimo', 'N/A')}")

                # Recordatorio interno inyectado en la respuesta de la herramienta.
                # Incluye reglas de formato matemático y prohibición de backend.
                # Se inyecta aquí porque Gemini lee el contenido de la función_response
                # antes de formular su respuesta final.
                result["_INSTRUCCION_FORMATO_OBLIGATORIA"] = (
                    "INSTRUCCIONES OBLIGATORIAS PARA REDACTAR LA RESPUESTA FINAL:\n"
                    "1. Usa las secciones con los TÍTULOS EXACTOS del System Prompt (sección 6).\n"
                    "2. PROHIBIDO mencionar nombres de funciones, módulos, APIs, procesos "
                    "internos o herramientas. Los nombres solve_quadratic_programming, "
                    "solve_geometric_programming, solve_separable_programming, "
                    "solve_linear_combinations_method y stochastic_to_deterministic son "
                    "información PRIVADA del sistema. Describe solo la matemática realizada.\n"
                    "3. FORMATO MATEMÁTICO OBLIGATORIO: toda expresión matemática usa LaTeX. "
                    "Fórmulas en línea: $...$  Fórmulas centradas: $$...$$ "
                    "PROHIBIDO: símbolos * como multiplicación, variables estilo código "
                    "(x1*x2, 16/x1), fragmentos LaTeX incompletos fuera de bloques.\n"
                    "4. Si el problema original tenía incertidumbre/parámetros aleatorios, "
                    "el MÉTODO PRINCIPAL en la sección 5 SIEMPRE es Programación Estocástica. "
                    "El método usado en la etapa determinista es AUXILIAR.\n"
                    "5. Si el usuario solo pidió clasificación o teoría (no resolución explícita), "
                    "OMITE la sección 6 de Resolución numérica."
                )

                tool_response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name,
                            response={"result": json.dumps(result, ensure_ascii=False)},
                        )
                    )
                )

            tool_msg = genai.protos.Content(role="user", parts=tool_response_parts)

            response = self.model.generate_content(
                self.history + [tool_msg],
                tool_config=tool_cfg_auto,
            )
            self.history.append(tool_msg)

        return response

    # ------------------------------------------------------------------
    # Persistencia de sesiones
    # ------------------------------------------------------------------

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
                    parts_data.append("[Llamada a módulo de cálculo interno]")
                elif hasattr(part, "function_response") and part.function_response.name:
                    parts_data.append("[Resultado de módulo de cálculo interno]")
            # Solo guardamos el turno si tiene al menos una parte con contenido
            if parts_data:
                raw_history.append({"role": msg.role, "parts": parts_data})

        data = {
            "session_id":  self.current_session_id,
            "ui_messages": ui_messages,
            "raw_history": raw_history,
            "timestamp":   time.time(),
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
            # Filtramos strings vacíos para evitar errores en la API de Gemini
            valid_parts = [p for p in m.get("parts", []) if p and p.strip()]
            if not valid_parts:
                continue
            parts = [genai.protos.Part(text=p) for p in valid_parts]
            reconstructed_history.append(
                genai.protos.Content(role=m["role"], parts=parts)
            )

        if self.model is None:
            self.model = genai.GenerativeModel(
                model_name=MODEL_NAME,
                system_instruction=self.full_instruction,
                tools=_TOOLS,
            )

        self.history = reconstructed_history
        return data["ui_messages"]

    def list_sessions(self) -> list:
        if not os.path.exists(SESSIONS_DIR):
            return []
        files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
        sessions = []
        for fname in files:
            try:
                with open(os.path.join(SESSIONS_DIR, fname), "r", encoding="utf-8") as s:
                    data = json.load(s)
                title = next(
                    (msg["content"][:40] + "..." for msg in data["ui_messages"] if msg["role"] == "user"),
                    "Nueva Conversación",
                )
                sessions.append({
                    "id":    data["session_id"],
                    "title": title,
                    "time":  data["timestamp"],
                })
            except Exception:
                continue
        return sorted(sessions, key=lambda x: x["time"], reverse=True)
