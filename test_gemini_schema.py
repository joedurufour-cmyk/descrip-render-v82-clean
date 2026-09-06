"""
test_gemini_schema.py — Regresión del bug real encontrado en producción:
Pydantic v2 envuelve todo campo con tipo enum/$ref que además tiene un
default (ej. `modo: Resolucion = Resolucion.SD`) en
`{"allOf": [{"$ref": "..."}], "default": ...}` en vez de un `$ref` directo.
El transformer de google-genai (`_transformers.process_schema`) sabe resolver
un `$ref` suelto pero no tiene ningún manejo para `allOf` — cae al branch
final con `schema.get("type")` en None y explota:
`AttributeError: 'NoneType' object has no attribute 'upper'`.

Esto rompía en producción CUALQUIER llamada a Gemini que usara un modelo con
ese patrón como response_schema: /v2/generate en modo texto libre
(SolicitudPrompt, que tiene `modo: Resolucion = Resolucion.SD`) y /v2/creativo
(RespuestaCreativa, anidado vía $ref hasta el mismo campo). La imagen
(DescripcionVisual) no lo tiene porque no define ningún campo enum con
default.

Este test corre el `process_schema` REAL de google-genai (no un mock) contra
la salida de `_limpiar_schema_gemini` para los tres modelos que efectivamente
se usan como response_schema en el backend — es la única forma de detectar
este tipo de incompatibilidad sin llamar a la red real.
"""
import copy
import sys

from google.genai import _transformers as _genai_transformers

from mj_engine import SolicitudPrompt, DescripcionVisual
from gemini_orquestador import _limpiar_schema_gemini
from creativo import RespuestaCreativa

fallos = []


def check(cond, msg):
    if not cond:
        fallos.append(msg)


class _FakeClient:
    """Standin mínimo: process_schema solo lee client.vertexai."""
    vertexai = False


def _procesa_sin_explotar(nombre: str, modelo) -> None:
    raw = modelo.model_json_schema()
    limpio = _limpiar_schema_gemini(raw)
    try:
        _genai_transformers.process_schema(copy.deepcopy(limpio), _FakeClient())
    except Exception as e:
        fallos.append(f"{nombre}: process_schema de google-genai explotó con el schema limpio: {type(e).__name__}: {e}")
        return
    print(f"{nombre} OK -> process_schema real de google-genai no explota")


_procesa_sin_explotar("SolicitudPrompt (flujo texto libre, /v2/generate idea_texto)", SolicitudPrompt)
_procesa_sin_explotar("DescripcionVisual (flujo imagen, /v2/vision y /v2/generate)", DescripcionVisual)
_procesa_sin_explotar("RespuestaCreativa (tab Creatividad, /v2/creativo)", RespuestaCreativa)

# --- Caso específico: el campo con default+enum que causaba el crash ---
# (modo: Resolucion = Resolucion.SD) debe quedar como un $ref plano, nunca
# envuelto en allOf, después de _limpiar_schema_gemini.
schema_sp = _limpiar_schema_gemini(SolicitudPrompt.model_json_schema())
modo_schema = schema_sp["properties"]["modo"]
check("allOf" not in modo_schema, f"modo no debe quedar envuelto en allOf tras la limpieza, quedó: {modo_schema}")
check("default" not in modo_schema, "modo no debe conservar 'default' (Gemini lo rechaza con ValueError)")
print("Caso 'modo' (allOf unwrap) OK ->", modo_schema)


def test_gemini_schema():
    """Entry point compatible con pytest."""
    assert not fallos, "Fallos:\n" + "\n".join(f"  - {f}" for f in fallos)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    if fallos:
        print(f"❌ FALLARON {len(fallos)} TESTS:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("✅ TODOS LOS TESTS DE SCHEMA DE GEMINI PASARON")
        sys.exit(0)
