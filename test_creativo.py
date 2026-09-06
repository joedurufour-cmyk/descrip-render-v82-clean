"""
test_creativo.py — Tests del tab Creatividad (/v2/creativo), sin red real:
mockea creativo._client.aio.models.generate_content.

Casos del plan V9 (Fase F):
  (a) propuesta con categoria inválida -> 422 por Pydantic, no 500.
  (b) prompt_base presente -> la instrucción de iteración aparece en _contexto().
  (c) overrides de jerarquía física se propagan a propuestas que no los traen.
"""
import asyncio
import json
import sys

import httpx
from unittest.mock import AsyncMock, MagicMock

import creativo
import main as m

fallos = []


def check(cond, msg):
    if not cond:
        fallos.append(msg)


def _fake_resp(payload: dict):
    resp = MagicMock()
    resp.text = json.dumps(payload)
    return resp


async def _run():
    creativo._client = MagicMock()
    creativo._model = "gemini-3.7-flash"

    transport = httpx.ASGITransport(app=m.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        # --- (a) categoría inválida en la respuesta de Gemini -> 422 Pydantic, no 500 ---
        payload_bad = {
            "mensaje": "Acá va una idea",
            "propuestas": [{
                "titulo": "Prueba",
                "razon": "porque sí",
                "solicitud": {
                    "sujeto": "Un gato",
                    "categoria": "categoria_que_no_existe",
                },
            }],
            "sugerencias": [],
        }
        creativo._client.aio.models.generate_content = AsyncMock(return_value=_fake_resp(payload_bad))
        r = await client.post("/v2/creativo", json={"mensajes": [{"rol": "user", "contenido": "hola"}]})
        check(r.status_code == 422, f"(a): categoría inválida del LLM debe dar 422 (dato mal formado), no 500, dio {r.status_code}: {r.text}")
        check("datos inválidos" in r.json().get("detail", ""), f"(a): el detail debe indicar que Gemini devolvió datos inválidos, dio: {r.text}")
        print("(a) OK -> categoría inválida -> 422 Pydantic, no 500:", r.json()["detail"][:80])

        # --- (b) prompt_base presente -> aparece en el contexto que se le manda a Gemini ---
        captured = {}

        async def capturar_contenido(**kwargs):
            captured["contents"] = kwargs["contents"]
            payload_ok = {
                "mensaje": "Dale, ajusto la luz",
                "propuestas": [{
                    "titulo": "Atardecer dorado",
                    "razon": "cambia solo la iluminación",
                    "solicitud": {
                        "sujeto": "Guerrera samurái joven",
                        "iluminacion_atmosfera": "golden hour, warm rim light",
                        "categoria": "cine_fotografia_cinematografica",
                    },
                }],
                "sugerencias": ["más dramático"],
            }
            return _fake_resp(payload_ok)

        creativo._client.aio.models.generate_content = AsyncMock(side_effect=capturar_contenido)
        body = {
            "mensajes": [{"rol": "user", "contenido": "hazla con luz de atardecer"}],
            "prompt_base": "Guerrera samurái joven, bosque de bambú --ar 1:1 --s 250 --niji 7",
            "categoria": "cine_fotografia_cinematografica",
        }
        r2 = await client.post("/v2/creativo", json=body)
        check(r2.status_code == 200, f"(b): debe responder 200, dio {r2.status_code}: {r2.text}")
        contents = captured.get("contents", [])
        primer_texto = contents[0].parts[0].text if contents else ""
        check("PROMPT BASE" in primer_texto, "(b): _contexto() debe incluir la etiqueta PROMPT BASE cuando prompt_base está presente")
        check("Guerrera samurái joven, bosque de bambú" in primer_texto, "(b): el texto del prompt_base debe viajar tal cual en el contexto")
        print("(b) OK -> prompt_base aparece en el contexto enviado a Gemini")

        # --- (c) overrides de jerarquía física se propagan a propuestas que no los traen ---
        async def sin_jerarquia(**kwargs):
            payload_sin_jerarquia = {
                "mensaje": "Va una variante más oscura",
                "propuestas": [{
                    "titulo": "Versión nocturna",
                    "razon": "cambia la iluminación a nocturna",
                    "solicitud": {
                        "sujeto": "Guerrero cyberpunk",
                        "categoria": "cyberpunk_scifi_denso",
                        # sin nivel_abdominal/proporcion/genero -> deben heredarse de overrides
                    },
                }],
                "sugerencias": [],
            }
            return _fake_resp(payload_sin_jerarquia)

        creativo._client.aio.models.generate_content = AsyncMock(side_effect=sin_jerarquia)
        body_c = {
            "mensajes": [{"rol": "user", "contenido": "una versión más oscura"}],
            "overrides": {"nivel_abdominal": "A6", "proporcion": "heroic", "genero": "masculino"},
            "categoria": "cyberpunk_scifi_denso",
        }
        r3 = await client.post("/v2/creativo", json=body_c)
        check(r3.status_code == 200, f"(c): debe responder 200, dio {r3.status_code}: {r3.text}")
        prop = r3.json()["propuestas"][0]
        sol_c = prop["solicitud"]
        check(sol_c["nivel_abdominal"] == "A6", f"(c): nivel_abdominal de overrides debe propagarse a la propuesta, dio: {sol_c.get('nivel_abdominal')}")
        check(sol_c["proporcion"] == "heroic", f"(c): proporcion de overrides debe propagarse, dio: {sol_c.get('proporcion')}")
        check(sol_c["genero"] == "masculino", f"(c): genero de overrides debe propagarse, dio: {sol_c.get('genero')}")
        check("perfect 10-pack" in prop["prompt"] or "A6" in json.dumps(sol_c), "(c): el prompt final debe reflejar la jerarquía física heredada")
        print("(c) OK -> jerarquía física de overrides se propagó a la propuesta que no la traía:", sol_c["nivel_abdominal"], sol_c["proporcion"], sol_c["genero"])

        # --- (c2) si la propuesta SÍ trae su propio valor, no se debe pisar con el override ---
        async def con_jerarquia_propia(**kwargs):
            payload = {
                "mensaje": "Va",
                "propuestas": [{
                    "titulo": "Con su propia jerarquía",
                    "razon": "el LLM ya decidió un nivel distinto",
                    "solicitud": {
                        "sujeto": "Atleta",
                        "categoria": "fotorealismo_retrato_producto",
                        "nivel_abdominal": "A2",
                    },
                }],
                "sugerencias": [],
            }
            return _fake_resp(payload)

        creativo._client.aio.models.generate_content = AsyncMock(side_effect=con_jerarquia_propia)
        r4 = await client.post("/v2/creativo", json={
            "mensajes": [{"rol": "user", "contenido": "otra variante"}],
            "overrides": {"nivel_abdominal": "A6"},
            "categoria": "fotorealismo_retrato_producto",
        })
        check(r4.status_code == 200, f"(c2): debe responder 200, dio {r4.status_code}")
        sol_c2 = r4.json()["propuestas"][0]["solicitud"]
        check(sol_c2["nivel_abdominal"] == "A2", f"(c2): si la propuesta ya trae su propio nivel_abdominal, el override NO debe pisarlo, dio: {sol_c2.get('nivel_abdominal')}")
        print("(c2) OK -> override no pisa un valor que la propuesta ya traía:", sol_c2["nivel_abdominal"])


def test_creativo():
    """Entry point compatible con pytest."""
    asyncio.run(_run())
    assert not fallos, "Fallos:\n" + "\n".join(f"  - {f}" for f in fallos)


if __name__ == "__main__":
    asyncio.run(_run())
    print("\n" + "=" * 60)
    if fallos:
        print(f"❌ FALLARON {len(fallos)} TESTS:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("✅ TODOS LOS TESTS DE CREATIVIDAD PASARON")
        sys.exit(0)
