FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 payflex

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

USER payflex
EXPOSE 8000

HEALTHCHECK --interval=3s --timeout=3s --start-period=3s --retries=20 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# Journal d'accès uvicorn désactivé : l'application produit son propre journal (PAN masqués).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
