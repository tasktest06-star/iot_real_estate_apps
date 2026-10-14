# UNVERIFIED: no Docker on the build machine, so this image has never been
# built. Written to be correct; flagged rather than presented as tested.
#
# 3.9-slim matches the authoring runtime. The backend's only deps are FastAPI,
# uvicorn, httpx and pytest; detection/ and simulator/ are stdlib-only by
# design (contracts/INTERFACES.md §5), so there is nothing to compile and no
# build stage is needed.
FROM python:3.9-slim

WORKDIR /app

# Dependencies first so the layer caches independently of source edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY detection/ ./detection/
COPY simulator/ ./simulator/
COPY dashboard/ ./dashboard/
COPY contracts/ ./contracts/

# Run unprivileged. The DB lives on a mounted volume, not in the image layer.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data && chown -R app:app /data /app
USER app

ENV LEAK_DB_PATH=/data/leak.db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz')"

CMD ["python3", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
