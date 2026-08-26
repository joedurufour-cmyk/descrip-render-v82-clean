"""
gemini_orquestador.py — Capa que conecta Gemini 3.7 Flash con el contrato
Pydantic de mj_engine.py.

ARQUITECTURA (3 etapas, separación estricta de responsabilidades):

Etapa 0 — VISIÓN (la hace Gemini, multimodal):
  Recibe UNA imagen (upload del usuario) y la describe en los mismos campos
  que usará el resto del pipeline: sujeto, rasgos, acción/estado, contexto,
  luz, paleta, medio/estilo original, lente/ángulo aparente, texto visible (OCR)
  y una categoría estética sugerida de las 10 del contrato.
  Salida forzada al schema `DescripcionVisual` — Gemini no puede inventar
  campos ni devolver prosa libre.

  El usuario puede entonces reescribir por texto cualquier atributo detectado
  (p. ej. "cambia la luz a atardecer", "hazlo estilo cyberpunk",
  "quítale las gafas") vía `OverridesTexto`: cada campo no-None que el usuario
  escribe PISA lo que Gemini detectó en la imagen. Esto es determinístico
  (Python puro, `mj_engine.fusionar_vision_y_overrides`), no requiere una
  segunda llamada a Gemini para aplicar el cambio.

  Con `mj_engine.regenerar_en_estilos()` la misma imagen se re-renderiza en N
  categorías estéticas distintas en un solo llamado (conserva sujeto/atributos,
  varía solo stylize/raw/chaos/weird/modelo según la tabla de perfiles).

Etapa 1 — DECOMPOSICIÓN CREATIVA (la hace Gemini, texto):
  Recibe la idea libre del usuario (lenguaje natural, cualquier idioma) y la
  descompone en los campos de `SolicitudPrompt` cuando NO hay imagen.
  Gemini NO decide números (--s, --chaos, --exp): eso es responsabilidad
  exclusiva del motor determinístico.
  Se fuerza salida estructurada (response_schema = SolicitudPrompt.model_json_schema())
  para que Gemini no pueda inventar campos.

Etapa 2 — CONSTRUCCIÓN DETERMINÍSTICA (la hace mj_engine.construir_prompt):
  Toma el JSON validado de la etapa 0 (fusionada) o la etapa 1 y aplica las
  reglas duras del documento fuente (rangos de --s por categoría, fuerza raw,
  purga sintaxis legacy, valida HD/SD, arma el string final).
  Esta etapa NUNCA delega números al LLM: es 100% Python/Pydantic.

Flujo con imagen:
  Imagen → Etapa 0 (visión) → [+ OverridesTexto opcional] → fusionar_vision_y_overrides
  → Etapa 2 → prompt(s)

Flujo sin imagen:
  Texto libre → Etapa 1 (decomposición) → Etapa 2 → prompt

Por qué separar así: un LLM (incluso con "structured output") puede razonar mal
un rango numérico o alucinar un parámetro muerto (--q, --cref, ::). El research
document es explícito en que estos errores son SILENCIOSOS (la API no arroja
error, solo ignora). Delegar la aritmética al validador Pydantic elimina esa
clase de fallo por completo.
"""

from __future__ import annotations
import json
from typing import Optional, List

from mj_engine import (
    SolicitudPrompt,
    ResultadoPrompt,
    construir_prompt,
    DescripcionVisual,
    OverridesTexto,
    fusionar_vision_y_overrides,
    regenerar_en_estilos,
    CategoriaEstetica,
    Resolucion,
)

MODEL_ID = "gemini-3.7-flash"  # doc verificado: GA desde 13-ago-2026, structured output + multimodal soportado

# ═══════════════════════════════════════════════════════════
# ETAPA 0 — VISIÓN
# ═══════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION_VISION = """Eres el módulo de VISIÓN de un motor de prompts para Midjourney V8.2 / Niji 7. Recibes UNA imagen y debes describirla objetivamente en los campos del schema `DescripcionVisual`. NO agregues parámetros Midjourney. NO inventes elementos que no estén visibles.

