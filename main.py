"""
main.py — Backend DescripRender V8.2
Arquitectura de 3 etapas con motor determinístico mj_engine.py.
Mantiene compatibilidad con endpoint /generate legacy (APK existente).
"""

import os
import json
import base64
import logging
from typing import Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from dotenv import load_dotenv
from PIL import Image
import io

from google import genai
from google.genai import types

# Nuevo motor determinístico
from mj_engine import (
    SolicitudPrompt, ResultadoPrompt, DescripcionVisual, OverridesTexto,
    CategoriaEstetica, Resolucion, ModeloMJ,
    construir_prompt, fusionar_vision_y_overrides, regenerar_en_estilos,
)
from gemini_orquestador import (
    construir_payload_vision, procesar_respuesta_vision,
    construir_payload_texto, procesar_respuesta_texto,
    SYSTEM_INSTRUCTION, SYSTEM_INSTRUCTION_VISION,
    _limpiar_schema_gemini,
)

# Legacy models para compatibilidad
from models import TransformRequest, GenerationResponse, SourceAnalysis, GeneratedPrompt

load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="DescripRender V8.2",
    description="Motor determinístico de prompts para Midjourney V8.1 / Niji 7",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"ERROR: No se pudo configurar Gemini client: {e}")
else:
    print("WARNING: GEMINI_API_KEY no configurada.")


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def mapear_transform_a_overrides(transform: TransformRequest, ar: str = "1:1") -> OverridesTexto:
    """Mapea el viejo TransformRequest a OverridesTexto para compatibilidad.

    NOTA: TransformRequest.physique (PhysiqueLevel: lean/defined/ultra) describe
    nivel de definición muscular, no categoría estética — no existe en el payload
    legacy ninguna señal de categoría (photorealistic/cinematic/anime/...), así
    que se usa CINE como default explícito en vez de un mapeo que nunca podía
    resolver (bug previo: buscaba por physique.value en un dict de estilos).
    """
    categoria = CategoriaEstetica.CINE

    rasgos = []
    if transform.packs:
        rasgos.append(f"abdominal definition: {transform.packs}-pack")
    if transform.low_waist:
        rasgos.append("low waist, visible iliac furrows")
    if transform.feminine is not None:
        rasgos.append("feminine physique" if transform.feminine else "masculine physique")

    return OverridesTexto(
        categoria=categoria,
        rasgos_fisicos=", ".join(rasgos) if rasgos else None,
        iluminacion_atmosfera=(
            "dramatic sculptural lighting, hard rim light, deep shadow modeling"
            if transform.lighting_drama else None
        ),
        ar=ar,
    )


def _img_to_bytes(image_bytes: bytes) -> bytes:
    """Convierte bytes de imagen ya leídos a bytes JPEG."""
    pil_image = Image.open(io.BytesIO(image_bytes))
    buffered = io.BytesIO()
    pil_image.save(buffered, format="JPEG")
    return buffered.getvalue()


def _call_gemini_vision(image_bytes: bytes) -> str:
    """Etapa 0: Gemini describe imagen → JSON DescripcionVisual."""
    import traceback
    from google.genai import types
    try:
        schema = _limpiar_schema_gemini(DescripcionVisual.model_json_schema())
        
        # Crear contenido multimodal con bytes directos
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        text_part = types.Part.from_text(text="Describe esta imagen según el schema provisto.")

        content = types.Content(
            role="user",
            parts=[image_part, text_part]
        )

        logger.info(f"Calling Gemini vision with model: {GEMINI_MODEL}")
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[content],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION_VISION,
                response_mime_type="application/json",
                response_schema=schema,
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini vision error: {e}")
        logger.error(traceback.format_exc())
        raise


def _call_gemini_texto(idea: str) -> str:
    """Etapa 1: Gemini decompone texto → JSON SolicitudPrompt."""
    import traceback
    try:
        payload = construir_payload_texto(idea)
        logger.info(f"Calling Gemini text with model: {GEMINI_MODEL}")
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=payload["contents"],
            config={
                "system_instruction": SYSTEM_INSTRUCTION,
                "response_mime_type": "application/json",
                "response_schema": payload["generation_config"]["response_schema"],
            }
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini text error: {e}")
        logger.error(traceback.format_exc())
        raise


