

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import sympy as sp
from scipy.optimize import linprog, minimize, minimize_scalar
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Utilidades comunes
# ---------------------------------------------------------------------------

def _build_symbols(variables: Sequence[str]) -> Dict[str, sp.Symbol]:
    """Crea símbolos de sympy a partir de nombres de variables (strings)."""
    return {v: sp.symbols(v, real=True) for v in variables}


def _parse_expr(expr_str: str, symbols: Dict[str, sp.Symbol]) -> sp.Expr:
    """Convierte un string matemático en una expresión simbólica de sympy.

    Se usa ``sympy.sympify`` con un diccionario de variables conocido para
    evitar que sympy interprete, por ejemplo, ``x`` y ``X`` como símbolos
    distintos sin querer, y para dar errores claros si el string contiene
    una variable no declarada.
    """
    try:
        expr = sp.sympify(expr_str, locals=dict(symbols))
    except (sp.SympifyError, TypeError, SyntaxError) as exc:
        raise ValueError(
            f"No se pudo interpretar la expresión '{expr_str}': {exc}"
        ) from exc

    extra = expr.free_symbols - set(symbols.values())
    if extra:
        nombres = ", ".join(str(s) for s in extra)
        raise ValueError(
            f"La expresión '{expr_str}' usa variable(s) no declaradas: {nombres}"
        )
    return expr


def _round_dict(values: Dict[str, float], ndigits: int = 6) -> Dict[str, float]:
    return {k: round(float(v), ndigits) for k, v in values.items()}


def _hessian_definiteness(H: np.ndarray) -> str:
    """Clasifica una matriz hessiana simétrica según sus autovalores.

    Devuelve una de: "definida positiva", "definida negativa",
    "semidefinida positiva", "semidefinida negativa", "indefinida".
    """
    eigvals = np.linalg.eigvalsh(H)
    tol = 1e-9
    if np.all(eigvals > tol):
        return "definida positiva"
    if np.all(eigvals < -tol):
        return "definida negativa"
    if np.all(eigvals >= -tol) and np.any(np.abs(eigvals) <= tol):
        return "semidefinida positiva"
    if np.all(eigvals <= tol) and np.any(np.abs(eigvals) <= tol):
        return "semidefinida negativa"
    return "indefinida"


