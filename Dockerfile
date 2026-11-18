FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir pillow tzdata
WORKDIR /app
COPY bot.py card.py forecast.py render.py series.py srs.py ./
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
CMD ["python3", "bot.py"]
