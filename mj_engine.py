"""
MJ_ENGINE — Motor determinístico de generación/reescritura de prompts
para Midjourney V8.2 (con salida a V7 legacy y Niji 7), destinado a
correr en un backend Pydantic detrás de Gemini API (model id: gemini-3.7-flash).

Fuente normativa: análisis "Motor de Prompts de Midjourney V8.2" (PDF adjunto).
Toda regla numérica de este archivo está trazada a una sección de ese documento
(ver comentarios `# doc:`).

Diseño: Gemini NO llama a Gemini aquí. Este módulo es el CONTRATO (schema +
reglas + validadores + constructor determinístico) que:
1) Gemini 3.7 Flash debe recibir como system_instruction + response_schema
   (structured output) para reescribir un prompt de usuario.
2) El backend usa como capa de postvalidación dura (defensa en profundidad):
   nunca confiar en que el LLM respetó los rangos; se re-valida aquí.

Autor del contrato: Claude (Anthropic), para DURBAR ASESORES / SIGMA-THEORY.
"""

from __future__ import annotations
from enum import Enum
from typing import Optional, List
import random
import re
from pydantic import BaseModel, Field, field_validator, model_validator

# ═══════════════════════════════════════════════════════════
# 1. ENUMS — universo cerrado de valores válidos
# ═══════════════════════════════════════════════════════════

class ModeloMJ(str, Enum):
    V8_2 = "8.2"          # doc: default desde 24-jul-2026, estética+personalización
    V8_1 = "8.1"          # doc: velocidad + apego literal
    V7 = "7"
    V6_1 = "6.1"
    NIJI_7 = "niji-7"     # doc: familia de peso base INDEPENDIENTE, no combinable con v8.x


class Resolucion(str, Enum):
    SD = "sd"             # doc: 1024x1024, 0.8 min GPU, ar hasta 14:1
    HD = "hd"             # doc: 2048x2048, 1.3 min GPU (+62.5%), ar máx 4:1
    DRAFT = "draft"       # doc: Big Batch Draft Mode, 24 img @512px, 0.4 min GPU/lote


class CategoriaEstetica(str, Enum):
    FOTOREALISMO_RETRATO = "fotorealismo_retrato_producto"
    CINE = "cine_fotografia_cinematografica"
    ANIME_MANGA = "anime_manga_ilustracion_asiatica"
    PINTURA_CLASICA = "pintura_arte_clasico"
    CONCEPTUAL_FANTASIA = "arte_conceptual_fantasia"
    EDITORIAL_MODA = "editorial_fotografia_moda"
    MODELADO_3D_CGI = "modelado_3d_cgi"
    CYBERPUNK_SCIFI = "cyberpunk_scifi_denso"
    EXPERIMENTAL_SURREALISMO = "experimental_surrealismo_vanguardista"
    VINTAGE_ANALOGICA = "fotografia_vintage_analogica"
    COMIC_OCCIDENTAL = "comic_occidental_tira_comica"
    ABSTRACTO = "arte_abstracto_no_figurativo"
    FIGURA_PVC_RESINA = "figura_coleccionable_pvc_resina"
    FIGURA_ACCION_REALISTA = "figura_accion_realista_toy_photography"


# ═══════════════════════════════════════════════════════════
# 1.1 JERARQUÍA FÍSICA — Niveles de definición muscular (A0-A8)
#     Heredado de ABDOMEN FORGE v1.2 + OMNI-FORGE v1.1
# ═══════════════════════════════════════════════════════════

class NivelAbdominal(str, Enum):
    """Matriz de definición abdominal A0-A8."""
    A0 = "A0"   # Sin definición visible
    A1 = "A1"   # Línea media suave
    A2 = "A2"   # Top 2 abdominales visibles
    A3 = "A3"   # 4-pack visible
    A4 = "A4"   # 6-pack definido
    A5 = "A5"   # 8-pack profundo
    A6 = "A6"   # 10-pack carved
    A7 = "A7"   # Shredded, vascular
    A8 = "A8"   # Hypertrophic, striated


class PerfilProporcion(str, Enum):
    """Perfiles corporales para determinar terminología de físico."""
    ATHLETIC = "athletic"           # Atlético funcional
    HEROIC = "heroic"               # Proporciones superheroicas
    AMAZON = "amazon"               # Físico femenino poderoso
    EXUBERANT_ANIME = "exuberant_anime"  # Hiper-musculoso estilo anime


class GeneroFisico(str, Enum):
    FEMENINO = "femenino"
    MASCULINO = "masculino"
    ANDROGINO = "androgino"


# Mapeo de niveles A0-A8 a terminología de prompt
TERMINOLOGIA_ABDOMINAL = {
    NivelAbdominal.A0: "soft abdomen, no visible definition",
    NivelAbdominal.A1: "faint ab lines, subtle core definition",
    NivelAbdominal.A2: "top two abs visible, light definition",
    NivelAbdominal.A3: "four-pack abs, moderate definition",
    NivelAbdominal.A4: "sharply defined six-pack abs, deep cuts",
    NivelAbdominal.A5: "prominent 8-pack, deeply carved abdominal blocks",
    NivelAbdominal.A6: "perfect 10-pack, extreme abdominal architecture",
    NivelAbdominal.A7: "shredded core, paper-thin skin, visible vascularity",
    NivelAbdominal.A8: "hypertrophic abdominal blocks, striated muscle fibers, deep separation",
}

# Terminología adicional por perfil de proporción.
# NOTA: ningún valor debe nombrar un medio/estilo artístico (anime, óleo,
# foto, 3D...) — la Jerarquía Física es un eje independiente de la Categoría
# Estética (categoria/CategoriaEstetica) y se combina con cualquiera de las
# 10 categorías, incluida regenerar_en_estilos() (una imagen → N estilos).
# EXUBERANT_ANIME antes decía "anime physique" literal, lo que se colaba en
# TODAS las categorías destino (fotorrealismo, pintura clásica, etc.) sin
# importar cuál se hubiera elegido — se mantiene la intención (proporciones
# hiper-musculosas y exageradas) sin el término de estilo.
TERMINOLOGIA_PROPORCION = {
    PerfilProporcion.ATHLETIC: "lean functional physique, low body fat",
    PerfilProporcion.HEROIC: "powerful heroic proportions, broad shoulders narrow waist",
    PerfilProporcion.AMAZON: "strong feminine physique, powerful lower body, defined core",
    PerfilProporcion.EXUBERANT_ANIME: "hyper-muscular exuberant physique, extremely exaggerated proportions, extreme vascularity",
}

# Tags de físico adicionales que se pueden combinar
TAGS_FISICO = {
    "vascular": "high vascularity, prominent veins",
    "striated": "striated muscle fibers, grainy texture",
    "carved": "deeply carved muscle definition, sharp separations",
    "shredded": "shredded physique, paper-thin skin",
    "hypertrophic": "hypertrophic muscle mass, extreme size",
    "toned": "toned muscles, athletic definition",
    "lean": "lean physique, visible muscle without bulk",
    "iliac_furrows": "visible iliac furrows, adonis belt",
    "serratus": "pronounced serratus anterior, ribbed obliques",
    "obliques": "deeply cut obliques, twisted core definition",
    "adonis_belt": "prominent adonis belt, v-taper",
    "hourglass": "extreme hourglass waist, tiny waist muscular core",
    "low_waist": "low waist pants, visible lower abdominals",
    "back_muscles": "defined latissimus dorsi, muscular back",
    "delts": "capped deltoids, round shoulder muscles",
    "glutes": "defined gluteal muscles, athletic posterior",
    "quads": "muscular quadriceps, teardrop definition",
}


