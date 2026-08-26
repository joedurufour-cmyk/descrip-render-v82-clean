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

# Terminología adicional por perfil de proporción
TERMINOLOGIA_PROPORCION = {
    PerfilProporcion.ATHLETIC: "lean functional physique, low body fat",
    PerfilProporcion.HEROIC: "powerful heroic proportions, broad shoulders narrow waist",
    PerfilProporcion.AMAZON: "strong feminine physique, powerful lower body, defined core",
    PerfilProporcion.EXUBERANT_ANIME: "hyper-muscular anime physique, exaggerated proportions, extreme vascularity",
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
    
    # Nivel abdominal (A0-A8)
    if nivel and nivel != NivelAbdominal.A0:
        partes.append(TERMINOLOGIA_ABDOMINAL[nivel])
    
    # Packs específicos (override del nivel)
    if packs and packs >= 4:
        partes.append(f"{packs}-pack abs, deeply defined")
    
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
    
    # Ajuste por género
    if genero == GeneroFisico.FEMENINO:
        # Asegurar que la terminología sea femenina
        partes = [p.replace("his ", "her ").replace("male ", "female ") for p in partes]
    
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
                     "no combinable con -v 8.2). El rango 200-300 es SOLO fallback "
                     "si el flujo de trabajo obliga a usar V8.2 base.",
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
}


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
    v: ModeloMJ = ModeloMJ.V8_2
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

    # ---- doc: "Trampa HD vs SD" — ar máx 4:1 en hd, 14:1 en sd ---
    @model_validator(mode="after")
    def _validar_resolucion_ar(self) -> "ParametrosMJ":
        try:
            w, h = (float(x) for x in self.ar.split(":"))
            ratio = max(w / h, h / w)
        except Exception:
            ratio = 1.0
        if self.resolucion == Resolucion.HD and ratio > 4.0:
            self.warnings.append(
                f"--ar {self.ar} excede 4:1 permitido en modo HD (doc: 'techo de "
                f"procesamiento inquebrantable'). Se recomienda renderizar en SD "
                f"(hasta 14:1) y reservar HD como export terminal."
            )
        return self

    # ---- doc: niji es modelo independiente, NO combinable con v8.x ---
    @model_validator(mode="after")
    def _validar_niji_exclusivo(self) -> "ParametrosMJ":
        if self.v == ModeloMJ.NIJI_7 and (self.stylize != 100 or self.raw or self.exp > 0):
            self.warnings.append(
                "Niji 7 es una familia de modelo independiente: los parámetros "
                "estéticos ajustados para V8.2 (stylize/raw/exp) no tienen "
                "interpolación nativa garantizada; validar visualmente."
            )
        return self

    # ---- doc: exp>25-50 suprime stylize; profesional = exp<25 ---
    @model_validator(mode="after")
    def _validar_exp_vs_stylize(self) -> "ParametrosMJ":
        if self.exp >= 25:
            self.warnings.append(
                f"--exp {self.exp} ≥25: umbral de sobreescritura (doc: 'punto de "
                f"quiebre crítico 25-50'). Empieza a suprimir --stylize "
                f"{self.stylize}. Para determinismo profesional, exp<25."
            )
        return self

    # ---- doc: --raw no imposibilita stylize alto, pero raw+weird es contradictorio ---
    @model_validator(mode="after")
    def _validar_raw_vs_weird(self) -> "ParametrosMJ":
        if self.raw and self.weird > 0:
            self.warnings.append(
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
    medio_estilo: Optional[str] = None
    lente_angulo: Optional[str] = None
    categoria: Optional[CategoriaEstetica] = None
    texto_incrustado: Optional[str] = None
    ar: Optional[str] = None
    p: Optional[str] = None
    sref: Optional[str] = None
    forzar_v8_2_en_anime: Optional[bool] = None
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
    
    # Si hay descripción física construida, combinar con rasgos existentes
    rasgos_fisicos = ov.rasgos_fisicos or vision.rasgos_fisicos_detectados
    if desc_fisica:
        if rasgos_fisicos:
            rasgos_fisicos = f"{rasgos_fisicos}, {desc_fisica}"
        else:
            rasgos_fisicos = desc_fisica
    
    return SolicitudPrompt(
        sujeto=ov.sujeto or vision.sujeto_detectado,
        rasgos_fisicos=rasgos_fisicos,
        accion_estado=ov.accion_estado or vision.accion_estado_detectado,
        contexto_entorno=ov.contexto_entorno or vision.contexto_detectado,
        iluminacion_atmosfera=ov.iluminacion_atmosfera or vision.iluminacion_detectada,
        medio_estilo=ov.medio_estilo or vision.medio_estilo_detectado,
        lente_angulo=ov.lente_angulo or vision.lente_angulo_detectado,
        categoria=ov.categoria or vision.categoria_sugerida,
        texto_incrustado=(
            ov.texto_incrustado if ov.texto_incrustado is not None
            else vision.texto_detectado_ocr
        ),
        modo=modo,
        ar=ov.ar or "1:1",
        p=ov.p,
        sref=ov.sref,
        forzar_v8_2_en_anime=(
            ov.forzar_v8_2_en_anime if ov.forzar_v8_2_en_anime is not None else False
        ),
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
    'una imagen → varios estilos' pedido explícitamente."""
    resultados = {}
    for cat in categorias_destino:
        ov_cat = (
            overrides.model_copy(update={"categoria": cat}) if overrides
            else OverridesTexto(categoria=cat)
        )
        sol = fusionar_vision_y_overrides(vision, ov_cat, modo=modo)
        resultados[cat] = construir_prompt(sol)
    return resultados


class ResultadoPrompt(BaseModel):
    prompt_final: str
    parametros: ParametrosMJ
    perfil_aplicado: CategoriaEstetica
    warnings: List[str]
    conteo_palabras: int
    modelo_efectivo: ModeloMJ


# ═══════════════════════════════════════════════════════════
# 4. ALGORITMO DETERMINÍSTICO — construcción del prompt (front-loading)
#    doc: "Fórmula de Prompting V8.2 Óptima"
#    [Sujeto+rasgos] + [Acción/Estado] + [Contexto] + [Iluminación] +
#    [Medio/Estilo] + [Lente/Ángulo] + [Parámetros]
# ═══════════════════════════════════════════════════════════

LIMITE_PALABRAS_SEGURO = 70   # doc: 60-80 palabras antes de activar Prompt Shortener


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


def construir_prompt(sol: SolicitudPrompt) -> ResultadoPrompt:
    perfil = PERFILES[sol.categoria]
    warnings: List[str] = []

    # --- resolver modelo efectivo (override niji) ---
    modelo_efectivo = ModeloMJ.V8_2
    if perfil.modelo_override == ModeloMJ.NIJI_7 and not sol.forzar_v8_2_en_anime:
        modelo_efectivo = ModeloMJ.NIJI_7
        warnings.append(
            "Categoría anime/manga: enrutado a Niji 7 (modelo base independiente). "
            "Usa forzar_v8_2_en_anime=True para forzar V8.2 base con fallback "
            "stylize 200-300 (calidad anime inferior)."
        )

    # --- construir descripción física automáticamente si hay jerarquía ---
    desc_fisica_auto = construir_descripcion_fisica(
        nivel=sol.nivel_abdominal,
        proporcion=sol.proporcion,
        genero=sol.genero,
        tags=sol.tags_fisico,
        packs=sol.packs,
        low_waist=sol.low_waist,
    )
    
    # Combinar rasgos_fisicos con descripción física automática
    rasgos_combinados = sol.rasgos_fisicos or ""
    if desc_fisica_auto:
        if rasgos_combinados:
            rasgos_combinados = f"{rasgos_combinados}, {desc_fisica_auto}"
        else:
            rasgos_combinados = desc_fisica_auto

    # --- construir bloques en orden front-loading obligatorio ---
    bloques = [
        f"{sol.sujeto}, {rasgos_combinados}" if rasgos_combinados else sol.sujeto,
        sol.accion_estado,
        sol.contexto_entorno,
        sol.iluminacion_atmosfera,
        sol.medio_estilo,
        sol.lente_angulo,
    ]
    cuerpo = ". ".join(b.strip() for b in bloques if b and b.strip())
    cuerpo, warns_legacy = _purgar_sintaxis_legacy(cuerpo)
    warnings.extend(warns_legacy)

    # --- TRUNCAMIENTO DURO: nunca exceder 70 palabras ---
    conteo_palabras = len(cuerpo.split())
    if conteo_palabras > LIMITE_PALABRAS_SEGURO:
        cuerpo = _truncar_a_max_palabras(cuerpo, LIMITE_PALABRAS_SEGURO)
        warnings.append(
            f"Prompt truncado de {conteo_palabras} a {LIMITE_PALABRAS_SEGURO} palabras "
            f"(doc: límite seguro para evitar Prompt Shortener). "
            f"Priorizados: sujeto, rasgos físicos, acción."
        )

    # --- render de texto tipográfico (doc: "Renderizado de Texto") ---
    stylize_val = (perfil.stylize_min + perfil.stylize_max) // 2
    raw_val = bool(perfil.raw_obligatorio) if perfil.raw_obligatorio is not None else False

    if sol.texto_incrustado:
        n_pal = len(sol.texto_incrustado.split())
        if n_pal > 4:
            warnings.append(
                f"Texto incrustado tiene {n_pal} palabras; doc recomienda 2-4 "
                f"para máxima fidelidad tipográfica."
            )
        cuerpo += f', letrero que dice "{sol.texto_incrustado}"'
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
        raw_val = False   # doc: niji no opera con raw de V8.2

    # Nota: conteo y truncamiento de palabras ya aplicados arriba

    params = ParametrosMJ(
        ar=sol.ar,
        v=modelo_efectivo,
        resolucion=sol.modo,
        stylize=stylize_val,
        chaos=(perfil.chaos_sugerido[0] if perfil.chaos_sugerido else 0),
        weird=(perfil.weird_sugerido[0] if perfil.weird_sugerido else 0),
        raw=raw_val,
        exp=0,
        p=sol.p or (None if not perfil.p_recomendado else "<CODIGO_P_USUARIO>"),
        sref=sol.sref,
    )
    warnings.extend(params.warnings)

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
        modelo_efectivo=modelo_efectivo,
    )
