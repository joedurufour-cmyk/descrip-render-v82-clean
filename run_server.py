"""Servidor local de desarrollo.

Requiere un archivo .env en la raíz del proyecto (ignorado por git) con:
    GEMINI_API_KEY=tu_api_key_de_google_ai_studio
    GEMINI_MODEL=gemini-3.7-flash   # opcional, este es el default

main.py ya carga ese .env vía load_dotenv(), así que basta con crearlo.
"""
import uvicorn
from main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