def _resultado_to_legacy(resultado: ResultadoPrompt, transform: TransformRequest) -> GenerationResponse:
    """Mapea ResultadoPrompt (motor v2) al formato legacy del APK.

    NOTA: GeneratedPrompt/SourceAnalysis en models.py usan el esquema de salida
    del viejo prompt_builder.py (5 prompts autoevaluados por Gemini con
    preservation_score/visual_power_score). El motor determinístico v2 no
    genera múltiples candidatos ni autoevalúa: aquí se produce un único
    GeneratedPrompt y los scores quedan fijos como placeholder porque no hay
    una puntuación equivalente que calcular.
    """
    generated = GeneratedPrompt(
        style_label=resultado.perfil_aplicado.value,
        prompt_text=resultado.prompt_final,
        parameters={
            "ar": resultado.parametros.ar,
            "stylize": resultado.parametros.stylize,
            "chaos": resultado.parametros.chaos,
            "weird": resultado.parametros.weird,
            "raw": resultado.parametros.raw,
            "v": resultado.parametros.v.value,
            "warnings": resultado.warnings,
        },
        preservation_score=1.0,
        visual_power_score=1.0,
    )
    return GenerationResponse(
        source_analysis=SourceAnalysis(
            subject=resultado.prompt_final[:100],
            identity="",
            physique_original=transform.physique.value,
            pose="",
            expression="",
            clothing="",
            environment="",
            camera="",
            lighting="",
            style=resultado.perfil_aplicado.value,
        ),
        locked_attributes=[],
        mutable_attributes=[],
        transformation_applied={"physique": transform.physique.value},
        prompts=[generated]
    )


# ═══════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    import os
    return {
        "status": "ok",
        "version": "2.0.0",
        "model": GEMINI_MODEL,
        "gemini_configured": gemini_client is not None,
        "motor": "mj_engine_v2.0",
        "build_date": os.getenv("BUILD_DATE", "unknown"),
    }


@app.get("/debug")
async def debug_info():
    """Endpoint de debug — muestra exactamente qué código está corriendo."""
    import os, subprocess, sys
    
    # Intentar obtener commit git
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd="/app",
            text=True
        ).strip()
    except:
        commit = "unknown"
    
    # Listar archivos Python con tamaños
    files = {}
    try:
        for f in os.listdir("/app"):
            if f.endswith(".py"):
                files[f] = os.path.getsize(f"/app/{f}")
    except:
        pass
    
    return {
        "commit": commit,
        "python_version": sys.version,
        "build_date": os.getenv("BUILD_DATE", "unknown"),
        "gemini_model": GEMINI_MODEL,
        "gemini_configured": gemini_client is not None,
        "files": files,
        "has_truncamiento": "_truncar_a_max_palabras" in dir(),
    }


