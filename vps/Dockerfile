FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py configure.py ./
COPY shared ./shared
COPY pilot ./pilot

RUN useradd --system --uid 10001 --home-dir /app rayzen \
    && mkdir -p /app/data \
    && chown -R rayzen:rayzen /app/data

USER rayzen
VOLUME ["/app/data"]

CMD ["python", "-u", "app.py"]
