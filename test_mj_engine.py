"""
test_mj_engine.py — Tests exhaustivos del motor determinístico.
17 casos que validan: perfiles, parámetros, purga de legacy, HD/SD,
visión+overrides, y regeneración multi-estilo.
"""

import sys

from mj_engine import (
    SolicitudPrompt,
    CategoriaEstetica,
    Resolucion,
    ModeloMJ,
    ParametrosMJ,
    DescripcionVisual,
    OverridesTexto,
    construir_prompt,
    fusionar_vision_y_overrides,
    regenerar_en_estilos,
)

fallos = []

def check(cond, msg):
    if not cond:
        fallos.append(msg)

# ═══════════════════════════════════════════════════════════
# CASOS A-K: Tests base del motor
# ═══════════════════════════════════════════════════════════

# --- Caso A: Fotorealismo documental (doc ejemplo A) ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Pescador anciano con piel curtida y cicatrices",
    accion_estado="reparando una red de nailon amarilla brillante",
    contexto_entorno="muelle de madera desgastado",
    iluminacion_atmosfera="luz natural nublada y plana",
    medio_estilo="película de 35mm, granulado fílmico sutil",
    lente_angulo="lente de 85mm, profundidad de campo reducida",
    categoria=CategoriaEstetica.FOTOREALISMO_RETRATO,
    ar="4:5",
))
check(r.parametros.raw is True, "A: raw debe ser True en fotorealismo")
check(0 <= r.parametros.stylize <= 100, f"A: stylize fuera de rango: {r.parametros.stylize}")
print("A OK ->", r.prompt_final, "| warnings:", r.warnings)

# --- Caso B: Tipografía comercial (doc ejemplo B) ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Póster publicitario minimalista, fondo rosa pastel sólido",
    accion_estado="lata de refresco verde cian con condensación flotando al centro",
    iluminacion_atmosfera="estudio softbox brillante y uniforme",
    categoria=CategoriaEstetica.EDITORIAL_MODA,  # perfil con raw_obligatorio=False por defecto
    texto_incrustado="FRESH",
    ar="3:4",
))
check(r.parametros.raw is True, "B: raw debe forzarse a True por texto incrustado")
check(r.parametros.stylize <= 100, f"B: stylize debe bajar a <=100 con texto: {r.parametros.stylize}")
check('"FRESH"' in r.prompt_final, "B: texto debe ir entre comillas dobles")
check(any("forzado a True" in w for w in r.warnings), "B: debe emitir warning de raw forzado")
print("B OK ->", r.prompt_final, "| warnings:", r.warnings)

# --- Caso C: Conceptual/fantasía alto stylize + chaos (doc ejemplo C) ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Monolito de cristal asimétrico gigante",
    accion_estado="flotando sobre una metrópolis gótica fractal en ruinas",
    iluminacion_atmosfera="tormenta eléctrica verde bioluminiscente, niebla espesa",
    lente_angulo="ángulo bajo extremo, escala colosal",
    categoria=CategoriaEstetica.CONCEPTUAL_FANTASIA,
    ar="16:9",
    p="8a3m9z",
))
check(700 <= r.parametros.stylize <= 1000, f"C: stylize debe estar 700-1000: {r.parametros.stylize}")
check(r.parametros.raw is False, "C: raw no debe forzarse en conceptual sin texto")
check(r.parametros.p == "8a3m9z", "C: código --p debe respetarse")
print("C OK ->", r.prompt_final, "| warnings:", r.warnings)

# --- Caso D: Anime -> debe enrutar a Niji 7 por defecto ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Guerrera samurái joven",
    contexto_entorno="bosque de bambú al atardecer",
    categoria=CategoriaEstetica.ANIME_MANGA,
))
check(r.modelo_efectivo == ModeloMJ.NIJI_7, "D: anime debe enrutar a niji-7 por defecto")
check("--niji 7" in r.prompt_final, "D: prompt final debe usar --niji 7, no --v 8.2")
check(r.parametros.raw is False, "D: niji no debe llevar --raw")
print("D OK ->", r.prompt_final, "| warnings:", r.warnings)

# --- Caso D2: Anime forzado en V8.1 base ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Guerrera samurái joven",
    categoria=CategoriaEstetica.ANIME_MANGA,
    forzar_v8_2_en_anime=True,
))
check(r.modelo_efectivo == ModeloMJ.V8_1, "D2: override debe mantener v8.1")
check(200 <= r.parametros.stylize <= 300, f"D2: fallback stylize 200-300: {r.parametros.stylize}")
print("D2 OK ->", r.prompt_final)

