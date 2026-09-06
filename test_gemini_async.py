"""
test_gemini_async.py — Verifica PR-2 (Gemini async + caché de visión) sin
red real: mockea gemini_client.aio.models.generate_content con latencia
simulada y confirma que:

  (B1) el event loop NO se bloquea: /health responde en <100ms mientras
       una visión "lenta" (300ms simulados) está en curso — antes de este
       fix, la llamada sync a Gemini + time.sleep del retry bloqueaban el
       worker completo de uvicorn.
  (B1) el reintento ante 429 usa asyncio.sleep (no bloqueante) y se
       recupera en el segundo intento.
  (B2) la caché de visión evita una segunda llamada a Gemini para la
       MISMA imagen, y SÍ llama de nuevo para una imagen distinta.
  (B5) la respuesta incluye request_id y timings.vision_ms/engine_ms.

No requiere GEMINI_API_KEY: gemini_client se reemplaza por un MagicMock.
"""
import asyncio
import io
import json
import sys

import httpx
from PIL import Image
from unittest.mock import AsyncMock, MagicMock

import main as m

fallos = []


def check(cond, msg):
    if not cond:
        fallos.append(msg)


VISION_DICT = {
    "sujeto_detectado": "Retrato de estudio",
    "categoria_sugerida": "fotorealismo_retrato_producto",
    "elementos_notables": [],
    "confianza_baja": [],
}


def _png_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), color).save(buf, format="PNG")
    return buf.getvalue()


async def _run():
    import time

    m.gemini_client = MagicMock()
    calls = {"n": 0}

    async def slow_generate_content(**kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.3)
        resp = MagicMock()
        resp.text = json.dumps(VISION_DICT)
        return resp

    m.gemini_client.aio.models.generate_content = AsyncMock(side_effect=slow_generate_content)

    transport = httpx.ASGITransport(app=m.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        png1 = _png_bytes((10, 20, 30))
        data = {"categoria": "fotorealismo_retrato_producto"}

        async def hit_health():
            await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            r = await client.get("/health")
            return (time.perf_counter() - t0) * 1000, r

        gen_task = asyncio.create_task(
            client.post("/v2/generate", files={"image": ("t.png", png1, "image/png")}, data=data)
        )
        health_task = asyncio.create_task(hit_health())
        (health_ms, health_r), gen_r = await asyncio.gather(health_task, gen_task)

        check(health_r.status_code == 200, "B1: /health debe responder 200")
        check(health_ms < 100, f"B1: /health tardó {health_ms:.1f}ms (>100ms) mientras la visión estaba en curso — event loop bloqueado")
        check(gen_r.status_code == 200, f"B1: /v2/generate debe responder 200, dio {gen_r.status_code}: {gen_r.text}")
        body = gen_r.json()
        check("request_id" in body, "B5: la respuesta debe incluir request_id")
        check("timings" in body and "vision_ms" in body.get("timings", {}), "B5: la respuesta debe incluir timings.vision_ms")
        check(body.get("timings", {}).get("vision_ms", 0) >= 250, "B5: vision_ms debe reflejar la latencia real simulada (~300ms)")

        # --- B2: cache hit en la misma imagen ---
        calls_antes = calls["n"]
        r2 = await client.post("/v2/generate", files={"image": ("t.png", png1, "image/png")}, data=data)
        check(r2.status_code == 200, "B2: segunda llamada con la misma imagen debe responder 200")
        check(calls["n"] == calls_antes, f"B2: caché de visión debería evitar una nueva llamada a Gemini (antes={calls_antes}, después={calls['n']})")

        # --- B2b: imagen distinta SÍ llama a Gemini ---
        png2 = _png_bytes((200, 200, 200))
        calls_antes2 = calls["n"]
        r3 = await client.post("/v2/generate", files={"image": ("t2.png", png2, "image/png")}, data=data)
        check(r3.status_code == 200, "B2b: imagen distinta debe responder 200")
        check(calls["n"] == calls_antes2 + 1, "B2b: una imagen distinta SÍ debe llamar a Gemini de nuevo (la caché no debe confundir imágenes distintas)")

        # --- Retry async ante 429 ---
        import requests
        from google.genai import errors as genai_errors

        intentos = {"n": 0}

        async def flaky(**kwargs):
            intentos["n"] += 1
            if intentos["n"] == 1:
                resp_429 = requests.Response()
                resp_429.status_code = 429
                resp_429._content = b'{"error": {"message": "rate limited"}}'
                raise genai_errors.APIError(429, resp_429)
            resp = MagicMock()
            resp.text = json.dumps(VISION_DICT)
            return resp

        m.gemini_client.aio.models.generate_content = AsyncMock(side_effect=flaky)
        png3 = _png_bytes((5, 5, 5))
        r4 = await client.post("/v2/generate", files={"image": ("t3.png", png3, "image/png")}, data=data)
        check(r4.status_code == 200, f"Retry: debe recuperarse del 429 y responder 200, dio {r4.status_code}")
        check(intentos["n"] == 2, f"Retry: debe haber reintentado exactamente una vez (429 + éxito), hubo {intentos['n']} intentos")


def test_gemini_async():
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
        print("✅ TODOS LOS TESTS DE GEMINI ASYNC PASARON")
        sys.exit(0)