def _evaluate_linear_constraints(
    A: Sequence[Sequence[float]],
    b: Sequence[float],
    x: Sequence[float],
    tol: float = 1e-6,
) -> List[Dict[str, Any]]:
    """Evalúa AX <= b en el punto x e indica cuáles restricciones están activas."""
    A = np.array(A, dtype=float)
    b = np.array(b, dtype=float)
    x = np.array(x, dtype=float)
    lhs = A @ x
    out = []
    for i in range(len(b)):
        holgura = float(b[i] - lhs[i])
        out.append(
            {
                "restriccion": i + 1,
                "lado_izquierdo": round(float(lhs[i]), 6),
                "lado_derecho": float(b[i]),
                "holgura": round(holgura, 6),
                "activa": abs(holgura) <= tol,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 1. Núcleo NLP genérico (usado por QP y por Combinaciones Lineales)
# ---------------------------------------------------------------------------

def _solve_generic_nlp(
    objective: str,
    variables: Sequence[str],
    sense: str,
    A: Optional[Sequence[Sequence[float]]] = None,
    b: Optional[Sequence[float]] = None,
    bounds: Optional[Sequence[Tuple[Optional[float], Optional[float]]]] = None,
    x0: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Resuelve  max/min f(X)  sujeto a  A X <= b ,  bounds en cada x_i.

    Usa SLSQP con múltiples puntos de partida para mitigar óptimos locales
    (relevante sobre todo para Combinaciones Lineales, donde f no tiene por
    qué ser convexa/cóncava global).
    """
    if sense not in ("max", "min"):
        return {"error": "sense debe ser 'max' o 'min'."}

    symbols = _build_symbols(variables)
    try:
        f_expr = _parse_expr(objective, symbols)
    except ValueError as exc:
        return {"error": str(exc)}

    var_syms = [symbols[v] for v in variables]
    grad_expr = [sp.diff(f_expr, s) for s in var_syms]
    hess_expr = sp.hessian(f_expr, var_syms)

    f_num = sp.lambdify(var_syms, f_expr, "numpy")
    grad_num = sp.lambdify(var_syms, grad_expr, "numpy")
    hess_num = sp.lambdify(var_syms, hess_expr, "numpy")

    sign = -1.0 if sense == "max" else 1.0

    def objective_fn(x):
        return sign * float(f_num(*x))

    def jac_fn(x):
        return sign * np.array(grad_num(*x), dtype=float)

    n = len(variables)
    if bounds is None:
        bounds = [(0.0, None) for _ in range(n)]

    constraints = []
    if A is not None and b is not None:
        A_arr = np.array(A, dtype=float)
        b_arr = np.array(b, dtype=float)
        if A_arr.shape[0] != len(b_arr) or A_arr.shape[1] != n:
            return {
                "error": (
                    f"Dimensiones inconsistentes: A es {A_arr.shape}, "
                    f"b tiene {len(b_arr)} elementos, hay {n} variables."
                )
            }
        # scipy.linprog/minimize usan restricciones de la forma g(x) >= 0,
        # por lo que A X <= b  se reescribe como  b - A X >= 0.
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda x, A=A_arr, b=b_arr: b - A @ x,
                "jac": lambda x, A=A_arr: -A,
            }
        )

    # Múltiples puntos de partida dentro de la región (heurística simple).
    starts: List[np.ndarray] = []
    if x0 is not None:
        starts.append(np.array(x0, dtype=float))
    starts.append(np.zeros(n))
    if A is not None and b is not None:
        # punto "intermedio" entre el origen y un extremo factible aproximado
        b_arr = np.array(b, dtype=float)
        A_arr = np.array(A, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where(A_arr != 0, b_arr[:, None] / A_arr, np.inf)
        approx_max = np.nanmin(np.where(ratios > 0, ratios, np.inf), axis=0)
        approx_max = np.where(np.isfinite(approx_max), approx_max, 1.0)
        starts.append(approx_max / 2.0)
        starts.append(approx_max / 4.0)

    best = None
    for start in starts:
        try:
            res = minimize(
                objective_fn,
                start,
                jac=jac_fn,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-12},
            )
        except Exception:  # noqa: BLE001 - cualquier falla numérica puntual
            continue
        if not res.success:
            continue
        if best is None or res.fun < best.fun - 1e-9:
            best = res

    if best is None:
        return {
            "error": (
                "El optimizador numérico (SLSQP) no logró converger con "
                "los datos provistos. Verifique que la región factible no "
                "sea vacía y que la función objetivo esté bien definida."
            )
        }

    x_star = best.x
    f_star = sign * best.fun
    grad_at_x = np.array(grad_num(*x_star), dtype=float)
    hess_at_x = np.array(hess_num(*x_star), dtype=float)

    result: Dict[str, Any] = {
        "exito": True,
        "sentido": sense,
        "funcion_objetivo": str(f_expr),
        "gradiente_simbolico": [str(g) for g in grad_expr],
        "hessiana_simbolica": [[str(h) for h in row] for row in hess_expr.tolist()],
        "x_optimo": _round_dict({v: x for v, x in zip(variables, x_star)}),
        "valor_optimo": round(float(f_star), 6),
        "gradiente_en_optimo": _round_dict(
            {v: g for v, g in zip(variables, grad_at_x)}
        ),
        "hessiana_en_optimo": hess_at_x.round(6).tolist(),
        "hessiana_definitud": _hessian_definiteness(hess_at_x),
    }

    if A is not None and b is not None:
        result["restricciones"] = _evaluate_linear_constraints(A, b, x_star)

    return result


# ---------------------------------------------------------------------------
# 2. Programación Cuadrática
# ---------------------------------------------------------------------------

def solve_quadratic_programming(
    variables: List[str],
    c: List[float],
    D: List[List[float]],
    A: Optional[List[List[float]]] = None,
    b: Optional[List[float]] = None,
    sense: str = "max",
) -> Dict[str, Any]:
    """Resuelve  Z = C·X + (1/2) X^T D X  sujeto a  A X <= b, X >= 0.

    Parámetros
    ----------
    variables : nombres de las variables de decisión, en orden, ej. ["x", "y"].
    c : vector de coeficientes lineales C (mismo orden que ``variables``).
    D : matriz simétrica de coeficientes cuadráticos (n x n).
    A, b : restricciones lineales A X <= b (opcionales si no hay restricciones
        además de X >= 0).
    sense : "max" o "min".

    Devuelve, además de la solución, la clasificación de definitud de D
    (necesaria para que el chatbot explique si las condiciones KKT
    garantizan o no un óptimo global) y la verificación de las
    condiciones KKT en el punto hallado.
    """
    n = len(variables)
    c_arr = np.array(c, dtype=float)
    D_arr = np.array(D, dtype=float)

    if c_arr.shape != (n,):
        return {"error": f"El vector C debe tener {n} elementos."}
    if D_arr.shape != (n, n):
        return {"error": f"La matriz D debe ser {n}x{n}."}
    if not np.allclose(D_arr, D_arr.T, atol=1e-9):
        return {
            "error": (
                "La matriz D no es simétrica. En programación cuadrática "
                "D debe ser simétrica; revise los coeficientes cruzados "
                "(el coeficiente de x_i*x_j debe repartirse simétricamente "
                "entre D[i][j] y D[j][i])."
            )
        }

    # Construcción simbólica de Z = C·X + 1/2 X^T D X
    symbols = _build_symbols(variables)
    var_syms = [symbols[v] for v in variables]
    X = sp.Matrix(var_syms)
    Cm = sp.Matrix(c_arr.tolist())
    Dm = sp.Matrix(D_arr.tolist())
    f_expr = (Cm.T * X)[0, 0] + sp.Rational(1, 2) * (X.T * Dm * X)[0, 0]
    f_expr = sp.expand(f_expr)

    definitud_D = _hessian_definiteness(D_arr)

    nlp = _solve_generic_nlp(
        objective=str(f_expr),
        variables=variables,
        sense=sense,
        A=A,
        b=b,
    )
    if "error" in nlp:
        return nlp

    # ¿La definitud de D garantiza optimalidad GLOBAL bajo KKT?
    if sense == "max":
        global_garantizado = definitud_D in (
            "definida negativa",
            "semidefinida negativa",
        )
    else:
        global_garantizado = definitud_D in (
            "definida positiva",
            "semidefinida positiva",
        )

    nlp["matriz_D"] = D_arr.tolist()
    nlp["definitud_D"] = definitud_D
    nlp["optimo_global_garantizado_por_KKT"] = global_garantizado
    nlp["nota_definitud"] = (
        "Como D es "
        + definitud_D
        + (
            ", la función objetivo es "
            + (
                "cóncava"
                if definitud_D.endswith("negativa")
                else "convexa"
                if definitud_D.endswith("positiva")
                else "ni convexa ni cóncava"
            )
        )
        + ". Dado que el espacio de soluciones definido por A X <= b, X >= 0 "
        "es siempre convexo, "
        + (
            "las condiciones KKT son necesarias y suficientes, por lo que "
            "el punto hallado es un óptimo GLOBAL."
            if global_garantizado
            else "las condiciones KKT solo garantizan un óptimo LOCAL "
            "(no se puede asegurar optimalidad global con la definitud "
            "obtenida)."
        )
    )
    return nlp


# ---------------------------------------------------------------------------
# 3. Programación Geométrica (posinomios, sin restricciones)
# ---------------------------------------------------------------------------

def solve_geometric_programming(
    variables: List[str],
    terms: List[Dict[str, Any]],
    sense: str = "min",
) -> Dict[str, Any]:
    """Resuelve  min (o max) f(X) = sum_i c_i * prod_j x_j^{a_ij}  sin restricciones.

    Parámetros
    ----------
    variables : nombres de las variables, ej. ["x1", "x2"].
    terms : lista de términos del posinomio. Cada término es
        ``{"c": <coeficiente positivo>, "exponents": {"x1": a1, "x2": a2, ...}}``.
        Los exponentes de variables ausentes en un término se asumen 0.
    sense : "min" (caso típico del curso) o "max".

    Procedimiento (dualidad de Programación Geométrica)
    -----------------------------------------------------
    1. Se calculan los "pesos" w_i resolviendo el sistema de condiciones
       de normalidad (sum w_i = 1) y ortogonalidad (para cada variable j,
       sum_i w_i * a_ij = 0).
       - Si hay m = n+1 términos (grado de dificultad 0), el sistema es
         cuadrado y se resuelve exactamente.
       - Si hay m > n+1 términos (grado de dificultad > 0), se maximiza
         v(w) sujeto a esas mismas restricciones lineales y w_i >= 0
         (problema dual).
    2. El valor óptimo de la función objetivo es
       v* = prod_i (c_i / w_i)^{w_i}.
    3. Los valores óptimos de las x_j se recuperan resolviendo, en
       logaritmos, el sistema lineal  ln(w_i * v*) = ln(c_i) + sum_j a_ij
       ln(x_j)  para los términos con w_i > 0.
    """
    n = len(variables)
    m = len(terms)
    if m == 0:
        return {"error": "Debe indicar al menos un término del posinomio."}
    if m < n + 1:
        return {
            "error": (
                f"Con {n} variable(s) se necesitan al menos {n + 1} términos "
                f"en el posinomio para que el problema esté determinado "
                f"(se recibieron {m})."
            )
        }

    coeffs = []
    exp_matrix = np.zeros((m, n))
    for i, term in enumerate(terms):
        c_i = term.get("c")
        if c_i is None or c_i <= 0:
            return {
                "error": (
                    f"El coeficiente c del término {i + 1} debe ser un número "
                    f"positivo (requisito de los posinomios)."
                )
            }
        coeffs.append(float(c_i))
        exps = term.get("exponents", {})
        for j, var in enumerate(variables):
            exp_matrix[i, j] = float(exps.get(var, 0.0))

    coeffs = np.array(coeffs, dtype=float)

    # Sistema de normalidad + ortogonalidad: M w = rhs
    # fila 0: normalidad -> sum w_i = 1
    # filas 1..n: ortogonalidad -> sum_i w_i a_ij = 0
    M = np.vstack([np.ones((1, m)), exp_matrix.T])  # (n+1) x m
    rhs = np.zeros(n + 1)
    rhs[0] = 1.0

    if m == n + 1:
        try:
            w = np.linalg.solve(M, rhs)
        except np.linalg.LinAlgError:
            return {
                "error": (
                    "El sistema de condiciones de normalidad y ortogonalidad "
                    "es singular (no tiene solución única). Verifique los "
                    "exponentes de los términos del posinomio."
                )
            }
        if np.any(w <= 0):
            return {
                "error": (
                    "La solución del sistema de pesos arroja al menos un "
                    "peso w_i <= 0, lo que indica que el problema no tiene "
                    "un óptimo interior con esta formulación (revise el "
                    "planteo del posinomio)."
                ),
                "pesos": _round_dict(
                    {f"w{i+1}": wi for i, wi in enumerate(w)}
                ),
            }
        grado_dificultad = 0
    else:
        grado_dificultad = m - (n + 1)

        def neg_log_v(w):
            w = np.clip(w, 1e-12, None)
            return -np.sum(w * (np.log(coeffs) - np.log(w)))

        cons = [
            {"type": "eq", "fun": lambda w, M=M, rhs=rhs: M @ w - rhs}
        ]
        w0 = np.full(m, 1.0 / m)
        res = minimize(
            neg_log_v,
            w0,
            method="SLSQP",
            bounds=[(1e-9, 1.0) for _ in range(m)],
            constraints=cons,
            options={"maxiter": 500, "ftol": 1e-14},
        )
        if not res.success:
            return {
                "error": (
                    "No se pudo resolver el problema dual (grado de "
                    f"dificultad {grado_dificultad}) para hallar los pesos "
                    "óptimos."
                )
            }
        w = res.x

    # v* = prod (c_i / w_i)^{w_i}  (los w_i ~ 0 no aportan)
    mask = w > 1e-9
    v_star = float(np.prod((coeffs[mask] / w[mask]) ** w[mask]))
    if sense == "max":
        v_star = v_star  # el mismo procedimiento aplica; el signo del
        # posinomio se asume positivo por construcción.

    # Recuperar x_j: ln(w_i v*) = ln(c_i) + A_i . ln(x)   para w_i > 0
    A_active = exp_matrix[mask]
    rhs_log = np.log(w[mask] * v_star) - np.log(coeffs[mask])

    x_star: Dict[str, float] = {}
    aviso_recuperacion = None
    if A_active.shape[0] == n:
        try:
            ln_x = np.linalg.solve(A_active, rhs_log)
            x_star = {v: float(np.exp(val)) for v, val in zip(variables, ln_x)}
        except np.linalg.LinAlgError:
            aviso_recuperacion = (
                "La matriz de exponentes de los términos activos es "
                "singular; no se pudieron recuperar los valores de las "
                "variables a partir de los pesos (sí se obtuvo el valor "
                "óptimo de la función objetivo)."
            )
    else:
        ln_x, *_ = np.linalg.lstsq(A_active, rhs_log, rcond=None)
        x_star = {v: float(np.exp(val)) for v, val in zip(variables, ln_x)}
        aviso_recuperacion = (
            "El sistema para recuperar las variables a partir de los "
            "pesos no es cuadrado (grado de dificultad > 0); se entrega "
            "una solución por mínimos cuadrados."
        )

    # Verificación directa evaluando el posinomio en x_star (si se obtuvo)
    valor_verificado = None
    if x_star:
        valor_verificado = float(
            sum(
                coeffs[i] * np.prod(
                    [x_star[variables[j]] ** exp_matrix[i, j] for j in range(n)]
                )
                for i in range(m)
            )
        )

    out = {
        "exito": True,
        "sentido": sense,
        "grado_de_dificultad": grado_dificultad,
        "pesos": _round_dict({f"w{i+1}": wi for i, wi in enumerate(w)}),
        "valor_optimo_dual": round(v_star, 6),
        "x_optimo": _round_dict(x_star) if x_star else {},
        "valor_optimo_verificado_en_x": (
            round(valor_verificado, 6) if valor_verificado is not None else None
        ),
        "condiciones": {
            "normalidad": "sum(w_i) = 1",
            "ortogonalidad": [
                f"sum_i w_i * a_i{j+1} = 0  (variable {variables[j]})"
                for j in range(n)
            ],
        },
    }
    if aviso_recuperacion:
        out["aviso"] = aviso_recuperacion
    return out


# ---------------------------------------------------------------------------
# 4. Programación Convexa Separable (aproximación lineal por tramos + simplex)
# ---------------------------------------------------------------------------

def solve_separable_programming(
    variables: List[str],
    objective_terms: List[Dict[str, Any]],
    A: Optional[List[List[float]]] = None,
    b: Optional[List[float]] = None,
    sense: str = "max",
    n_segments: int = 10,
) -> Dict[str, Any]:
    n_segments = int(n_segments) 
    """Resuelve un problema separable  Z = sum_j f_j(x_j)  sujeto a A X <= b, X >= 0.

    Parámetros
    ----------
    variables : nombres de las variables, ej. ["x1", "x2"].
    objective_terms : un elemento por variable, en el mismo orden que
        ``variables``:
        ``{"expr": "<expresión univariada f_j(x_j)>", "lower": a_j, "upper": b_j}``
        ``expr`` debe usar exclusivamente el nombre de esa variable.
    A, b : restricciones lineales (en términos de las variables originales x_j).
    sense : "max" (f_j cóncavas) o "min" (f_j convexas).
    n_segments : número de tramos de la aproximación lineal por partes
        para cada variable (por defecto 10; más tramos => mayor precisión).

    Procedimiento
    --------------
    Cada f_j(x_j) se aproxima en [a_j, b_j] mediante ``n_segments`` tramos
    lineales de igual longitud. Se introduce una variable auxiliar
    ``delta_{j,k} in [0, ancho_k]`` por tramo, con
    ``x_j = a_j + sum_k delta_{j,k}``  y  ``f_j(x_j) ~ sum_k pendiente_{j,k} *
    delta_{j,k}``.

    Si f_j es cóncava (caso "max") las pendientes de los tramos son
    decrecientes, y si es convexa (caso "min") son crecientes; en ambos
    casos el símplex llena los tramos en el orden correcto de forma
    automática (no se necesita la regla de "entrada restringida a la
    base" explícita), por lo que basta con resolver el programa lineal
    resultante con ``scipy.optimize.linprog``.
    """
    n = len(variables)
    if len(objective_terms) != n:
        return {
            "error": (
                f"Se esperaba un término objetivo por variable "
                f"({n}), se recibieron {len(objective_terms)}."
            )
        }

    symbols = _build_symbols(variables)

    # Construir, para cada variable, los puntos de quiebre, valores de f y
    # pendientes de cada tramo.
    segmentos: List[Dict[str, Any]] = []
    for j, var in enumerate(variables):
        term = objective_terms[j]
        a_j = float(term["lower"])
        b_j = float(term["upper"])
        if b_j <= a_j:
            return {
                "error": (
                    f"El dominio de '{var}' es inválido: "
                    f"lower={a_j} debe ser menor que upper={b_j}."
                )
            }
        try:
            expr = _parse_expr(term["expr"], {var: symbols[var]})
        except ValueError as exc:
            return {"error": str(exc)}
        f_j = sp.lambdify(symbols[var], expr, "numpy")

        breakpoints = np.linspace(a_j, b_j, n_segments + 1)
        valores = np.array([float(f_j(bp)) for bp in breakpoints])
        anchos = np.diff(breakpoints)
        pendientes = np.diff(valores) / anchos

        if sense == "max" and not np.all(np.diff(pendientes) <= 1e-9):
            return {
                "error": (
                    f"La función de '{var}' no parece cóncava en "
                    f"[{a_j}, {b_j}] (las pendientes de los tramos no son "
                    f"decrecientes); la aproximación por tramos para "
                    f"maximización solo es válida para funciones cóncavas."
                )
            }
        if sense == "min" and not np.all(np.diff(pendientes) >= -1e-9):
            return {
                "error": (
                    f"La función de '{var}' no parece convexa en "
                    f"[{a_j}, {b_j}] (las pendientes de los tramos no son "
                    f"crecientes); la aproximación por tramos para "
                    f"minimización solo es válida para funciones convexas."
                )
            }

        segmentos.append(
            {
                "variable": var,
                "a": a_j,
                "b": b_j,
                "breakpoints": breakpoints.tolist(),
                "valores": valores.tolist(),
                "anchos": anchos.tolist(),
                "pendientes": pendientes.tolist(),
                "f_expr": str(expr),
            }
        )

    # Construcción del LP en términos de delta_{j,k}, k = 0..n_segments-1
    total_deltas = n * n_segments

    def delta_index(j: int, k: int) -> int:
        return j * n_segments + k

    c_lp = np.zeros(total_deltas)
    for j, seg in enumerate(segmentos):
        for k in range(n_segments):
            c_lp[delta_index(j, k)] = seg["pendientes"][k]
    # linprog minimiza -> para "max" negamos
    if sense == "max":
        c_lp = -c_lp
    elif sense != "min":
        return {"error": "sense debe ser 'max' o 'min'."}

    bounds_lp = []
    for j, seg in enumerate(segmentos):
        for k in range(n_segments):
            bounds_lp.append((0.0, float(seg["anchos"][k])))

    A_ub = None
    b_ub = None
    if A is not None and b is not None:
        A_arr = np.array(A, dtype=float)
        b_arr = np.array(b, dtype=float)
        if A_arr.shape[1] != n or A_arr.shape[0] != len(b_arr):
            return {
                "error": (
                    f"Dimensiones inconsistentes en A ({A_arr.shape}) / b "
                    f"({len(b_arr)}) para {n} variables."
                )
            }
        # x_j = a_j + sum_k delta_{j,k}  =>  sum_k A[i,j] * delta_{j,k}
        # <= b[i] - sum_j A[i,j] * a_j
        A_ub = np.zeros((A_arr.shape[0], total_deltas))
        b_ub = b_arr.copy()
        for i in range(A_arr.shape[0]):
            for j, seg in enumerate(segmentos):
                for k in range(n_segments):
                    A_ub[i, delta_index(j, k)] = A_arr[i, j]
                b_ub[i] -= A_arr[i, j] * seg["a"]

    res = linprog(c_lp, A_ub=A_ub, b_ub=b_ub, bounds=bounds_lp, method="highs")
    if not res.success:
        return {
            "error": (
                "El programa lineal de la aproximación por tramos no tiene "
                f"solución factible o no convergió (status scipy: "
                f"{res.message})."
            )
        }

    deltas = res.x
    x_star = {}
    f_star_aprox = 0.0
    contrib_por_var = {}
    for j, var in enumerate(variables):
        seg = segmentos[j]
        suma_delta = sum(deltas[delta_index(j, k)] for k in range(n_segments))
        x_j = seg["a"] + suma_delta
        x_star[var] = x_j
        contrib = sum(
            seg["pendientes"][k] * deltas[delta_index(j, k)]
            for k in range(n_segments)
        )
        contrib_por_var[var] = contrib
        f_star_aprox += contrib

    # Evaluación "real" (no aproximada) de cada f_j en el x_j obtenido,
    # para que el chatbot pueda mostrar el error de aproximación.
    f_star_real = 0.0
    for j, var in enumerate(variables):
        expr = _parse_expr(objective_terms[j]["expr"], {var: symbols[var]})
        f_j = sp.lambdify(symbols[var], expr, "numpy")
        f_star_real += float(f_j(x_star[var]))

    out = {
        "exito": True,
        "sentido": sense,
        "n_segmentos_por_variable": n_segments,
        "x_optimo_aproximado": _round_dict(x_star),
        "valor_optimo_aproximado_lineal_por_tramos": round(f_star_aprox, 6),
        "valor_real_de_f_en_x_optimo": round(f_star_real, 6),
        "contribucion_por_variable_aproximada": _round_dict(contrib_por_var),
        "detalle_segmentos": [
            {
                "variable": s["variable"],
                "dominio": [s["a"], s["b"]],
                "f_expr": s["f_expr"],
                "pendientes_de_los_tramos": [round(p, 6) for p in s["pendientes"]],
            }
            for s in segmentos
        ],
    }
    if A is not None and b is not None:
        out["restricciones"] = _evaluate_linear_constraints(A, b, list(x_star.values()))
    return out


# ---------------------------------------------------------------------------
# 5. Método de Combinaciones Lineales (Frank-Wolfe / gradiente con
#    restricciones lineales)
# ---------------------------------------------------------------------------

def solve_linear_combinations_method(
    variables: List[str],
    objective: str,
    A: List[List[float]],
    b: List[float],
    sense: str = "max",
    x0: Optional[List[float]] = None,
    max_iter: int = 30,
    tol: float = 1e-6,
) -> Dict[str, Any]:
    """Resuelve  max/min f(X)  sujeto a  A X <= b, X >= 0  con el método de
    combinaciones lineales (Frank-Wolfe).

    En cada iteración k:

    1. Se calcula el gradiente de f en el punto actual X_k.
    2. Se resuelve el subproblema LINEAL
       ``max/min  ∇f(X_k) . Y   sujeto a  A Y <= b, Y >= 0``
       cuya solución Y_k es un punto extremo del poliedro.
    3. Se hace una búsqueda unidimensional del paso óptimo
       ``r in [0, 1]`` para  ``X_k+1 = X_k + r (Y_k - X_k)``.
    4. Se repite hasta que la mejora del paso 3 sea despreciable o el
       producto ``∇f(X_k) . (Y_k - X_k)`` (medida de optimalidad) sea
       cercano a 0.

    Devuelve, además de la solución final, el detalle de cada iteración
    para que el chatbot pueda mostrar el procedimiento paso a paso, tal
    como se describe en la Unidad 5 (método de la pendiente más
    inclinada adaptado a restricciones lineales).
    """
    if sense not in ("max", "min"):
        return {"error": "sense debe ser 'max' o 'min'."}

    n = len(variables)
    symbols = _build_symbols(variables)
    try:
        f_expr = _parse_expr(objective, symbols)
    except ValueError as exc:
        return {"error": str(exc)}

    var_syms = [symbols[v] for v in variables]
    grad_expr = [sp.diff(f_expr, s) for s in var_syms]
    f_num = sp.lambdify(var_syms, f_expr, "numpy")
    grad_num = sp.lambdify(var_syms, grad_expr, "numpy")

    A_arr = np.array(A, dtype=float)
    b_arr = np.array(b, dtype=float)
    if A_arr.shape[0] != len(b_arr) or A_arr.shape[1] != n:
        return {
            "error": (
                f"Dimensiones inconsistentes: A es {A_arr.shape}, b tiene "
                f"{len(b_arr)} elementos, hay {n} variables."
            )
        }

    sign = -1.0 if sense == "max" else 1.0

    # Punto inicial factible: X0 (si se da) o el origen.
    if x0 is None:
        x_k = np.zeros(n)
    else:
        x_k = np.array(x0, dtype=float)
        if np.any(A_arr @ x_k > b_arr + 1e-6) or np.any(x_k < -1e-9):
            return {
                "error": (
                    "El punto inicial x0 no es factible: viola A X <= b o "
                    "X >= 0."
                )
            }

    iteraciones = []
    for it in range(1, max_iter + 1):
        grad_k = np.array(grad_num(*x_k), dtype=float)

        # Subproblema lineal: max/min grad_k . y  s.a. A y <= b, y >= 0
        # linprog minimiza, así que para "max" usamos -grad_k
        c_lp = grad_k if sense == "min" else -grad_k
        res_lp = linprog(c_lp, A_ub=A_arr, b_ub=b_arr, bounds=[(0, None)] * n, method="highs")
        if not res_lp.success:
            return {
                "error": (
                    "El subproblema lineal de la iteración "
                    f"{it} no tiene solución factible (status: "
                    f"{res_lp.message})."
                )
            }
        y_k = res_lp.x

        direction = y_k - x_k
        medida_optimalidad = float(np.dot(grad_k, direction))

        # Búsqueda del paso óptimo r en [0, 1]
        def phi(r):
            point = x_k + r * direction
            return sign * float(f_num(*point))

        res_ls = minimize_scalar(phi, bounds=(0.0, 1.0), method="bounded")
        r_star = float(res_ls.x)
        x_next = x_k + r_star * direction

        f_k = sign * 1.0 * f_num(*x_k) * sign  # = f(x_k) (deshacer signo)
        f_k = float(f_num(*x_k))
        f_next = float(f_num(*x_next))

        iteraciones.append(
            {
                "iteracion": it,
                "x_actual": _round_dict({v: val for v, val in zip(variables, x_k)}),
                "f_actual": round(f_k, 6),
                "gradiente": _round_dict({v: val for v, val in zip(variables, grad_k)}),
                "punto_extremo_y": _round_dict({v: val for v, val in zip(variables, y_k)}),
                "medida_de_optimalidad_grad_dot_dir": round(medida_optimalidad, 6),
                "paso_r": round(r_star, 6),
                "x_siguiente": _round_dict({v: val for v, val in zip(variables, x_next)}),
                "f_siguiente": round(f_next, 6),
            }
        )

        convergio = (
            abs(medida_optimalidad) < tol
            or np.linalg.norm(x_next - x_k) < tol
            or abs(f_next - f_k) < tol
        )
        x_k = x_next
        if convergio:
            break

    f_star = float(f_num(*x_k))
    grad_star = np.array(grad_num(*x_k), dtype=float)

    return {
        "exito": True,
        "sentido": sense,
        "funcion_objetivo": str(f_expr),
        "gradiente_simbolico": [str(g) for g in grad_expr],
        "iteraciones_realizadas": len(iteraciones),
        "convergencia_alcanzada": iteraciones[-1]["medida_de_optimalidad_grad_dot_dir"]
        if iteraciones
        else None,
        "detalle_iteraciones": iteraciones,
        "x_optimo": _round_dict({v: val for v, val in zip(variables, x_k)}),
        "valor_optimo": round(f_star, 6),
        "gradiente_en_optimo": _round_dict(
            {v: val for v, val in zip(variables, grad_star)}
        ),
        "restricciones": _evaluate_linear_constraints(A, b, x_k),
    }


# ---------------------------------------------------------------------------
# 6. Equivalente determinista de una restricción estocástica
#    (Programación Estocástica restringida por probabilidad)
# ---------------------------------------------------------------------------

def stochastic_to_deterministic(
    coef_deterministicos: Dict[str, float],
    parametro_aleatorio: str,
    media: float,
    desviacion_estandar: float,
    tipo_restriccion: str,
    rhs: float,
    nivel_confianza: float,
    posicion: str = "rhs",
) -> Dict[str, Any]:
    """Convierte una restricción con UN parámetro normal aleatorio en su
    equivalente determinista (chance-constrained programming).

    Caso soportado (el cubierto en el curso): una restricción de la forma
    ``sum_j a_j x_j {<=, >=} b``  donde **un único** coeficiente (ya sea un
    ``a_j`` o el propio ``b``) es una variable aleatoria normal con media y
    desviación estándar conocidas, y se exige que la restricción se cumpla
    con una probabilidad mínima ``nivel_confianza`` (por ejemplo, 0.95).

    Parámetros
    ----------
    coef_deterministicos : coeficientes de las variables que SÍ son
        constantes conocidas, ej. ``{"x1": 2, "x2": 3}``. Si el parámetro
        aleatorio es uno de los a_j, debe figurar aquí también la variable
        a la que multiplica (su coeficiente determinista se ignora, se
        reemplaza por ``media``/``desviacion_estandar``); use
        ``posicion="coeficiente:<nombre_variable>"``.
    parametro_aleatorio : nombre descriptivo del parámetro aleatorio
        (solo para el reporte).
    media, desviacion_estandar : parámetros de la distribución normal del
        parámetro aleatorio.
    tipo_restriccion : "<=" o ">=".
    rhs : lado derecho de la restricción (si el parámetro aleatorio es el
        propio lado derecho, este valor se ignora y se usa ``media``).
    nivel_confianza : probabilidad mínima exigida, en (0, 1), ej. 0.95.
    posicion : "rhs" si el parámetro aleatorio es el lado derecho b, o
        ``"coeficiente:<var>"`` si es el coeficiente a_j de la variable
        ``<var>``.

    Devuelve la restricción determinista equivalente, junto con el valor
    z (cuantil normal) usado y la explicación del signo aplicado.
    """
    if not (0 < nivel_confianza < 1):
        return {"error": "nivel_confianza debe estar entre 0 y 1 (ej. 0.95)."}
    if tipo_restriccion not in ("<=", ">="):
        return {"error": "tipo_restriccion debe ser '<=' o '>='."}

    z = float(norm.ppf(nivel_confianza))

    if posicion == "rhs":
        # sum a_j x_j <= b  con b ~ N(media, sigma^2), se exige
        # P(sum a_j x_j <= b) >= nivel_confianza
        # equivalente determinista: sum a_j x_j <= media - z * sigma   (si <=)
        # o                          sum a_j x_j >= media + z * sigma   (si >=)
        if tipo_restriccion == "<=":
            rhs_det = media - z * desviacion_estandar
        else:
            rhs_det = media + z * desviacion_estandar
        lado_aleatorio = "lado derecho (b)"
        lhs_str = " + ".join(f"{coef}*{var}" for var, coef in coef_deterministicos.items())
        restriccion_determinista = f"{lhs_str} {tipo_restriccion} {round(rhs_det, 6)}"
    elif posicion.startswith("coeficiente:"):
        var_aleatoria = posicion.split(":", 1)[1]
        if var_aleatoria not in coef_deterministicos:
            return {
                "error": (
                    f"La variable '{var_aleatoria}' indicada en 'posicion' "
                    "no está en coef_deterministicos."
                )
            }
        # a_j ~ N(media, sigma^2), término a_j * x_{var_aleatoria}.
        # Se reemplaza el coeficiente aleatorio por su "peor caso" al nivel
        # de confianza pedido: media + z*sigma (si <=, para no violar la
        # restricción con alta probabilidad) o media - z*sigma (si >=).
        if tipo_restriccion == "<=":
            coef_equiv = media + z * desviacion_estandar
        else:
            coef_equiv = media - z * desviacion_estandar
        lado_aleatorio = f"coeficiente de '{var_aleatoria}'"
        terminos = []
        for var, coef in coef_deterministicos.items():
            c = coef_equiv if var == var_aleatoria else coef
            terminos.append(f"{round(c, 6)}*{var}")
        lhs_str = " + ".join(terminos)
        restriccion_determinista = f"{lhs_str} {tipo_restriccion} {rhs}"
    else:
        return {
            "error": "posicion debe ser 'rhs' o 'coeficiente:<nombre_variable>'."
        }

    return {
        "exito": True,
        "parametro_aleatorio": parametro_aleatorio,
        "distribucion": "Normal",
        "media": media,
        "desviacion_estandar": desviacion_estandar,
        "nivel_confianza": nivel_confianza,
        "z_asociado": round(z, 6),
        "elemento_aleatorio": lado_aleatorio,
        "restriccion_determinista_equivalente": restriccion_determinista,
        "interpretacion": (
            f"Se reemplazó {lado_aleatorio} por su valor en el percentil "
            f"{nivel_confianza * 100:.1f}% de la distribución Normal "
            f"(z = {z:.4f}), de modo que la restricción original se cumpla "
            f"con probabilidad >= {nivel_confianza:.2f}. La restricción "
            "determinista resultante puede ahora analizarse con cualquiera "
            "de los otros métodos (por ejemplo, si la función objetivo es "
            "cuadrática, con solve_quadratic_programming)."
        ),
    }
