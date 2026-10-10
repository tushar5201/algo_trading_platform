FROM python:3.11-slim
WORKDIR /app

# System deps for cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libssl-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1001 engine && chown -R engine /app
USER engine

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4).status == 200 else 1)"

CMD ["python", "main.py"]