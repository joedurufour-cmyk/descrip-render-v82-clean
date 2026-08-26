# DescripRender V8.2 — Backend Limpio

Motor determinístico de prompts para Midjourney V8.2 / Niji 7.

## Archivos principales

- `mj_engine.py` — Motor determinístico con validación Pydantic
- `gemini_orquestador.py` — Capa de conexión con Gemini API
- `main.py` — FastAPI endpoints (legacy + V2)
- `models.py` — Modelos Pydantic legacy
- `prompt_builder.py` — Constructor legacy

## Deploy en Render

1. Crear nuevo Web Service en Render
2. Conectar repo `descrip-render-v82-clean`
3. Elegir Docker como entorno
4. Deploy automático desde main

## Variables de entorno

- `GEMINI_API_KEY` — API key de Google AI Studio
- `GEMINI_MODEL` — `gemini-3.7-flash` (default)

## Endpoints

- `GET /health` — Estado del servicio
- `GET /debug` — Información de debug
- `POST /generate` — Endpoint legacy (compatible APK)
- `POST /v2/generate` — Endpoint V2 con motor determinístico
- `POST /v2/vision` — Análisis de imagen
- `GET /v2/perfiles` — Lista de perfiles estéticos
