FROM python:3.11-slim

WORKDIR /app

# Invalidar caché de build (cambiar fecha para forzar rebuild)
ENV BUILD_DATE=2026-08-27-0509

# Crear archivo timestamp para forzar invalidación de caché
RUN echo "2026-08-27-0509" > /tmp/build_timestamp

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código (forzar no-cache con timestamp)
COPY . .
RUN cat /tmp/build_timestamp

# LIMPIAR CACHÉ PYTHON PARA EVITAR USAR CÓDIGO VIEJO
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
RUN find . -name "*.pyc" -delete 2>/dev/null || true

# Puerto
EXPOSE 8000

# Comando de inicio
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
