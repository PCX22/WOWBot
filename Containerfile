# Imagen del bot para el mismo servidor que la web (Podman o Docker).
#   podman build -t bot-hermandad .
#   podman run -d --name bot-hermandad --env-file .env -v bot-hermandad-datos:/data --restart unless-stopped bot-hermandad
FROM docker.io/library/python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
# Base de datos propia del bot (grupos, recetas, archivos subidos): en un volumen, para que sobreviva a las actualizaciones.
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
RUN mkdir /data && chown 1000:1000 /data
VOLUME /data
USER 1000
CMD ["python", "-m", "bot"]
