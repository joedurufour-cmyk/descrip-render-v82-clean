"""
creativo.py — Etapa 1b: CONVERSACIÓN CREATIVA (Tab "Creatividad").

Chat multi-turno con Gemini actuando de director de arte. Misma doctrina que el
resto del motor: el LLM propone ESTRUCTURAS (SolicitudPrompt), nunca números ni
parámetros; construir_prompt() ensambla cada propuesta. Estado de la
conversación vive en el cliente y viaja completo en cada llamada (Gemini es
stateless). Cuando el usuario copia una propuesta, el cliente lo anota en el
historial y el LLM itera sobre ese prompt como nueva base.

Montaje en main.py:
    from creativo import router as creativo_router, set_gemini
    set_gemini(gemini_client, GEMINI_MODEL)
    app.include_router(creativo_router)
"""
from __future__ import annotations
import json, logging, asyncio
from typing import List, Optional, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from google.genai import types, errors as genai_errors

from mj_engine import (
    SolicitudPrompt, OverridesTexto, DescripcionVisual,
    CategoriaEstetica, Resolucion, construir_prompt,
)
from gemini_orquestador import _limpiar_schema_gemini

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2", tags=["creativo"])

_client = None
_model = "gemini-3.7-flash"

def set_gemini(client, model: str):
    global _client, _model
    _client, _model = client, model

# ─── Contrato ───────────────────────────────────────────────
class MensajeChat(BaseModel):
    rol: Literal["user", "assistant"]
    contenido: str

class Propuesta(BaseModel):
    titulo: str = Field(description="Nombre corto de la dirección creativa (3-6 palabras)")
    razon: str = Field(description="Una frase: qué cambia y por qué mejora")
    solicitud: SolicitudPrompt

class RespuestaCreativa(BaseModel):
    mensaje: str = Field(description="Respuesta conversacional breve al usuario")
    propuestas: List[Propuesta] = Field(default_factory=list, description="0 a 3 propuestas")
    sugerencias: List[str] = Field(default_factory=list, description="Hasta 4 pedidos de seguimiento que el usuario podría hacer, muy cortos")

class PeticionCreativa(BaseModel):
    mensajes: List[MensajeChat] = Field(min_length=1, max_length=40)
    vision: Optional[DescripcionVisual] = None       # imagen ya analizada (vision_raw)
    overrides: Optional[OverridesTexto] = None       # estado actual de la UI (jerarquía física, ar, etc.)
    categoria: CategoriaEstetica = CategoriaEstetica.CINE
    ar: str = "1:1"
    modo: Resolucion = Resolucion.SD
    prompt_base: Optional[str] = None                # último prompt copiado por el usuario

class PropuestaRendida(BaseModel):
    titulo: str
    razon: str
    prompt: str
    parametros: dict
    warnings: List[str]
    solicitud: SolicitudPrompt                       # para "usar como base" en la UI

class RespuestaCreativaRendida(BaseModel):
    mensaje: str
    propuestas: List[PropuestaRendida]
    sugerencias: List[str]

# ─── System instruction ─────────────────────────────────────
SYSTEM_CREATIVO = """Eres el DIRECTOR DE ARTE conversacional de un motor de prompts para Midjourney V8.1 / Niji 7. Conversas con el usuario para mejorar, variar o inventar direcciones visuales.

Reglas duras:
1. NUNCA escribas parámetros Midjourney (--ar, --s, --chaos, --raw, --v, --niji, ::peso). Un motor Python los calcula. Tú devuelves estructuras `SolicitudPrompt` completas en `propuestas`.
2. Los campos de cada `solicitud` van en INGLÉS, concisos (todo el prompt debe caber en ~80 palabras). `categoria` es EXACTAMENTE uno de los valores del enum.
3. Si hay CONTEXTO DE IMAGEN, conserva sujeto, pose, entorno y encuadre detectados salvo que el usuario pida cambiarlos. Nunca metas el medio/estilo original en `sujeto` o `rasgos_fisicos`.
4. Si hay PROMPT BASE (el usuario copió una propuesta), itera SOBRE ÉL: cambios quirúrgicos, no reinvenciones, salvo pedido explícito de "algo totalmente distinto".
5. Cuando el usuario pida opciones/variantes, entrega 2-3 propuestas con direcciones realmente distintas (no sinónimos): distinta luz, distinto encuadre, distinta categoría. Cuando pida un ajuste puntual, 1 propuesta.
6. Si la petición es una pregunta o charla sin pedido de prompt, `propuestas` va vacía y respondes en `mensaje`.
7. `mensaje` en el idioma del usuario, máximo 3 frases, sin repetir el contenido de las propuestas. `sugerencias`: hasta 4 pedidos cortos y concretos que el usuario podría hacer a continuación (ej. "más dramático", "versión anime", "plano cenital").
8. Respeta el STATE de la UI (categoría, ar, jerarquía física) como default cuando el usuario no indique lo contrario.
Responde ÚNICAMENTE con el JSON del schema."""