def construir_descripcion_fisica(
    nivel: Optional[NivelAbdominal] = None,
    proporcion: Optional[PerfilProporcion] = None,
    genero: Optional[GeneroFisico] = None,
    tags: Optional[List[str]] = None,
    packs: Optional[int] = None,
    low_waist: bool = False,
) -> str:
    """Construye la descripción física muscular determinísticamente.
    
    Esta es la función que traduce los controles de la UI a terminología
    que Midjourney V8.2 entiende y respeta.
    """
    partes = []

    # Packs específicos OVERRIDEA el nivel — no se deben combinar los dos:
    # antes se agregaban ambos incondicionalmente y podían contradecirse en
    # el mismo prompt (ej. nivel A6 = "perfect 10-pack" + packs=6 =
    # "6-pack abs" a la vez, un número de abdominales imposible).
    if packs and packs >= 4:
        partes.append(f"{packs}-pack abs, deeply defined")
    elif nivel and nivel != NivelAbdominal.A0:
        partes.append(TERMINOLOGIA_ABDOMINAL[nivel])
    
    # Perfil de proporción
    if proporcion:
        partes.append(TERMINOLOGIA_PROPORCION[proporcion])
    
    # Tags adicionales
    if tags:
        for tag in tags:
            if tag in TAGS_FISICO:
                partes.append(TAGS_FISICO[tag])
    
    # Low waist (pants bajos que exponen abdominales)
    if low_waist:
        partes.append("low-rise pants exposing lower abdominals, visible iliac furrows")

    # NOTA: la reinterpretación de género (chip "Género" de la UI) NO se
    # hace acá. Antes había un reemplazo de palabras (.replace("his ", "her "))
    # que nunca tuvo efecto real — ninguna cadena de TERMINOLOGIA_ABDOMINAL/
    # TERMINOLOGIA_PROPORCION/TAGS_FISICO contiene esas palabras en inglés,
    # y Masculino/Andrógino no tenían ningún manejo. Adaptar el género es un
    # trabajo de lenguaje natural sobre prosa libre (sujeto/rasgos_fisicos
    # detectados por visión, en español), así que ahora se le pide
    # directamente a Gemini en la llamada de visión — ver
    # construir_instruccion_vision() en gemini_orquestador.py y su uso en
    # main.py::_call_gemini_vision(). `genero` sigue como parámetro acá por
    # si en el futuro se agregan tags físicos con lenguaje de género propio.

    return ", ".join(partes) if partes else ""


def score_fisico(
    nivel: Optional[NivelAbdominal] = None,
    tags: Optional[List[str]] = None,
    proporcion: Optional[PerfilProporcion] = None,
) -> dict:
    """Calcula scores de calidad del físico para display en UI.
    Heredado de ABDOMEN FORGE scores."""
    score_abs = 0
    if nivel:
        score_abs = int(nivel.value[1]) * 12  # A0=0, A8=96
    
    score_silueta = 0
    if proporcion == PerfilProporcion.HEROIC:
        score_silueta = 90
    elif proporcion == PerfilProporcion.AMAZON:
        score_silueta = 85
    elif proporcion == PerfilProporcion.EXUBERANT_ANIME:
        score_silueta = 95
    elif proporcion == PerfilProporcion.ATHLETIC:
        score_silueta = 75
    
    score_exuberance = score_abs + score_silueta
    if tags:
        score_exuberance += len(tags) * 5
    
    return {
        "abs_architecture": min(score_abs, 100),
        "silhouette_power": min(score_silueta, 100),
        "exuberance": min(score_exuberance, 100),
    }


# ═══════════════════════════════════════════════════════════
# 2. TABLA MAESTRA DE PERFILES ESTÉTICOS
#    doc: sección "Mapeo de Categorías Estéticas y el Parámetro Stylize"
# ═══════════════════════════════════════════════════════════

class PerfilEstetico(BaseModel):
    categoria: CategoriaEstetica
    stylize_min: int
    stylize_max: int
    raw_obligatorio: Optional[bool]      # True=usar --raw, False=evitarlo, None=opcional
    chaos_sugerido: Optional[tuple[int, int]] = None
    weird_sugerido: Optional[tuple[int, int]] = None
    p_recomendado: bool = False
    sref_recomendado: bool = False
    modelo_override: Optional[ModeloMJ] = None   # fuerza otro modelo base (ej. niji)
    nota_tecnica: str