# --- Caso E: Trampa HD — ar 16:9 en HD (ratio 1.78) no debe disparar warning ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Ciudad futurista",
    categoria=CategoriaEstetica.CINE,
    modo=Resolucion.HD,
    ar="16:9",
))
check(not any("excede 4:1" in w for w in r.warnings), "E: 16:9 (1.78:1) no debe disparar warning de HD")
print("E OK -> warnings:", r.warnings)

# --- Caso F: Trampa HD real — ar 10:1 en HD debe disparar warning ---
r = construir_prompt(SolicitudPrompt(
    sujeto="Panorámica de montañas",
    categoria=CategoriaEstetica.CINE,
    modo=Resolucion.HD,
    ar="10:1",
))
check(any("excede 4:1" in w for w in r.warnings), "F: ar 10:1 en HD debe disparar warning")
print("F OK -> warnings:", r.warnings)

# --- Caso G: Legacy params y sintaxis :: deben purgarse ---
r = construir_prompt(SolicitudPrompt(
    sujeto="cielo nocturno::2 bosque::1 --quality 2 escena de fantasía",
    categoria=CategoriaEstetica.CONCEPTUAL_FANTASIA,
))
check("::" not in r.prompt_final, "G: sintaxis :: debe purgarse del cuerpo")
check("--quality" not in r.prompt_final, "G: --quality debe purgarse del cuerpo")
check("uality" not in r.prompt_final, "G: no debe quedar residuo de substring '--q' dentro de '--quality'")
check(len(r.warnings) >= 2, "G: debe reportar al menos 2 warnings (legacy + ::)")
print("G OK ->", r.prompt_final, "| warnings:", r.warnings)

# --- Caso G2: --q corto (sin -uality) también debe purgarse limpio ---
r2 = construir_prompt(SolicitudPrompt(
    sujeto="retrato de estudio --q 2 alta calidad",
    categoria=CategoriaEstetica.FOTOREALISMO_RETRATO,
))
check("--q" not in r2.prompt_final.split("--ar")[0], "G2: --q corto debe purgarse del cuerpo sin residuo")
print("G2 OK ->", r2.prompt_final)

# --- Caso H: Experimental/surrealismo con raw activo a la fuerza -> warning contradicción ---
p = ParametrosMJ(ar="1:1", v=ModeloMJ.V8_2, stylize=650, weird=800, raw=True)
check(any("raw" in w.lower() and "weird" in w.lower() for w in p.warnings),
      "H: raw+weird simultáneos deben generar warning de contradicción")
print("H OK -> warnings:", p.warnings)

# --- Caso I: exp alto debe advertir sobreescritura de stylize ---
p = ParametrosMJ(ar="1:1", stylize=500, exp=40)
check(any("exp" in w.lower() and "40" in w for w in p.warnings), "I: exp>=25 debe advertir")
print("I OK -> warnings:", p.warnings)

# --- Caso J: prompt largo debe disparar warning de Prompt Shortener ---
sujeto_largo = " ".join(["palabra"] * 120)
r = construir_prompt(SolicitudPrompt(sujeto=sujeto_largo, categoria=CategoriaEstetica.CINE))
check(any("Prompt Shortener" in w for w in r.warnings), "J: prompt >100 palabras debe advertir shortener")
print("J OK -> conteo:", r.conteo_palabras)

# --- Caso K: validación de rangos duros pydantic (debe fallar si excede) ---
try:
    ParametrosMJ(stylize=1500)
    fallos.append("K: stylize=1500 debería lanzar ValidationError")
except Exception:
    print("K OK -> ValidationError esperado para stylize=1500")

try:
    ParametrosMJ(chaos=200)
    fallos.append("K2: chaos=200 debería lanzar ValidationError")
except Exception:
    print("K2 OK -> ValidationError esperado para chaos=200")

# ═══════════════════════════════════════════════════════════
# ETAPA 0 — VISIÓN: descripción de imagen + fusión con overrides
# ═══════════════════════════════════════════════════════════