Reglas de descripción obligatorias:
1. `sujeto_detectado`: el elemento visualmente dominante de la composición (el que ocuparía el mayor espacio o mayor peso atencional), redactado para poder ir primero en un prompt (front-loading).
2. `rasgos_fisicos_detectados`, `accion_estado_detectado`, `contexto_detectado`, `iluminacion_detectada`, `paleta_color_detectada`, `lente_angulo_detectado`: describe SOLO lo que es visualmente verificable. Si un atributo no es determinable (oclusión, recorte, baja resolución), déjalo en null y agrega el nombre del campo a `confianza_baja`.
3. `medio_estilo_detectado`: identifica el medio ORIGINAL de la imagen (fotografía, render 3D, óleo, ilustración digital, acuarela, dibujo a lápiz, etc.) — esto es insumo, no instrucción; el usuario decidirá luego en qué categoría estética regenerar.
4. `texto_detectado_ocr`: transcribe EXACTAMENTE cualquier texto/tipografía visible en la imagen (letreros, etiquetas, camisetas). Si no hay texto visible, deja null. Nunca lo completes o corrijas — transcribe tal cual se ve, incluso si parece un error tipográfico en la imagen original.
5. `categoria_sugerida`: elige EXACTAMENTE uno de los 10 valores del enum `CategoriaEstetica`, el que mejor describe el estilo VISUAL ACTUAL de la imagen (no el que el usuario podría querer después — esa decisión es del usuario vía override).
6. `elementos_notables`: lista corta (máx. 5) de detalles secundarios que un director de arte querría preservar en una regeneración (ej. "cicatriz en la mejilla izquierda", "reflejo de neón en el pavimento mojado").

Responde ÚNICAMENTE con el JSON del schema. Sin explicaciones, sin markdown, sin comentarios."""


def _limpiar_schema_gemini(schema: dict) -> dict:
    """Elimina valores por defecto del schema — Gemini API no los soporta en
    response_schema (arroja: 'Default value is not supported')."""
    import copy
    cleaned = copy.deepcopy(schema)
    def _recurse(node):
        if isinstance(node, dict):
            node.pop("default", None)
            for v in node.values():
                _recurse(v)
        elif isinstance(node, list):
            for item in node:
                _recurse(item)
    _recurse(cleaned)
    return cleaned


def construir_payload_vision(imagen_base64: str, mime_type: str = "image/jpeg") -> dict:
    """Etapa 0: request body multimodal (imagen + instrucción) con structured
    output atado a DescripcionVisual."""
    schema = _limpiar_schema_gemini(DescripcionVisual.model_json_schema())
    return {
        "model": MODEL_ID,
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION_VISION}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": imagen_base64}},
                    {"text": "Describe esta imagen según el schema provisto."},
                ],
            }
        ],
        "generation_config": {
            "response_mime_type": "application/json",
            "response_schema": schema,
            
        },
    }


def procesar_respuesta_vision(
    json_text: str,
    overrides: Optional[OverridesTexto] = None,
    modo: Resolucion = Resolucion.SD,
) -> ResultadoPrompt:
    """Etapa 0 → 2 en un solo paso: valida la DescripcionVisual de Gemini,
    aplica overrides de texto del usuario (si los hay) y ejecuta el motor
    determinístico. Igual que en texto: Pydantic bloquea cualquier campo
    mal formado o categoría inventada antes de tocar el prompt final."""
    data = json.loads(json_text)
    vision = DescripcionVisual.model_validate(data)
    solicitud = fusionar_vision_y_overrides(vision, overrides, modo=modo)
    return construir_prompt(solicitud)


def procesar_respuesta_vision_multi_estilo(
    json_text: str,
    categorias_destino: List[CategoriaEstetica],
    overrides: Optional[OverridesTexto] = None,
    modo: Resolucion = Resolucion.SD,
) -> dict[CategoriaEstetica, ResultadoPrompt]:
    """Etapa 0 → 2 para el caso 'una imagen → varios estilos': misma imagen,
    N categorías, N prompts, sujeto/atributos conservados o editados por
    override una sola vez para todo el lote."""
    data = json.loads(json_text)
    vision = DescripcionVisual.model_validate(data)
    return regenerar_en_estilos(vision, categorias_destino, overrides, modo=modo)


# ═══════════════════════════════════════════════════════════
# ETAPA 1 — DECOMPOSICIÓN CREATIVA (texto libre, sin imagen)
# ═══════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """Eres el módulo de DECOMPOSICIÓN de un motor de prompts para Midjourney V8.2 / Niji 7. Tu única tarea es transformar la idea libre del usuario en los campos del schema JSON provisto. NO agregues parámetros Midjourney (--ar, --s, --chaos, --raw, --v, etc.) al texto: eso lo calcula un motor Python separado. NO uses sintaxis "concepto::peso". NO uses comandos legacy (--q, --quality, --cref, --cw, --oref).