PERFILES: dict[CategoriaEstetica, "PerfilEstetico"] = {
    CategoriaEstetica.FOTOREALISMO_RETRATO: PerfilEstetico(
        categoria=CategoriaEstetica.FOTOREALISMO_RETRATO,
        stylize_min=0, stylize_max=100,
        raw_obligatorio=True,
        nota_tecnica="raw obligatorio para suprimir viñeteo/bokeh/contraste "
                     "automático de V8.2; respeta iluminación plana y escala real.",
    ),
    CategoriaEstetica.CINE: PerfilEstetico(
        categoria=CategoriaEstetica.CINE,
        stylize_min=150, stylize_max=350,
        raw_obligatorio=False,
        nota_tecnica="V8.2 aplica grading direccional (teal&orange) y profundidad "
                     "anamórfica sin mutar personajes a ilustración; no requiere raw.",
    ),
    CategoriaEstetica.ANIME_MANGA: PerfilEstetico(
        categoria=CategoriaEstetica.ANIME_MANGA,
        stylize_min=200, stylize_max=300,
        raw_obligatorio=False,
        modelo_override=ModeloMJ.NIJI_7,
        nota_tecnica="Estética anime real requiere Niji 7 (modelo independiente, "
                     "no combinable con -v 8.1). El rango 200-300 es SOLO fallback "
                     "si el flujo de trabajo obliga a usar V8.1 base.",
    ),
    CategoriaEstetica.PINTURA_CLASICA: PerfilEstetico(
        categoria=CategoriaEstetica.PINTURA_CLASICA,
        stylize_min=400, stylize_max=600,
        raw_obligatorio=False,
        nota_tecnica="Libertad interpretativa alta para impasto/claroscuro/acuarela.",
    ),
    CategoriaEstetica.CONCEPTUAL_FANTASIA: PerfilEstetico(
        categoria=CategoriaEstetica.CONCEPTUAL_FANTASIA,
        stylize_min=700, stylize_max=1000,
        raw_obligatorio=False,
        nota_tecnica="Impacto visual sobre precisión física; fusión semántica fluida.",
    ),
    CategoriaEstetica.EDITORIAL_MODA: PerfilEstetico(
        categoria=CategoriaEstetica.EDITORIAL_MODA,
        stylize_min=200, stylize_max=400,
        raw_obligatorio=False,
        p_recomendado=True,
        nota_tecnica="Balance actitud/sofisticación vs anatomía y textil legible; --p "
                     "inyecta pose/iluminación de pasarela.",
    ),
    CategoriaEstetica.MODELADO_3D_CGI: PerfilEstetico(
        categoria=CategoriaEstetica.MODELADO_3D_CGI,
        stylize_min=100, stylize_max=250,
        raw_obligatorio=None,
        nota_tecnica="raw opcional/recomendado; stylize alto arriesga convertir el "
                     "render 3D en ilustración 2D por sesgo pictórico del modelo.",
    ),
    CategoriaEstetica.CYBERPUNK_SCIFI: PerfilEstetico(
        categoria=CategoriaEstetica.CYBERPUNK_SCIFI,
        stylize_min=300, stylize_max=500,
        raw_obligatorio=False,
        chaos_sugerido=(10, 20),
        nota_tecnica="Neón múltiple/niebla volumétrica; chaos bajo da variación de "
                     "iluminación sin perder densidad mecánica.",
    ),
    CategoriaEstetica.EXPERIMENTAL_SURREALISMO: PerfilEstetico(
        categoria=CategoriaEstetica.EXPERIMENTAL_SURREALISMO,
        stylize_min=500, stylize_max=800,
        raw_obligatorio=False,
        weird_sugerido=(500, 1500),
        nota_tecnica="weird rompe lógica euclidiana/anatómica; stylize alto da libertad "
                     "compositiva. NUNCA combinar con raw (se anula el propósito).",
    ),
    CategoriaEstetica.VINTAGE_ANALOGICA: PerfilEstetico(
        categoria=CategoriaEstetica.VINTAGE_ANALOGICA,
        stylize_min=50, stylize_max=150,
        raw_obligatorio=True,
        nota_tecnica="raw obligatorio: sin él, V8.2 'limpia' grano/fugas de luz/"
                     "desaturación que son el objetivo estético.",
    ),
    CategoriaEstetica.COMIC_OCCIDENTAL: PerfilEstetico(
        categoria=CategoriaEstetica.COMIC_OCCIDENTAL,
        stylize_min=200, stylize_max=400,
        raw_obligatorio=False,
        nota_tecnica="Trazo de tinta grueso y color plano tipo tira cómica/newspaper "
                     "strip (Peanuts, Calvin & Hobbes); stylize moderado mantiene el "
                     "look simple sin derivar a pintura digital detallada.",
    ),
    CategoriaEstetica.ABSTRACTO: PerfilEstetico(
        categoria=CategoriaEstetica.ABSTRACTO,
        stylize_min=650, stylize_max=1000,
        raw_obligatorio=False,
        weird_sugerido=(200, 600),
        nota_tecnica="No-figurativo por definición: stylize alto le da a V8.2 libertad "
                     "total para priorizar composición/color sobre cualquier sujeto "
                     "literal; weird moderado ayuda a romper la simetría por defecto.",
    ),
    CategoriaEstetica.FIGURA_PVC_RESINA: PerfilEstetico(
        categoria=CategoriaEstetica.FIGURA_PVC_RESINA,
        stylize_min=50, stylize_max=200,
        raw_obligatorio=True,
        nota_tecnica="raw obligatorio: es un render de PRODUCTO (figura de garage kit "
                     "en resina/PVC sobre peana), no una ilustración — sin raw, V8.2 "
                     "tiende a pintar la superficie en vez de mantenerla plástico/resina.",
    ),
    CategoriaEstetica.FIGURA_ACCION_REALISTA: PerfilEstetico(
        categoria=CategoriaEstetica.FIGURA_ACCION_REALISTA,
        stylize_min=50, stylize_max=200,
        raw_obligatorio=True,
        nota_tecnica="Mismo criterio que figura PVC: raw obligatorio para preservar "
                     "material de juguete/articulaciones visibles en vez de derivar a "
                     "un personaje real fotográfico.",
    ),
}


# PERFILES solo aporta números (--s/--raw/--chaos/--weird/modelo) por
# categoría — nunca palabras. Sin un mapeo a texto, elegir una categoría
# NO cambiaba una sola palabra del prompt: Midjourney interpreta la
# dirección artística del TEXTO, los parámetros numéricos solo modulan
# cómo se renderiza. DESCRIPTOR_ESTILO es el texto en inglés que le dice a
# MJ qué estilo es, y se usa como fallback en construir_prompt() cuando no
# hay un medio/estilo ya provisto por el usuario o por regenerar_en_estilos.
DESCRIPTOR_ESTILO: dict[CategoriaEstetica, str] = {
    CategoriaEstetica.FOTOREALISMO_RETRATO: "photorealistic photography, natural skin texture, true-to-life detail",
    CategoriaEstetica.CINE: "cinematic photography, film still, dramatic color grading",
    CategoriaEstetica.ANIME_MANGA: "anime illustration, Japanese animation style",
    CategoriaEstetica.PINTURA_CLASICA: "classical oil painting, fine art brushwork",
    CategoriaEstetica.CONCEPTUAL_FANTASIA: "fantasy concept art, digital painting",
    CategoriaEstetica.EDITORIAL_MODA: "high fashion editorial photography",
    CategoriaEstetica.MODELADO_3D_CGI: "3D CGI render, physically based rendering",
    CategoriaEstetica.CYBERPUNK_SCIFI: "cyberpunk science fiction illustration, neon-lit dystopian atmosphere",
    CategoriaEstetica.EXPERIMENTAL_SURREALISMO: "surreal experimental art, avant-garde composition",
    CategoriaEstetica.VINTAGE_ANALOGICA: "vintage analog film photography, grainy retro aesthetic",
    CategoriaEstetica.COMIC_OCCIDENTAL: "western comic strip illustration, bold ink outlines, flat cel-shaded colors, newspaper comic panel style",
    CategoriaEstetica.ABSTRACTO: "abstract non-representational art, expressive shapes and color fields, no literal figure",
    CategoriaEstetica.FIGURA_PVC_RESINA: "1/7 scale PVC collectible figure, resin garage kit, glossy plastic material, studio product photography on a display base",
    CategoriaEstetica.FIGURA_ACCION_REALISTA: "realistic articulated action figure, visible joints, collectible toy material, macro toy photography",
}