# --- Caso L: descripción visual pura, sin overrides ---
vision = DescripcionVisual(
    sujeto_detectado="Mujer con abrigo rojo",
    rasgos_fisicos_detectados="cabello corto plateado, gafas redondas",
    accion_estado_detectado="caminando bajo la lluvia",
    contexto_detectado="calle urbana con neones reflejados en el pavimento",
    iluminacion_detectada="luz nocturna de neón, tonos azul y magenta",
    medio_estilo_detectado="fotografía digital",
    categoria_sugerida=CategoriaEstetica.CYBERPUNK_SCIFI,
)
sol = fusionar_vision_y_overrides(vision)
r = construir_prompt(sol)
check(r.perfil_aplicado == CategoriaEstetica.CYBERPUNK_SCIFI, "L: categoría detectada debe respetarse sin override")
check("Mujer con abrigo rojo" in r.prompt_final, "L: sujeto detectado debe aparecer en el prompt")
check(300 <= r.parametros.stylize <= 500, f"L: stylize cyberpunk fuera de rango: {r.parametros.stylize}")
print("L OK ->", r.prompt_final)

# --- Caso M: override de texto cambia SOLO la iluminación, conserva el resto ---
ov = OverridesTexto(iluminacion_atmosfera="amanecer dorado, niebla suave")
sol2 = fusionar_vision_y_overrides(vision, ov)
check(sol2.sujeto == vision.sujeto_detectado, "M: sujeto debe conservarse cuando no hay override")
check(sol2.iluminacion_atmosfera == "amanecer dorado, niebla suave", "M: override de iluminación debe aplicarse")
r2 = construir_prompt(sol2)
check("amanecer dorado" in r2.prompt_final, "M: prompt final debe reflejar override de iluminación")
check("luz nocturna de neón" not in r2.prompt_final, "M: iluminación original NO debe filtrarse tras override")
print("M OK ->", r2.prompt_final)

# --- Caso N: override de categoría = regenerar el mismo sujeto en otro estilo ---
ov_estilo = OverridesTexto(categoria=CategoriaEstetica.VINTAGE_ANALOGICA)
sol3 = fusionar_vision_y_overrides(vision, ov_estilo)
r3 = construir_prompt(sol3)
check(r3.perfil_aplicado == CategoriaEstetica.VINTAGE_ANALOGICA, "N: override de categoría debe cambiar perfil")
check(r3.parametros.raw is True, "N: vintage debe forzar raw")
check("Mujer con abrigo rojo" in r3.prompt_final, "N: sujeto debe conservarse a pesar de cambio de estilo")
print("N OK ->", r3.prompt_final)

# --- Caso O: lote multi-estilo desde una imagen ---
cats = [CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.ANIME_MANGA]
lote = regenerar_en_estilos(vision, cats)
check(len(lote) == 3, "O: lote debe producir 3 resultados")
check(lote[CategoriaEstetica.FOTOREALISMO_RETRATO].parametros.raw is True, "O: fotoreal en lote debe tener raw")
check(lote[CategoriaEstetica.CONCEPTUAL_FANTASIA].parametros.stylize >= 700, "O: conceptual en lote debe tener stylize alto")
check(lote[CategoriaEstetica.ANIME_MANGA].modelo_efectivo == ModeloMJ.NIJI_7, "O: anime en lote debe enrutar a niji")
print("O OK -> lote generado:", {k.value: v.prompt_final[:60]+"..." for k, v in lote.items()})

# --- Caso P: OCR detectado fuerza raw igual que texto manual ---
vision_ocr = DescripcionVisual(
    sujeto_detectado="Cartel de concierto",
    contexto_detectado="pared de ladrillo en callejón",
    categoria_sugerida=CategoriaEstetica.EDITORIAL_MODA,
    texto_detectado_ocr="LIVE TONIGHT",
)
sol_ocr = fusionar_vision_y_overrides(vision_ocr)
r_ocr = construir_prompt(sol_ocr)
check(r_ocr.parametros.raw is True, "P: OCR detectado debe forzar raw igual que texto manual")
check('"LIVE TONIGHT"' in r_ocr.prompt_final, "P: OCR debe aparecer entre comillas en prompt")
print("P OK ->", r_ocr.prompt_final)

# ═══════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════

def test_motor_determinista():
    """Entry point compatible con pytest: falla si algún check() anterior falló."""
    assert not fallos, "Fallos:\n" + "\n".join(f"  - {f}" for f in fallos)


if __name__ == "__main__":
    print("\n" + "="*60)
    if fallos:
        print(f"❌ FALLARON {len(fallos)} TESTS:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("✅ TODOS LOS TESTS PASARON (17/17)")
        sys.exit(0)