@app.post("/generate", response_model=GenerationResponse)
async def generate_legacy(
    image: UploadFile = File(..., description="Imagen fuente"),
    transform_json: str = Form(..., description="JSON stringificado de TransformRequest"),
    ar: str = Form("1:1", description="Aspect ratio (ej. 1:1, 16:9, 9:16)"),
):
    """
    Endpoint LEGACY — compatible con APK existente.
    Internamente: Etapa 0 (visión) → overrides → Etapa 2 (motor determinístico).
    """
    if not gemini_client:
        raise HTTPException(status_code=503, detail="Gemini API no configurada.")

    # Validar request legacy
    try:
        transform = TransformRequest.model_validate_json(transform_json)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    logger.info(f"Legacy endpoint: physique={transform.physique.value}, ar={ar}")

    # Validar imagen
    allowed = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    if image.content_type not in allowed:
        raise HTTPException(status_code=400, detail=f"Formato no soportado: {image.content_type}")

    try:
        image_bytes = await image.read()
        if len(image_bytes) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Imagen muy grande. Max 10MB.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error leyendo imagen: {str(e)}")

    # === ETAPA 0: VISIÓN ===
    try:
        img_bytes = _img_to_bytes(image_bytes)
        vision_json = _call_gemini_vision(img_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en visión: {str(e)}")

    # === FUSIÓN + ETAPA 2 ===
    try:
        overrides = mapear_transform_a_overrides(transform, ar=ar)
        resultado = procesar_respuesta_vision(vision_json, overrides=overrides)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error construyendo prompt: {str(e)}")

    return _resultado_to_legacy(resultado, transform)


@app.post("/v2/vision")
async def vision_pure(
    image: UploadFile = File(..., description="Imagen a analizar")
):
    """Etapa 0 pura: devuelve DescripcionVisual JSON. No genera prompts."""
    if not gemini_client:
        raise HTTPException(status_code=503, detail="Gemini API no configurada.")

    try:
        image_bytes = await image.read()
        img_bytes = _img_to_bytes(image_bytes)
        vision_json = _call_gemini_vision(img_bytes)
        return json.loads(vision_json)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v2/generate")
async def generate_v2(
    image: UploadFile = File(None, description="Imagen opcional"),
    vision_json: str = Form(None, description="DescripcionVisual previa"),
    idea_texto: str = Form(None, description="Idea libre (si no hay imagen)"),
    overrides_json: str = Form(None, description="OverridesTexto opcional"),
    categoria: str = Form("cine_fotografia_cinematografica"),
    ar: str = Form("1:1"),
    modo: str = Form("sd"),
    n_estilos: int = Form(1),
):
    """
    Endpoint V2 — arquitectura completa de 3 etapas.
    Soporta: imagen+overrides, solo texto, vision previa, multi-estilo.
    """
    try:
        logger.info(f"generate_v2 called: image={image.filename if image else None}, idea_texto={bool(idea_texto)}, vision_json={bool(vision_json)}")
        
        if not gemini_client:
            raise HTTPException(status_code=503, detail="Gemini API no configurada.")

        # Validar modo con fallback a SD
        try:
            modo_enum = Resolucion(modo) if modo else Resolucion.SD
        except (ValueError, TypeError):
            logger.warning(f"Modo inválido '{modo}', usando SD por defecto")
            modo_enum = Resolucion.SD
        
        # Validar categoría con fallback
        try:
            cat_enum = CategoriaEstetica(categoria) if categoria else CategoriaEstetica.CINE
        except (ValueError, TypeError):
            logger.warning(f"Categoría inválida '{categoria}', usando CINE por defecto")
            cat_enum = CategoriaEstetica.CINE
        
        overrides = None
        if overrides_json:
            try:
                overrides = OverridesTexto.model_validate_json(overrides_json)
            except ValidationError as e:
                logger.error(f"Overrides validation error: {e}")
                raise HTTPException(status_code=422, detail=f"Overrides inválidos: {str(e)}")
        
        # Si se proporcionó categoría como parámetro y no hay override de categoría, usarla
        if categoria and (not overrides or not overrides.categoria):
            if not overrides:
                overrides = OverridesTexto(categoria=cat_enum)
            else:
                overrides.categoria = cat_enum
        
        # Si se proporcionó AR como parámetro y no hay override de AR, usarlo
        if ar and ar != "1:1" and (not overrides or not overrides.ar):
            if not overrides:
                overrides = OverridesTexto(ar=ar)
            else:
                overrides.ar = ar

        # --- Flujo con imagen ---
        if image and image.filename:
            logger.info("Processing image flow")
            try:
                image_bytes = await image.read()
                img_bytes = _img_to_bytes(image_bytes)
                vision_data = _call_gemini_vision(img_bytes)
            except Exception as e:
                logger.error(f"Vision error: {e}")
                raise HTTPException(status_code=500, detail=f"Error en visión: {str(e)}")

            vision_dict = json.loads(vision_data)

            if n_estilos > 1:
                import random
                vision = DescripcionVisual.model_validate(vision_dict)
                cats = random.sample(list(CategoriaEstetica), min(n_estilos, len(CategoriaEstetica)))
                resultados = regenerar_en_estilos(vision, cats, overrides, modo=modo_enum)
                return {
                    "modo": "multi-estilo",
                    "source_analysis": vision_dict,
                    "vision_raw": vision_dict,
                    "resultados": {
                        k.value: {
                            "prompt": v.prompt_final,
                            "parametros": v.parametros.model_dump(),
                            "warnings": v.warnings,
                        }
                        for k, v in resultados.items()
                    }
                }
            else:
                resultado = procesar_respuesta_vision(vision_data, overrides, modo=modo_enum)
                return {
                    "modo": "single",
                    "source_analysis": vision_dict,
                    "vision_raw": vision_dict,
                    "prompt": resultado.prompt_final,
                    "parametros": resultado.parametros.model_dump(),
                    "warnings": resultado.warnings,
                    "perfil": resultado.perfil_aplicado.value,
                    "modelo": resultado.modelo_efectivo.value,
                }

        # --- Flujo solo texto ---
        elif idea_texto:
            logger.info(f"Processing text flow: {idea_texto[:50]}...")
            try:
                texto_data = _call_gemini_texto(idea_texto)
                resultado = procesar_respuesta_texto(texto_data, overrides, modo=modo_enum)
                return {
                    "modo": "texto",
                    "prompt": resultado.prompt_final,
                    "parametros": resultado.parametros.model_dump(),
                    "warnings": resultado.warnings,
                    "perfil": resultado.perfil_aplicado.value,
                    "modelo": resultado.modelo_efectivo.value,
                }
            except Exception as e:
                logger.error(f"Text processing error: {e}")
                raise HTTPException(status_code=500, detail=f"Error procesando texto: {str(e)}")

        # --- Flujo con vision previa ---
        elif vision_json:
            logger.info("Processing vision_json flow")
            if n_estilos > 1:
                vision = DescripcionVisual.model_validate_json(vision_json)
                import random
                cats = random.sample(list(CategoriaEstetica), min(n_estilos, len(CategoriaEstetica)))
                resultados = regenerar_en_estilos(vision, cats, overrides, modo=modo_enum)
                return {
                    "modo": "multi-estilo",
                    "resultados": {
                        k.value: {
                            "prompt": v.prompt_final,
                            "parametros": v.parametros.model_dump(),
                            "warnings": v.warnings,
                        }
                        for k, v in resultados.items()
                    }
                }
            else:
                resultado = procesar_respuesta_vision(vision_json, overrides, modo=modo_enum)
                return {
                    "modo": "vision-previa",
                    "prompt": resultado.prompt_final,
                    "parametros": resultado.parametros.model_dump(),
                    "warnings": resultado.warnings,
                }

        else:
            raise HTTPException(status_code=400, detail="Proporciona image, idea_texto o vision_json.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error in generate_v2")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.get("/v2/perfiles")
async def list_profiles():
    """Devuelve tabla de perfiles estéticos."""
    from mj_engine import PERFILES
    return {
        k.value: {
            "stylize_range": [v.stylize_min, v.stylize_max],
            "raw": v.raw_obligatorio,
            "chaos": v.chaos_sugerido,
            "weird": v.weird_sugerido,
            "modelo_override": v.modelo_override.value if v.modelo_override else None,
            "nota": v.nota_tecnica,
        }
        for k, v in PERFILES.items()
    }


@app.get("/")
async def root():
    return {
        "service": "DescripRender V8.2",
        "version": "2.0.0",
        "motor": "mj_engine_v2.0",
        "endpoints": {
            "health": "/health",
            "generate": "POST /generate (legacy, compatible APK)",
            "vision": "POST /v2/vision",
            "generate_v2": "POST /v2/generate",
            "perfiles": "GET /v2/perfiles",
        }
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