# D3: multi-estilo determinístico. Para cada categoría, las otras 9
# ordenadas de más a menos cercana estéticamente (medio/técnica de
# renderizado, no tema) — reemplaza el random.sample() que hacía que "una
# imagen → N estilos" devolviera un lote distinto cada vez que se pedía,
# rompiendo la promesa de determinismo del resto del motor. Curada a mano
# en base a DESCRIPTOR_ESTILO: familias foto (fotorealismo/cine/editorial/
# vintage), ilustración (anime/pintura/conceptual/experimental) y
# digital (3D/cyberpunk).
VECINOS_ESTETICOS: dict[CategoriaEstetica, list[CategoriaEstetica]] = {
    CategoriaEstetica.FOTOREALISMO_RETRATO: [
        CategoriaEstetica.CINE, CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.VINTAGE_ANALOGICA,
        CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.FIGURA_ACCION_REALISTA, CategoriaEstetica.MODELADO_3D_CGI,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.EXPERIMENTAL_SURREALISMO,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.CINE: [
        CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.MODELADO_3D_CGI,
        CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.FIGURA_ACCION_REALISTA, CategoriaEstetica.PINTURA_CLASICA,
        CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.COMIC_OCCIDENTAL,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.ANIME_MANGA: [
        CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.CONCEPTUAL_FANTASIA,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.FIGURA_ACCION_REALISTA,
        CategoriaEstetica.CYBERPUNK_SCIFI, CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.EDITORIAL_MODA,
        CategoriaEstetica.CINE, CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.FOTOREALISMO_RETRATO,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.PINTURA_CLASICA: [
        CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.ABSTRACTO,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.VINTAGE_ANALOGICA,
        CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.CINE, CategoriaEstetica.FOTOREALISMO_RETRATO,
        CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.CYBERPUNK_SCIFI, CategoriaEstetica.FIGURA_PVC_RESINA,
        CategoriaEstetica.FIGURA_ACCION_REALISTA,
    ],
    CategoriaEstetica.CONCEPTUAL_FANTASIA: [
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.ABSTRACTO,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.FIGURA_ACCION_REALISTA,
        CategoriaEstetica.CINE, CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.VINTAGE_ANALOGICA,
        CategoriaEstetica.FOTOREALISMO_RETRATO,
    ],
    CategoriaEstetica.EDITORIAL_MODA: [
        CategoriaEstetica.CINE, CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.VINTAGE_ANALOGICA,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.MODELADO_3D_CGI,
        CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.FIGURA_ACCION_REALISTA, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.COMIC_OCCIDENTAL,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.MODELADO_3D_CGI: [
        CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.FIGURA_ACCION_REALISTA, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.CINE,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.COMIC_OCCIDENTAL,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.CYBERPUNK_SCIFI: [
        CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.FIGURA_ACCION_REALISTA, CategoriaEstetica.FIGURA_PVC_RESINA,
        CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.CINE, CategoriaEstetica.ANIME_MANGA,
        CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.FOTOREALISMO_RETRATO,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.COMIC_OCCIDENTAL,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.EXPERIMENTAL_SURREALISMO: [
        CategoriaEstetica.ABSTRACTO, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.PINTURA_CLASICA,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.CINE, CategoriaEstetica.EDITORIAL_MODA,
        CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.FIGURA_PVC_RESINA,
        CategoriaEstetica.FIGURA_ACCION_REALISTA,
    ],
    CategoriaEstetica.VINTAGE_ANALOGICA: [
        CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.CINE,
        CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.CONCEPTUAL_FANTASIA,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.ABSTRACTO, CategoriaEstetica.FIGURA_PVC_RESINA,
        CategoriaEstetica.FIGURA_ACCION_REALISTA,
    ],
    CategoriaEstetica.COMIC_OCCIDENTAL: [
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.PINTURA_CLASICA,
        CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.ABSTRACTO,
        CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.CINE, CategoriaEstetica.FOTOREALISMO_RETRATO,
        CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.CYBERPUNK_SCIFI, CategoriaEstetica.FIGURA_PVC_RESINA,
        CategoriaEstetica.FIGURA_ACCION_REALISTA,
    ],
    CategoriaEstetica.ABSTRACTO: [
        CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.PINTURA_CLASICA,
        CategoriaEstetica.ANIME_MANGA, CategoriaEstetica.COMIC_OCCIDENTAL, CategoriaEstetica.CYBERPUNK_SCIFI,
        CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.CINE, CategoriaEstetica.EDITORIAL_MODA,
        CategoriaEstetica.VINTAGE_ANALOGICA, CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.FIGURA_PVC_RESINA,
        CategoriaEstetica.FIGURA_ACCION_REALISTA,
    ],
    CategoriaEstetica.FIGURA_PVC_RESINA: [
        CategoriaEstetica.FIGURA_ACCION_REALISTA, CategoriaEstetica.MODELADO_3D_CGI, CategoriaEstetica.ANIME_MANGA,
        CategoriaEstetica.CYBERPUNK_SCIFI, CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.CONCEPTUAL_FANTASIA,
        CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.CINE, CategoriaEstetica.VINTAGE_ANALOGICA,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.COMIC_OCCIDENTAL,
        CategoriaEstetica.ABSTRACTO,
    ],
    CategoriaEstetica.FIGURA_ACCION_REALISTA: [
        CategoriaEstetica.FIGURA_PVC_RESINA, CategoriaEstetica.CYBERPUNK_SCIFI, CategoriaEstetica.MODELADO_3D_CGI,
        CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.ANIME_MANGA,
        CategoriaEstetica.EDITORIAL_MODA, CategoriaEstetica.CINE, CategoriaEstetica.VINTAGE_ANALOGICA,
        CategoriaEstetica.PINTURA_CLASICA, CategoriaEstetica.EXPERIMENTAL_SURREALISMO, CategoriaEstetica.COMIC_OCCIDENTAL,
        CategoriaEstetica.ABSTRACTO,
    ],
}


def categorias_vecinas(seleccionada: CategoriaEstetica, n_estilos: int) -> List[CategoriaEstetica]:
    """Categorías para el flujo 'una imagen → N estilos': la elegida por el
    usuario SIEMPRE primera, seguida de sus (n_estilos-1) vecinas más
    cercanas según VECINOS_ESTETICOS. 100% determinístico — misma entrada,
    mismo lote siempre, sin random.sample()."""
    n_otras = min(max(n_estilos - 1, 0), len(VECINOS_ESTETICOS[seleccionada]))
    return [seleccionada] + VECINOS_ESTETICOS[seleccionada][:n_otras]


# ═══════════════════════════════════════════════════════════
# 3. PARÁMETROS MJ — contrato validado (capa dura anti-alucinación del LLM)
# ═══════════════════════════════════════════════════════════

LEGACY_MUERTOS = ["--quality", "--cref", "-cw", "--q"]

# IMPORTANTE: más largos primero (--quality antes que --q) para evitar residuos
# de substring al purgar (ej. "--quality 2" no debe dejar "uality 2").
_PATRONES_LEGACY = [re.compile(rf"{re.escape(tok)}(\s+[\d.]+)?") for tok in LEGACY_MUERTOS]
PATRON_DOBLE_PUNTO_PESO = re.compile(r"\w+::\d+(\.\d+)?")


class ParametrosMJ(BaseModel):
    ar: str = Field(default="1:1", description="Aspect ratio W:H")
    v: ModeloMJ = ModeloMJ.V8_1
    resolucion: Resolucion = Resolucion.SD
    stylize: int = Field(default=100, ge=0, le=1000)
    chaos: int = Field(default=0, ge=0, le=100)
    weird: int = Field(default=0, ge=0, le=3000)
    raw: bool = False
    exp: int = Field(default=0, ge=0, le=100)
    p: Optional[str] = Field(default=None, description="Código de personalización")
    sref: Optional[str] = Field(default=None, description="URL de imagen de referencia de estilo")
    seed: Optional[int] = Field(default=None, ge=0, le=4294967295)
    stop: int = Field(default=100, ge=10, le=100)
    tile: bool = False
    no: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list, exclude=False)

    def _agregar_warning(self, mensaje: str) -> None:
        """Append idempotente. Pydantic v2 vuelve a ejecutar los
        model_validator(mode="after") de este modelo cada vez que la instancia
        se anida como campo de OTRO modelo (ej. ResultadoPrompt.parametros),
        aunque no se reconstruya vía __init__. Sin esta guarda, cada anidación
        duplicaría el mismo warning en self.warnings."""
        if mensaje not in self.warnings:
            self.warnings.append(mensaje)

    # ---- doc: "Trampa HD vs SD" — ar máx 4:1 en hd, 14:1 en sd ---
    @model_validator(mode="after")
    def _validar_resolucion_ar(self) -> "ParametrosMJ":
        try:
            w, h = (float(x) for x in self.ar.split(":"))
            ratio = max(w / h, h / w)
        except Exception:
            ratio = 1.0
        if self.resolucion == Resolucion.HD and ratio > 4.0:
            self._agregar_warning(
                f"--ar {self.ar} excede 4:1 permitido en modo HD (doc: 'techo de "
                f"procesamiento inquebrantable'). Se recomienda renderizar en SD "
                f"(hasta 14:1) y reservar HD como export terminal."
            )
        return self

    # ---- doc: niji es modelo independiente, NO combinable con v8.x ---
    @model_validator(mode="after")
    def _validar_niji_exclusivo(self) -> "ParametrosMJ":
        # stylize!=100 se sacó de esta condición: el perfil anime pone
        # midpoint 250 SIEMPRE, así que con stylize en la condición este
        # warning disparaba en el 100% de los prompts anime (fatiga de
        # warnings = nadie los lee). Lo que realmente no interopera entre
        # niji y v8.x es raw/exp, no el valor de stylize en sí.
        if self.v == ModeloMJ.NIJI_7 and (self.raw or self.exp > 0):
            self._agregar_warning(
                "Niji 7 es una familia de modelo independiente: los parámetros "
                "estéticos ajustados para V8.1 (stylize/raw/exp) no tienen "
                "interpolación nativa garantizada; validar visualmente."
            )
        return self

    # ---- doc: exp>25-50 suprime stylize; profesional = exp<25 ---
    @model_validator(mode="after")
    def _validar_exp_vs_stylize(self) -> "ParametrosMJ":
        if self.exp >= 25:
            self._agregar_warning(
                f"--exp {self.exp} ≥25: umbral de sobreescritura (doc: 'punto de "
                f"quiebre crítico 25-50'). Empieza a suprimir --stylize "
                f"{self.stylize}. Para determinismo profesional, exp<25."
            )
        return self

    # ---- doc: --raw no imposibilita stylize alto, pero raw+weird es contradictorio ---
    @model_validator(mode="after")
    def _validar_raw_vs_weird(self) -> "ParametrosMJ":
        if self.raw and self.weird > 0:
            self._agregar_warning(
                "--raw + --weird activos simultáneamente: raw fuerza apego "
                "literal mientras weird rompe lógica semántica/anatómica. "
                "Combinación válida solo si la intención es fricción deliberada; "
                "en experimental/surrealismo el doc recomienda NO usar raw."
            )
        return self

    # ---- doc: legacy muertos (--q/-quality/--cref/--cw) no producen error, "
    #          "ignoran silenciosamente -> se purgan aquí para evitar falsos diagnósticos
    @field_validator("no", mode="before")
    @classmethod
    def _purgar_legacy_en_no(cls, v):
        return v or []


class SolicitudPrompt(BaseModel):
    """Input crudo del usuario — lo que Gemini 3.7 Flash recibe para reescribir."""
    sujeto: str
    rasgos_fisicos: Optional[str] = None
    accion_estado: Optional[str] = None
    contexto_entorno: Optional[str] = None
    iluminacion_atmosfera: Optional[str] = None
    paleta: Optional[str] = Field(
        default=None, description="Paleta de color (ej. 'teal and amber'), bloque opcional después de iluminación"
    )
    medio_estilo: Optional[str] = None
    lente_angulo: Optional[str] = None
    categoria: CategoriaEstetica
    texto_incrustado: Optional[str] = Field(
        default=None, description="Texto a renderizar dentro de la imagen, sin comillas"
    )
    modo: Resolucion = Resolucion.SD
    ar: str = "1:1"
    p: Optional[str] = None
    sref: Optional[str] = None
    forzar_v8_2_en_anime: bool = False   # override consciente del fallback niji
    intensidad: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Slider 0-1 que interpola stylize/chaos/weird dentro del rango "
                    "sugerido de la categoría; None/0.5 = comportamiento default (midpoint)."
    )

    # ═══ Parámetros de reproducibilidad — el usuario los fija, nunca Gemini ═══
    seed: Optional[int] = Field(default=None, ge=0, le=4294967295)
    no: Optional[List[str]] = Field(default=None, description="Elementos a excluir (--no)")
    stop: Optional[int] = Field(default=None, ge=10, le=100)
    tile: bool = False

    # ═══ JERARQUÍA FÍSICA (ABDOMEN FORGE v1.2+) ═══
    nivel_abdominal: Optional[NivelAbdominal] = None
    proporcion: Optional[PerfilProporcion] = None
    genero: Optional[GeneroFisico] = None
    tags_fisico: Optional[List[str]] = Field(default=None, description="Tags de TAGS_FISICO")
    packs: Optional[int] = Field(default=None, ge=2, le=12, description="Número de packs visibles")
    low_waist: bool = False

    @field_validator("texto_incrustado")
    @classmethod
    def _limpiar_texto(cls, v):
        if v is None:
            return v
        v = v.strip().strip('"').strip("'")
        return v


# ═══════════════════════════════════════════════════════════
# 3.1 ETAPA 0 — VISIÓN: contrato de descripción de imagen + overrides
#     de texto del usuario para regenerar en otro(s) estilo(s).
# ═══════════════════════════════════════════════════════════

class DescripcionVisual(BaseModel):
    """Salida estructurada de Gemini 3.7 Flash al analizar UNA imagen.
    Mismo vocabulario de campos que SolicitudPrompt para poder fusionarse
    con ella sin transformación: la visión rellena, el texto del usuario
    sobrescribe."""
    sujeto_detectado: str
    rasgos_fisicos_detectados: Optional[str] = None
    accion_estado_detectado: Optional[str] = None
    contexto_detectado: Optional[str] = None
    iluminacion_detectada: Optional[str] = None
    paleta_color_detectada: Optional[str] = None
    medio_estilo_detectado: Optional[str] = Field(
        default=None,
        description="Medio original detectado: fotografía, óleo, render 3D, "
                    "ilustración digital, acuarela, etc.",
    )
    lente_angulo_detectado: Optional[str] = None
    texto_detectado_ocr: Optional[str] = Field(
        default=None,
        description="Texto/tipografía visible en la imagen (OCR), si existe"
    )
    categoria_sugerida: CategoriaEstetica
    elementos_notables: List[str] = Field(default_factory=list)
    confianza_baja: List[str] = Field(
        default_factory=list,
        description="Nombres de campos donde el modelo tiene baja certeza "
                    "visual (oclusión, baja resolución, ambigüedad de estilo)",
    )


class OverridesTexto(BaseModel):
    """Ediciones por escritura textual del usuario sobre lo detectado en la
    imagen (o sobre una SolicitudPrompt previa). Todo campo no-None pisa el
    valor detectado/existente; el resto se conserva."""
    sujeto: Optional[str] = None
    rasgos_fisicos: Optional[str] = None
    accion_estado: Optional[str] = None
    contexto_entorno: Optional[str] = None
    iluminacion_atmosfera: Optional[str] = None
    paleta: Optional[str] = None
    medio_estilo: Optional[str] = None
    lente_angulo: Optional[str] = None
    categoria: Optional[CategoriaEstetica] = None
    texto_incrustado: Optional[str] = None
    ar: Optional[str] = None
    p: Optional[str] = None
    sref: Optional[str] = None
    forzar_v8_2_en_anime: Optional[bool] = None
    intensidad: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    seed: Optional[int] = None
    no: Optional[List[str]] = None
    stop: Optional[int] = None
    tile: Optional[bool] = None
    # Jerarquía física overrides
    nivel_abdominal: Optional[NivelAbdominal] = None
    proporcion: Optional[PerfilProporcion] = None
    genero: Optional[GeneroFisico] = None
    tags_fisico: Optional[List[str]] = None
    packs: Optional[int] = None
    low_waist: Optional[bool] = None


def fusionar_vision_y_overrides(
    vision: DescripcionVisual,
    overrides: Optional[OverridesTexto] = None,
    modo: Resolucion = Resolucion.SD,
) -> "SolicitudPrompt":
    """Etapa 0→2: convierte la descripción visual en SolicitudPrompt,
    aplicando overrides de texto del usuario campo a campo (override no-None
    gana siempre). Determinístico, sin llamadas a LLM."""
    ov = overrides or OverridesTexto()
    
    # Construir descripción física si hay parámetros de jerarquía
    desc_fisica = construir_descripcion_fisica(
        nivel=ov.nivel_abdominal,
        proporcion=ov.proporcion,
        genero=ov.genero,
        tags=ov.tags_fisico,
        packs=ov.packs,
        low_waist=ov.low_waist if ov.low_waist is not None else False,
    )

    # Jerarquía física PRIMERO: por front-loading (doc: "Fórmula de Prompting
    # V8.2 Óptima"), lo que va antes en la cadena recibe más peso de
    # tokenización y sobrevive al truncamiento de 70 palabras. La jerarquía
    # física es el control central de esta app — no debe quedar detrás de
    # rasgos genéricos detectados por visión (cabello, ropa) ni arriesgarse
    # a perderse si esos rasgos son verbosos.
    rasgos_detectados = ov.rasgos_fisicos or vision.rasgos_fisicos_detectados
    if desc_fisica:
        rasgos_fisicos = f"{desc_fisica}, {rasgos_detectados}" if rasgos_detectados else desc_fisica
    else:
        rasgos_fisicos = rasgos_detectados

    # contexto_entorno absorbe elementos_notables detectados por visión
    # (doc: "detalles secundarios que un director de arte querría preservar
    # en una regeneración") — antes se capturaban pero nunca llegaban al
    # prompt final, así que pose/objetos/detalles de escena se perdían.
    contexto_entorno = ov.contexto_entorno
    if contexto_entorno is None:
        contexto_entorno = vision.contexto_detectado
        if vision.elementos_notables:
            detalles = ", ".join(vision.elementos_notables)
            contexto_entorno = f"{contexto_entorno}, {detalles}" if contexto_entorno else detalles

    return SolicitudPrompt(
        sujeto=ov.sujeto or vision.sujeto_detectado,
        rasgos_fisicos=rasgos_fisicos,
        accion_estado=ov.accion_estado or vision.accion_estado_detectado,
        contexto_entorno=contexto_entorno,
        iluminacion_atmosfera=ov.iluminacion_atmosfera or vision.iluminacion_detectada,
        # paleta_color_detectada NO es un medio/estilo (no compite con la
        # categoría estética elegida como sí lo hace medio_estilo_detectado)
        # — es una señal compositiva de bajo costo y alto valor para
        # preservar identidad visual entre categorías, así que sí cae acá
        # por fallback igual que iluminación/contexto.
        paleta=ov.paleta or vision.paleta_color_detectada,
        # Solo el override EXPLÍCITO del usuario (ov.medio_estilo) alimenta el
        # prompt de salida — NUNCA vision.medio_estilo_detectado. El medio
        # original detectado (ej. "ilustración digital estilizada" en una
        # imagen fuente ilustrada) es solo informativo para el usuario (ya
        # viaja en source_analysis/vision_raw); si se usara acá, le ganaba a
        # la categoría estética que el usuario eligió explícitamente (ej.
        # pedir "Fotoreal" y que el prompt final terminara diciendo
        # "ilustración digital estilizada" en vez de DESCRIPTOR_ESTILO).
        medio_estilo=ov.medio_estilo,
        lente_angulo=ov.lente_angulo or vision.lente_angulo_detectado,
        categoria=ov.categoria or vision.categoria_sugerida,
        # Solo el override EXPLÍCITO del usuario alimenta texto_incrustado —
        # NUNCA vision.texto_detectado_ocr automáticamente. El OCR de Gemini
        # alucina texto sobre logos/símbolos/texturas con más frecuencia de
        # la deseable (ver fix anterior de anti-alucinación en
        # SYSTEM_INSTRUCTION_VISION, que atenúa pero no elimina el problema
        # — sigue siendo el juicio de un modelo "flash" sobre una imagen).
        # texto_incrustado fuerza --raw y le pide a Midjourney que renderice
        # ese texto LITERAL como letrero en la imagen, así que una lectura
        # alucinada arruina la composición cada vez que ocurre. El texto
        # detectado sigue visible para el usuario en source_analysis/
        # vision_raw — si es correcto, lo puede confirmar a mano en el
        # override "Texto Incrustado" de la UI.
        texto_incrustado=ov.texto_incrustado,
        modo=modo,
        ar=ov.ar or "1:1",
        p=ov.p,
        sref=ov.sref,
        forzar_v8_2_en_anime=(
            ov.forzar_v8_2_en_anime if ov.forzar_v8_2_en_anime is not None else False
        ),
        intensidad=ov.intensidad,
        seed=ov.seed,
        no=ov.no,
        stop=ov.stop,
        tile=ov.tile if ov.tile is not None else False,
        # Propagar jerarquía física
        nivel_abdominal=ov.nivel_abdominal,
        proporcion=ov.proporcion,
        genero=ov.genero,
        tags_fisico=ov.tags_fisico,
        packs=ov.packs,
        low_waist=ov.low_waist if ov.low_waist is not None else False,
    )


def regenerar_en_estilos(
    vision: DescripcionVisual,
    categorias_destino: List[CategoriaEstetica],
    overrides: Optional[OverridesTexto] = None,
    modo: Resolucion = Resolucion.SD,
) -> dict[CategoriaEstetica, "ResultadoPrompt"]:
    """Toma UNA imagen ya descrita y produce N prompts, uno por cada estilo
    en `categorias_destino`, conservando sujeto/atributos detectados (o sus
    overrides) y variando solo la categoría estética. Este es el flujo
    'una imagen → varios estilos' pedido explícitamente.

    NOTA: medio_estilo_detectado (el medio/estilo ORIGINAL de la imagen, ej.
    "ilustración estilo anime") se descarta al regenerar salvo que el usuario
    lo haya fijado explícitamente vía overrides.medio_estilo. De lo contrario
    ese medio original quedaría incrustado en el prompt de CADA categoría
    destino, contradiciendo estilos distintos al original (ej. forzar "anime"
    dentro de un prompt de pintura clásica o fotorrealismo).

    SEED COMPARTIDA: si el usuario no fija overrides.seed, se genera UNA sola
    semilla aleatoria y se propaga a las N variantes. Sin esto, cada categoría
    salía con una seed aleatoria distinta de Midjourney y las N imágenes no
    compartían composición — comparar "el mismo encuadre en 3 estilos" era
    imposible aunque el prompt textual sí fuera consistente. Con la seed
    fija, las N variantes son una comparación A/B limpia de solo el estilo."""
    resultados = {}
    medio_explicito = overrides.medio_estilo if overrides else None
    seed_compartida = (
        overrides.seed if overrides and overrides.seed is not None
        else random.randint(0, 4294967295)
    )
    for cat in categorias_destino:
        ov_cat = (
            overrides.model_copy(update={"categoria": cat, "seed": seed_compartida}) if overrides
            else OverridesTexto(categoria=cat, seed=seed_compartida)
        )
        sol = fusionar_vision_y_overrides(vision, ov_cat, modo=modo)
        if not medio_explicito:
            sol = sol.model_copy(update={"medio_estilo": None})
        resultados[cat] = construir_prompt(sol)
    return resultados


def construir_variante_estilo_original(
    vision: DescripcionVisual,
    overrides: Optional[OverridesTexto] = None,
    modo: Resolucion = Resolucion.SD,
) -> ResultadoPrompt:
    """Variante EXTRA (no cuenta contra n_estilos, se pide aparte con
    incluir_estilo_original): reproduce el estilo LITERAL de la imagen
    original subida en vez de un DESCRIPTOR_ESTILO genérico de categoría.

    Usa vision.categoria_sugerida (la categoría que Gemini detectó como
    estilo visual ACTUAL de la imagen) para que los parámetros numéricos
    --s/--raw/--chaos/--weird correspondan a ese estilo real, y
    vision.medio_estilo_detectado como texto de medio/estilo — salvo que el
    usuario ya haya fijado un medio_estilo explícito vía overrides, que
    sigue ganando siempre (misma regla que el resto del motor: override
    explícito > detección automática)."""
    ov = overrides.model_copy() if overrides else OverridesTexto()
    ov.categoria = vision.categoria_sugerida
    if not ov.medio_estilo:
        ov.medio_estilo = vision.medio_estilo_detectado
    sol = fusionar_vision_y_overrides(vision, ov, modo=modo)
    return construir_prompt(sol)


class ResultadoPrompt(BaseModel):
    prompt_final: str
    parametros: ParametrosMJ
    perfil_aplicado: CategoriaEstetica
    warnings: List[str]
    conteo_palabras: int   # conteo ANTES de truncar (dispara el warning de Prompt Shortener)
    conteo_final: int      # conteo del cuerpo REAL que ve Midjourney: después de
                            # truncar y de agregar medio_estilo/texto_incrustado
                            # (que sobreviven al truncamiento a propósito).
                            # conteo_palabras solo mide el riesgo de truncar; esto
                            # mide lo que efectivamente se envía.
    modelo_efectivo: ModeloMJ


# ═══════════════════════════════════════════════════════════
# 4. ALGORITMO DETERMINÍSTICO — construcción del prompt (front-loading)
#    doc: "Fórmula de Prompting V8.2 Óptima"
#    [Sujeto+rasgos] + [Acción/Estado] + [Contexto] + [Iluminación] +
#    [Medio/Estilo] + [Lente/Ángulo] + [Parámetros]
# ═══════════════════════════════════════════════════════════

LIMITE_PALABRAS_SEGURO = 100  # doc: 60-80 palabras antes de activar Prompt Shortener;
# subido a 100 a pedido explícito del usuario para preservar más detalle
# secundario (acción/contexto/iluminación/lente) en descripciones largas.
# medio_estilo, jerarquía física (front-loaded) y texto_incrustado ya
# sobreviven al truncamiento sin importar el límite (ver construir_prompt),
# así que este número solo determina cuánto detalle secundario se conserva
# antes de recortar — por encima de 80 hay riesgo real de que el propio
# Prompt Shortener de Midjourney reescriba el prompt en la otra punta.


def _purgar_sintaxis_legacy(texto: str) -> tuple[str, List[str]]:
    warns = []
    if PATRON_DOBLE_PUNTO_PESO.search(texto):
        texto = PATRON_DOBLE_PUNTO_PESO.sub(lambda m: m.group(0).split("::")[0], texto)
        warns.append(
            "Sintaxis '::peso' detectada y removida: deshabilitada en V8.2 "
            "(doc: 'el modelo trata toda la cadena como input fluido')."
        )
    for token, patron in zip(LEGACY_MUERTOS, _PATRONES_LEGACY):
        if patron.search(texto):
            texto = patron.sub("", texto)
            warns.append(f"Parámetro legacy muerto '{token}' removido (ignorado "
                         f"silenciosamente por V8.2 desde V8.1+).")
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    return texto.strip(), warns


def _truncar_a_max_palabras(texto: str, max_palabras: int = 70) -> str:
    """Trunca el texto a un máximo de palabras, preservando oraciones completas."""
    palabras = texto.split()
    if len(palabras) <= max_palabras:
        return texto
    # Truncar y agregar elipsis
    truncado = " ".join(palabras[:max_palabras])
    # Evitar cortar a mitad de oración si es posible
    ultimo_punto = truncado.rfind(".")
    if ultimo_punto > len(truncado) * 0.7:  # Si hay un punto después del 70%, cortar ahí
        truncado = truncado[:ultimo_punto + 1]
    return truncado.strip()


PRIORIDAD_BLOQUES = ["sujeto_rasgos", "accion", "contexto", "iluminacion", "paleta", "lente"]
# lo último de la lista se sacrifica primero al truncar.


def _ensamblar_con_presupuesto(bloques: dict, limite: int) -> tuple[str, List[str]]:
    """D1: truncamiento por prioridad de bloque en vez de corte ciego de
    palabras. Antes, _truncar_a_max_palabras cortaba a las N palabras con
    un rfind(".") — podía eliminar la lente y media iluminación al azar,
    sin decir qué se perdió. Acá se descartan bloques ENTEROS empezando
    por el de menor prioridad (lente, luego paleta, luego iluminación...)
    hasta entrar en el presupuesto.

    sujeto_rasgos NUNCA se descarta entero (es lo que identifica la
    escena); si sobrevive solo y aun así excede el límite, se recorta por
    palabras como último recurso (mismo mecanismo que antes, acotado a
    ese bloque)."""
    descartados: List[str] = []
    for clave in reversed(PRIORIDAD_BLOQUES[1:]):
        total = sum(len((bloques.get(k) or "").split()) for k in PRIORIDAD_BLOQUES)
        if total <= limite:
            break
        if bloques.get(clave):
            bloques[clave] = None
            descartados.append(clave)
    cuerpo = ". ".join(bloques[k] for k in PRIORIDAD_BLOQUES if bloques.get(k))
    if len(cuerpo.split()) > limite:
        cuerpo = _truncar_a_max_palabras(cuerpo, limite)
    return cuerpo, descartados


def construir_prompt(sol: SolicitudPrompt) -> ResultadoPrompt:
    perfil = PERFILES[sol.categoria]
    warnings: List[str] = []

    # --- resolver modelo efectivo (override niji) ---
    # V8.1 por default: V8.2 no está disponible/estable en la práctica (Midjourney
    # lo normaliza a v8.1 de todos modos), y V8.1 es el modelo base estable real.
    modelo_efectivo = ModeloMJ.V8_1
    if perfil.modelo_override == ModeloMJ.NIJI_7 and not sol.forzar_v8_2_en_anime:
        modelo_efectivo = ModeloMJ.NIJI_7
        warnings.append(
            "Categoría anime/manga: enrutado a Niji 7 (modelo base independiente). "
            "Usa forzar_v8_2_en_anime=True para forzar V8.1 base con fallback "
            "stylize 200-300 (calidad anime inferior)."
        )

    # --- construir bloques en orden front-loading obligatorio ---
    # NOTA: la descripción física ya está incluida en sol.rasgos_fisicos
    # por fusionar_vision_y_overrides() (flujo imagen) o construir_payload_texto()
    rasgos_combinados = sol.rasgos_fisicos or ""

    # medio_estilo: si no hay uno explícito (override del usuario, o
    # detectado por visión y conservado porque coincide con la categoría),
    # se usa el descriptor de la categoría estética elegida como fallback —
    # sin esto, cambiar de categoría solo movía números (--s/--raw/--chaos)
    # y el prompt nunca decía en palabras qué estilo se quería.
    medio_estilo = sol.medio_estilo or DESCRIPTOR_ESTILO.get(sol.categoria)

    # D1: cada bloque se purga de sintaxis legacy por separado (antes se
    # hacía sobre la cadena ya unida) para poder truncar por bloque entero
    # más abajo sin volver a tocar el texto.
    bloques_crudos = {
        "sujeto_rasgos": f"{sol.sujeto}, {rasgos_combinados}" if rasgos_combinados else sol.sujeto,
        "accion": sol.accion_estado,
        "contexto": sol.contexto_entorno,
        "iluminacion": sol.iluminacion_atmosfera,
        # D4: paleta de color — señal barata y efectiva para preservar
        # identidad visual entre categorías, capturada por visión pero
        # descartada hasta ahora.
        "paleta": f"palette: {sol.paleta}" if sol.paleta else None,
        "lente": sol.lente_angulo,
    }
    bloques: dict = {}
    for clave, valor in bloques_crudos.items():
        if valor and valor.strip():
            limpio, warns_legacy = _purgar_sintaxis_legacy(valor.strip())
            bloques[clave] = limpio or None
            warnings.extend(warns_legacy)
        else:
            bloques[clave] = None

    cuerpo_completo = ". ".join(bloques[k] for k in PRIORIDAD_BLOQUES if bloques.get(k))

    # --- TRUNCAMIENTO DURO: nunca exceder LIMITE_PALABRAS_SEGURO palabras ---
    conteo_palabras = len(cuerpo_completo.split())
    if conteo_palabras > LIMITE_PALABRAS_SEGURO:
        cuerpo, descartados = _ensamblar_con_presupuesto(dict(bloques), LIMITE_PALABRAS_SEGURO)
        detalle = f"Bloques descartados: {', '.join(descartados)}." if descartados else "Recorte por palabras dentro del sujeto (los demás bloques ya venían vacíos)."
        warnings.append(
            f"Prompt truncado de {conteo_palabras} a {len(cuerpo.split())} palabras "
            f"(doc: límite seguro para evitar Prompt Shortener). {detalle}"
        )
    else:
        cuerpo = cuerpo_completo

    # medio_estilo se agrega DESPUÉS del truncamiento (igual que el texto
    # incrustado más abajo) para que sobreviva siempre: es lo único que le
    # dice a Midjourney en palabras qué categoría estética aplicar (los
    # parámetros --s/--raw/--chaos por sí solos no alcanzan). Estaba dentro
    # de `bloques` — casi al final — así que en prompts largos (el caso más
    # común, sobre todo con jerarquía física activada) el truncamiento se lo
    # comía antes de llegar a Midjourney, y la categoría elegida quedaba sin
    # ningún efecto visible en el resultado.
    if medio_estilo:
        cuerpo = cuerpo.rstrip(". ") + f". {medio_estilo}"

    # D2: slider de intensidad — interpola stylize/chaos/weird dentro del
    # rango sugerido de la categoría en vez de fijar siempre el midpoint.
    # intensidad=0.5 (default) reproduce el midpoint de siempre para
    # stylize; para chaos/weird, que antes tomaban directo el mínimo del
    # rango sugerido, el midpoint por default es un cambio de comportamiento
    # deliberado (un solo slider controla los tres ejes de forma coherente
    # en vez de que chaos/weird queden fijos en su piso).
    intensidad = sol.intensidad if sol.intensidad is not None else 0.5

    def _interpolar(rango: Optional[tuple[int, int]]) -> int:
        if not rango:
            return 0
        lo, hi = rango
        return round(lo + intensidad * (hi - lo))

    stylize_val = _interpolar((perfil.stylize_min, perfil.stylize_max))

    # --- render de texto tipográfico (doc: "Renderizado de Texto") ---
    raw_val = bool(perfil.raw_obligatorio) if perfil.raw_obligatorio is not None else False

    if sol.texto_incrustado:
        n_pal = len(sol.texto_incrustado.split())
        if n_pal > 4:
            warnings.append(
                f"Texto incrustado tiene {n_pal} palabras; doc recomienda 2-4 "
                f"para máxima fidelidad tipográfica."
            )
        # En inglés: el resto del prompt se redacta en inglés (regla 8 del
        # propio SYSTEM_INSTRUCTION de decomposición) — mezclar español acá
        # contradecía esa regla en el propio motor determinístico.
        cuerpo += f', with the text "{sol.texto_incrustado}" clearly legible'
        # doc: renderizado de texto exige raw o stylize muy bajo, casi imperativo
        if not raw_val:
            raw_val = True
            warnings.append(
                "raw forzado a True: texto incrustado presente y el perfil no lo "
                "exigía por defecto (doc: 'requisito casi absoluto para "
                "renderizado de texto ortográficamente correcto')."
            )
        stylize_val = min(stylize_val, 100)

    if modelo_efectivo == ModeloMJ.NIJI_7:
        raw_val = False   # doc: niji no opera con raw de la familia V8.x

    # conteo_final: cuántas palabras del CUERPO ve Midjourney realmente,
    # después de truncar y de agregar medio_estilo/texto_incrustado (que
    # sobreviven al truncamiento a propósito). conteo_palabras (arriba) mide
    # el riesgo de activar el Prompt Shortener; este mide lo que se envía.
    conteo_final = len(cuerpo.split())

    params = ParametrosMJ(
        ar=sol.ar,
        v=modelo_efectivo,
        resolucion=sol.modo,
        stylize=stylize_val,
        chaos=_interpolar(perfil.chaos_sugerido),
        weird=_interpolar(perfil.weird_sugerido),
        raw=raw_val,
        exp=0,
        # Nunca un placeholder literal: si el perfil recomienda --p pero el
        # usuario no puso uno, se avisa por warning en vez de emitir
        # "--p <CODIGO_P_USUARIO>" al prompt final (Midjourney lo rechaza
        # tal cual, no es un marcador que el usuario vaya a completar ahí).
        p=sol.p,
        sref=sol.sref,
        seed=sol.seed,
        stop=sol.stop if sol.stop is not None else 100,
        tile=sol.tile,
        no=sol.no or [],
    )
    warnings.extend(params.warnings)

    if perfil.p_recomendado and not sol.p:
        warnings.append(
            "Categoría editorial: se recomienda --p <tu código de "
            "personalización>; añádelo en Overrides."
        )

    # --- ensamblar string final de parámetros ---
    partes_param = [f"--ar {params.ar}"]
    if params.raw:
        partes_param.append("--raw")
    partes_param.append(f"--s {params.stylize}")
    if params.chaos:
        partes_param.append(f"--chaos {params.chaos}")
    if params.weird:
        partes_param.append(f"--weird {params.weird}")
    if params.p:
        partes_param.append(f"--p {params.p}")
    if params.sref:
        partes_param.append(f"--sref {params.sref}")
    if params.seed is not None:
        partes_param.append(f"--seed {params.seed}")
    if params.no:
        partes_param.append(f"--no {', '.join(params.no)}")
    if params.stop < 100:
        partes_param.append(f"--stop {params.stop}")
    if params.tile:
        partes_param.append("--tile")
    if modelo_efectivo == ModeloMJ.NIJI_7:
        partes_param.append("--niji 7")
    else:
        partes_param.append(f"--v {params.v.value}")

    prompt_final = f"{cuerpo} {' '.join(partes_param)}".strip()

    return ResultadoPrompt(
        prompt_final=prompt_final,
        parametros=params,
        perfil_aplicado=sol.categoria,
        warnings=warnings,
        conteo_palabras=conteo_palabras,
        conteo_final=conteo_final,
        modelo_efectivo=modelo_efectivo,
    )