def _contexto(p: PeticionCreativa) -> str:
    partes = [f"STATE UI: categoria={p.categoria.value}, ar={p.ar}, modo={p.modo.value}"]
    if p.overrides:
        ov = {k: v for k, v in p.overrides.model_dump().items() if v not in (None, [], False)}
        if ov:
            partes.append("OVERRIDES ACTIVOS: " + json.dumps(ov, ensure_ascii=False, default=str))
    if p.vision:
        partes.append("CONTEXTO DE IMAGEN (DescripcionVisual): " + p.vision.model_dump_json())
    if p.prompt_base:
        partes.append(f"PROMPT BASE (copiado por el usuario, iterar sobre él): {p.prompt_base}")
    return "\n".join(partes)

def _contents(p: PeticionCreativa) -> list:
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=_contexto(p))]),
                types.Content(role="model", parts=[types.Part.from_text(text="Contexto recibido.")])]
    for m in p.mensajes:
        contents.append(types.Content(
            role="user" if m.rol == "user" else "model",
            parts=[types.Part.from_text(text=m.contenido)],
        ))
    return contents

async def _llamar(p: PeticionCreativa) -> RespuestaCreativa:
    schema = _limpiar_schema_gemini(RespuestaCreativa.model_json_schema())
    for intento in range(1, 4):
        try:
            r = await _client.aio.models.generate_content(
                model=_model,
                contents=_contents(p),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_CREATIVO,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.9,   # aquí SÍ queremos divergencia; el motor pone el orden después
                ),
            )
            return RespuestaCreativa.model_validate_json(r.text)
        except genai_errors.APIError as e:
            if e.code not in {429, 503} or intento == 3:
                raise
            await asyncio.sleep(2 ** (intento - 1))

# ─── Endpoint ───────────────────────────────────────────────
@router.post("/creativo", response_model=RespuestaCreativaRendida)
async def creativo(p: PeticionCreativa):
    if not _client:
        raise HTTPException(503, "Gemini API no configurada.")
    try:
        resp = await _llamar(p)
    except ValidationError as e:
        # Gemini devolvió un JSON que no cumple el schema (ej. una categoría
        # que no es una del enum, un campo con tipo equivocado):
        # es un dato de entrada mal formado, no una falla del servidor.
        logger.warning(f"creativo: Gemini devolvió una estructura inválida: {e}")
        raise HTTPException(422, f"Gemini devolvió una propuesta con datos inválidos: {e}")
    except Exception as e:
        logger.exception("creativo: error Gemini")
        raise HTTPException(500, f"Error creativo: {e}")

    rendidas: List[PropuestaRendida] = []
    for prop in resp.propuestas[:3]:
        sol = prop.solicitud
        # El STATE de la UI manda sobre lo que el LLM haya puesto en ar/modo;
        # la jerarquía física activa se propaga si el LLM no la tocó.
        update = {"ar": p.ar, "modo": p.modo}
        if p.overrides:
            for campo in ("nivel_abdominal", "proporcion", "genero", "tags_fisico", "packs", "low_waist", "p", "sref"):
                v = getattr(p.overrides, campo)
                if v is not None and getattr(sol, campo) in (None, False, []):
                    update[campo] = v
        sol = sol.model_copy(update=update)
        res = construir_prompt(sol)
        rendidas.append(PropuestaRendida(
            titulo=prop.titulo, razon=prop.razon,
            prompt=res.prompt_final, parametros=res.parametros.model_dump(),
            warnings=res.warnings, solicitud=sol,
        ))
    return RespuestaCreativaRendida(mensaje=resp.mensaje, propuestas=rendidas, sugerencias=resp.sugerencias[:4])
