FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY ai /srv/ai
COPY backend /srv/backend
RUN python -m pip install '/srv/ai[chat,speech]' /srv/backend \
    && groupadd --gid 10001 shopping \
    && useradd --uid 10001 --gid shopping --no-create-home shopping
WORKDIR /srv/backend
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