Reglas de descomposición obligatorias:
1. Orden conceptual (aunque el JSON sea por campos, cada campo debe redactarse ya pensando en el orden final): sujeto+rasgos físicos → acción/estado → contexto/entorno → iluminación/atmósfera → medio/estilo artístico → lente/ángulo de cámara.
2. Front-loading: el `sujeto` debe contener el elemento visualmente dominante primero. Nunca antepongas un detalle menor (ej. "unas botas gastadas...") al sujeto principal.
3. Clasifica `categoria` eligiendo EXACTAMENTE uno de los 10 valores del enum — no inventes categorías nuevas.
4. Si el usuario pide anime/manga/estilo japonés, usa la categoría anime_manga_ilustracion_asiatica y dejas `forzar_v8_2_en_anime=false` salvo que el usuario pida explícitamente permanecer en V8.2 base.
5. Si el usuario pide texto/tipografía/letrero dentro de la imagen, coloca EXCLUSIVAMENTE la frase corta (2-4 palabras, sin comillas) en `texto_incrustado`. Nunca la insertes también en otro campo.
6. Mantén cada campo conciso (el motor limita el total a ~70 palabras antes de degradar por "Prompt Shortener"); no repitas adjetivos.
7. Si el usuario no especifica aspect ratio, dejas `ar` en null/omitido (el motor usa 1:1 por defecto).
8. Idioma de salida: escribe los campos en el mismo idioma del prompt final que se enviará a Midjourney (inglés si el usuario no indica lo contrario, ya que el research document indica que Midjourney interpreta mejor el inglés para términos técnicos de iluminación y cámara).

Responde ÚNICAMENTE con el JSON del schema. Sin explicaciones, sin markdown, sin comentarios."""


def construir_payload_texto(idea_usuario: str) -> dict:
    """Etapa 1: request body de texto con structured output atado a SolicitudPrompt."""
    schema = _limpiar_schema_gemini(SolicitudPrompt.model_json_schema())
    return {
        "model": MODEL_ID,
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": idea_usuario}],
            }
        ],
        "generation_config": {
            "response_mime_type": "application/json",
            "response_schema": schema,
            
        },
    }


def procesar_respuesta_texto(
    json_text: str,
    overrides: Optional[OverridesTexto] = None,
    modo: Resolucion = Resolucion.SD,
) -> ResultadoPrompt:
    """Etapa 1 → 2: valida la SolicitudPrompt de Gemini y ejecuta el motor
    determinístico."""
    data = json.loads(json_text)
    solicitud = SolicitudPrompt.model_validate(data)
    # Aplicar overrides si existen (merge campo a campo)
    if overrides:
        update_data = {k: v for k, v in overrides.model_dump().items() if v is not None}
        if update_data:
            solicitud = solicitud.model_copy(update=update_data)
    return construir_prompt(solicitud)
