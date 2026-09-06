"""
test_mj_engine.py — Tests exhaustivos del motor determinístico.
37 casos que validan: perfiles, parámetros, purga de legacy, HD/SD,
visión+overrides, regeneración multi-estilo, la variante de copia de
estilo original, los fixes de PR-1 (--p sin placeholder, seed/no/stop/
tile, seed compartida en multi-estilo, warning de niji no ruidoso,
conteo_final), y PR-5/Fase D (truncamiento por prioridad de bloque,
slider de intensidad, categorías vecinas determinísticas, paleta de
color).
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
    construir_variante_estilo_original,
    categorias_vecinas,
    VECINOS_ESTETICOS,
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

# --- Caso P: OCR detectado NO se aplica solo (riesgo de alucinación) ---
# texto_detectado_ocr es solo informativo (vive en source_analysis/vision_raw
# para que el usuario lo revise) — nunca se cuela automáticamente al prompt,
# porque una lectura de OCR alucinada forzaría --raw y un letrero falso en
# la imagen. Solo el override EXPLÍCITO del usuario debe aplicarlo.
vision_ocr = DescripcionVisual(
    sujeto_detectado="Cartel de concierto",
    contexto_detectado="pared de ladrillo en callejón",
    categoria_sugerida=CategoriaEstetica.EDITORIAL_MODA,
    texto_detectado_ocr="LIVE TONIGHT",
)
sol_ocr = fusionar_vision_y_overrides(vision_ocr)
r_ocr = construir_prompt(sol_ocr)
check(r_ocr.parametros.raw is False, "P: OCR detectado sin override NO debe forzar raw")
check("LIVE TONIGHT" not in r_ocr.prompt_final, "P: OCR detectado sin override NO debe aparecer en el prompt")
print("P OK ->", r_ocr.prompt_final)

# --- Caso P2: override explícito de texto_incrustado sí fuerza raw ---
ov_texto = OverridesTexto(texto_incrustado="LIVE TONIGHT")
sol_ocr2 = fusionar_vision_y_overrides(vision_ocr, ov_texto)
r_ocr2 = construir_prompt(sol_ocr2)
check(r_ocr2.parametros.raw is True, "P2: override explícito de texto SÍ debe forzar raw")
check('"LIVE TONIGHT"' in r_ocr2.prompt_final, "P2: texto debe aparecer entre comillas cuando es override explícito")
print("P2 OK ->", r_ocr2.prompt_final)

# --- Caso Q: variante "copia del estilo original" — sin overrides ---
# Debe usar categoria_sugerida (no una elegida por el usuario) y el medio
# literal detectado por visión, no un DESCRIPTOR_ESTILO genérico.
r_q = construir_variante_estilo_original(vision)
check(r_q.perfil_aplicado == vision.categoria_sugerida, "Q: debe usar categoria_sugerida de la visión")
check("fotografía digital" in r_q.prompt_final, "Q: medio_estilo_detectado debe aparecer literal en el prompt")
check("Mujer con abrigo rojo" in r_q.prompt_final, "Q: sujeto detectado debe conservarse")
print("Q OK ->", r_q.prompt_final)

# --- Caso Q2: variante estilo-original IGNORA la categoría elegida por el
# usuario para las otras variantes — es intencional, siempre usa la
# detectada, porque el objetivo es reproducir el estilo real de la imagen ---
ov_otra_cat = OverridesTexto(categoria=CategoriaEstetica.FOTOREALISMO_RETRATO)
r_q2 = construir_variante_estilo_original(vision, ov_otra_cat)
check(r_q2.perfil_aplicado == vision.categoria_sugerida, "Q2: override.categoria del usuario NO debe pisar categoria_sugerida")
print("Q2 OK ->", r_q2.prompt_final)

# --- Caso Q3: override EXPLÍCITO de medio_estilo sigue ganando incluso en
# la variante de estilo original (regla general del motor: override > detección) ---
ov_medio = OverridesTexto(medio_estilo="acuarela suelta, pinceladas visibles")
r_q3 = construir_variante_estilo_original(vision, ov_medio)
check("acuarela suelta" in r_q3.prompt_final, "Q3: override explícito de medio_estilo debe ganarle al detectado")
check("fotografía digital" not in r_q3.prompt_final, "Q3: medio detectado no debe filtrarse si hay override explícito")
print("Q3 OK ->", r_q3.prompt_final)

# ═══════════════════════════════════════════════════════════
# CASOS R-V: fixes de PR-1 (bugs y fugas del contrato)
# ═══════════════════════════════════════════════════════════

# --- Caso R: --p nunca se emite como placeholder literal; se avisa por warning ---
r_r = construir_prompt(SolicitudPrompt(
    sujeto="Modelo de alta costura en pasarela",
    categoria=CategoriaEstetica.EDITORIAL_MODA,  # p_recomendado=True, sin p explícito
))
check("<CODIGO_P_USUARIO>" not in r_r.prompt_final, "R: el prompt final NUNCA debe contener el placeholder literal de --p")
check("--p" not in r_r.prompt_final, "R: sin p explícito, --p no debe aparecer en absoluto")
check(any("se recomienda --p" in w for w in r_r.warnings), "R: debe avisar por warning que --p es recomendado")
print("R OK ->", r_r.prompt_final, "| warnings:", r_r.warnings)

# --- Caso R2: con p explícito, editorial no debe advertir ---
r_r2 = construir_prompt(SolicitudPrompt(
    sujeto="Modelo de alta costura en pasarela",
    categoria=CategoriaEstetica.EDITORIAL_MODA,
    p="8a3m9z",
))
check("--p 8a3m9z" in r_r2.prompt_final, "R2: --p explícito debe emitirse tal cual")
check(not any("se recomienda --p" in w for w in r_r2.warnings), "R2: con p explícito no debe advertir")
print("R2 OK ->", r_r2.prompt_final)

# --- Caso S: seed/no/stop/tile se ensamblan al prompt final (antes se validaban y se descartaban) ---
r_s = construir_prompt(SolicitudPrompt(
    sujeto="Retrato de estudio",
    categoria=CategoriaEstetica.FOTOREALISMO_RETRATO,
    seed=123456,
    no=["blurry", "text"],
    stop=80,
    tile=True,
))
check("--seed 123456" in r_s.prompt_final, "S: --seed debe aparecer en el prompt final")
check("--no blurry, text" in r_s.prompt_final, "S: --no debe listar los elementos separados por coma")
check("--stop 80" in r_s.prompt_final, "S: --stop <100 debe aparecer explícito")
check("--tile" in r_s.prompt_final, "S: --tile debe aparecer cuando está activo")
print("S OK ->", r_s.prompt_final)

# --- Caso S2: stop=100 (default) y tile=False no deben ensuciar el prompt ---
r_s2 = construir_prompt(SolicitudPrompt(sujeto="Retrato de estudio", categoria=CategoriaEstetica.FOTOREALISMO_RETRATO))
check("--stop" not in r_s2.prompt_final, "S2: --stop 100 (default) no debe emitirse")
check("--tile" not in r_s2.prompt_final, "S2: --tile False no debe emitirse")
check("--seed" not in r_s2.prompt_final, "S2: sin seed no debe emitirse --seed")
print("S2 OK ->", r_s2.prompt_final)

# --- Caso T: seed compartida en multi-estilo — las N variantes deben llevar la MISMA seed ---
cats_t = [CategoriaEstetica.FOTOREALISMO_RETRATO, CategoriaEstetica.CONCEPTUAL_FANTASIA, CategoriaEstetica.CINE]
lote_t = regenerar_en_estilos(vision, cats_t)
seeds_t = {v.parametros.seed for v in lote_t.values()}
check(len(seeds_t) == 1, f"T: las N variantes deben compartir la misma seed, encontradas: {seeds_t}")
check(None not in seeds_t, "T: la seed compartida generada automáticamente no debe ser None")
print("T OK -> seed compartida:", seeds_t)

# --- Caso T2: si el usuario fija una seed explícita, esa es la que se comparte ---
lote_t2 = regenerar_en_estilos(vision, cats_t, OverridesTexto(seed=999))
seeds_t2 = {v.parametros.seed for v in lote_t2.values()}
check(seeds_t2 == {999}, f"T2: seed explícita del usuario debe propagarse a todas las variantes, encontradas: {seeds_t2}")
print("T2 OK -> seed compartida:", seeds_t2)

# --- Caso U: warning ruidoso de niji ya NO dispara en el caso default (solo stylize distinto de 100) ---
r_u = construir_prompt(SolicitudPrompt(sujeto="Idolo pop anime", categoria=CategoriaEstetica.ANIME_MANGA))
check(not any("no tienen interpolación nativa" in w for w in r_u.warnings), "U: warning ruidoso de niji no debe dispararse en el caso default (solo por stylize!=100)")
print("U OK -> warnings:", r_u.warnings)

# --- Caso U2: el warning de niji SÍ debe seguir avisando cuando hay raw/exp real (forzado vía ParametrosMJ) ---
p_u2 = ParametrosMJ(v=ModeloMJ.NIJI_7, raw=True)
check(any("no tienen interpolación nativa" in w for w in p_u2.warnings), "U2: con raw=True SÍ debe advertir sobre niji")
print("U2 OK -> warnings:", p_u2.warnings)

# --- Caso V: conteo_final refleja el cuerpo real (con medio_estilo/texto), distinto de conteo_palabras ---
sujeto_largo_v = " ".join(["palabra"] * 120)
r_v = construir_prompt(SolicitudPrompt(sujeto=sujeto_largo_v, categoria=CategoriaEstetica.CINE, texto_incrustado="HOLA"))
check(r_v.conteo_palabras > 100, "V: conteo_palabras debe reflejar el total ANTES de truncar")
check(r_v.conteo_final <= 100 + 20, f"V: conteo_final debe reflejar el cuerpo truncado + medio_estilo + texto: {r_v.conteo_final}")
check(r_v.conteo_final != r_v.conteo_palabras, "V: conteo_final y conteo_palabras deben ser distintos cuando hubo truncamiento")
print("V OK -> conteo_palabras:", r_v.conteo_palabras, "conteo_final:", r_v.conteo_final)

# ═══════════════════════════════════════════════════════════
# CASOS W-Z: PR-5 / Fase D (calidad del prompt)
# ═══════════════════════════════════════════════════════════

# --- Caso W: truncamiento por prioridad — descarta lente/paleta/iluminación
# ANTES que sujeto/acción/contexto, y el warning nombra qué se descartó ---
sujeto_w = " ".join(["subject"] * 40)
accion_w = " ".join(["action"] * 30)
contexto_w = " ".join(["context"] * 20)
r_w = construir_prompt(SolicitudPrompt(
    sujeto=sujeto_w,
    accion_estado=accion_w,
    contexto_entorno=contexto_w,
    iluminacion_atmosfera="dramatic golden hour lighting",
    paleta="teal and orange",
    lente_angulo="85mm lens, shallow depth of field",
    categoria=CategoriaEstetica.CINE,
))
check(sujeto_w in r_w.prompt_final, "W: sujeto (máxima prioridad) debe sobrevivir al truncamiento")
check(accion_w in r_w.prompt_final, "W: acción (2da prioridad) debe sobrevivir")
check("85mm lens" not in r_w.prompt_final, "W: lente (mínima prioridad) debe descartarse primero")
check(any("Bloques descartados" in wmsg and "lente" in wmsg for wmsg in r_w.warnings), "W: el warning debe nombrar 'lente' como bloque descartado")
print("W OK -> warnings:", [wmsg for wmsg in r_w.warnings if "descartad" in wmsg.lower()])

# --- Caso W2: si SOLO sujeto_rasgos excede el límite, no hay bloques para
# descartar -> cae al recorte por palabras de siempre (fallback), sin crashear ---
sujeto_solo_largo = " ".join(["palabra"] * 150)
r_w2 = construir_prompt(SolicitudPrompt(sujeto=sujeto_solo_largo, categoria=CategoriaEstetica.CINE))
check(r_w2.conteo_final <= 100 + 20, f"W2: con solo sujeto largo, debe recortarse por palabras igual que antes (+medio_estilo que sobrevive aparte), dio {r_w2.conteo_final}")
check(any("Recorte por palabras" in wmsg for wmsg in r_w2.warnings), "W2: el warning debe indicar que se usó el recorte por palabras (no había bloques para descartar)")
print("W2 OK -> conteo_final:", r_w2.conteo_final)

# --- Caso X: slider de intensidad — 0.0 da el mínimo del rango, 1.0 el máximo ---
r_x_min = construir_prompt(SolicitudPrompt(sujeto="Ciudad futurista", categoria=CategoriaEstetica.CYBERPUNK_SCIFI, intensidad=0.0))
r_x_max = construir_prompt(SolicitudPrompt(sujeto="Ciudad futurista", categoria=CategoriaEstetica.CYBERPUNK_SCIFI, intensidad=1.0))
r_x_mid = construir_prompt(SolicitudPrompt(sujeto="Ciudad futurista", categoria=CategoriaEstetica.CYBERPUNK_SCIFI))  # default 0.5
check(r_x_min.parametros.stylize == 300, f"X: intensidad=0.0 debe dar stylize mínimo (300), dio {r_x_min.parametros.stylize}")
check(r_x_max.parametros.stylize == 500, f"X: intensidad=1.0 debe dar stylize máximo (500), dio {r_x_max.parametros.stylize}")
check(r_x_mid.parametros.stylize == 400, f"X: sin intensidad (default 0.5) debe dar el midpoint (400) como siempre, dio {r_x_mid.parametros.stylize}")
check(r_x_min.parametros.chaos == 10, f"X: intensidad=0.0 debe dar chaos mínimo (10), dio {r_x_min.parametros.chaos}")
check(r_x_max.parametros.chaos == 20, f"X: intensidad=1.0 debe dar chaos máximo (20), dio {r_x_max.parametros.chaos}")
print("X OK -> stylize min/mid/max:", r_x_min.parametros.stylize, r_x_mid.parametros.stylize, r_x_max.parametros.stylize,
      "| chaos min/max:", r_x_min.parametros.chaos, r_x_max.parametros.chaos)

# --- Caso Y: categorías vecinas — determinístico, la elegida siempre primera,
# sin duplicados, mismo resultado en llamadas repetidas ---
vecinas_1 = categorias_vecinas(CategoriaEstetica.CINE, 4)
vecinas_2 = categorias_vecinas(CategoriaEstetica.CINE, 4)
check(vecinas_1 == vecinas_2, "Y: categorias_vecinas debe ser 100% determinístico (mismo input, mismo output siempre)")
check(vecinas_1[0] == CategoriaEstetica.CINE, "Y: la categoría elegida debe ir siempre primera")
check(len(vecinas_1) == 4, f"Y: pedir 4 estilos debe devolver 4 categorías, dio {len(vecinas_1)}")
check(len(set(vecinas_1)) == 4, "Y: no debe haber categorías repetidas en el lote")
check(set(vecinas_1[1:]) <= set(VECINOS_ESTETICOS[CategoriaEstetica.CINE]), "Y: las demás deben salir de la tabla de vecinos, no de random.sample")
print("Y OK ->", [c.value for c in vecinas_1])

# --- Caso Z: paleta de color — aparece en el prompt después de iluminación,
# vía override explícito y vía fallback de visión ---
r_z = construir_prompt(SolicitudPrompt(
    sujeto="Retrato en la calle",
    iluminacion_atmosfera="neon night lighting",
    paleta="teal and amber",
    categoria=CategoriaEstetica.CINE,
))
check("palette: teal and amber" in r_z.prompt_final, "Z: paleta explícita debe aparecer en el prompt final")
idx_ilum = r_z.prompt_final.find("neon night lighting")
idx_paleta = r_z.prompt_final.find("palette: teal and amber")
check(0 <= idx_ilum < idx_paleta, "Z: paleta debe ir DESPUÉS de iluminación en el orden del prompt")
print("Z OK ->", r_z.prompt_final)

# --- Caso Z2: paleta detectada por visión se hereda si no hay override explícito ---
vision_paleta = DescripcionVisual(
    sujeto_detectado="Callejón lluvioso",
    paleta_color_detectada="teal and magenta neon",
    categoria_sugerida=CategoriaEstetica.CYBERPUNK_SCIFI,
)
sol_z2 = fusionar_vision_y_overrides(vision_paleta)
check(sol_z2.paleta == "teal and magenta neon", "Z2: paleta detectada por visión debe heredarse sin override")
r_z2 = construir_prompt(sol_z2)
check("teal and magenta neon" in r_z2.prompt_final, "Z2: paleta heredada de visión debe llegar al prompt final")
print("Z2 OK ->", r_z2.prompt_final)

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
        print("✅ TODOS LOS TESTS PASARON (37/37)")
        sys.exit(0)
