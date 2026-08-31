# Matches the interpreter the pinned lockfile was verified against.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/resolveflow

WORKDIR /opt/resolveflow

# Dependencies land in their own layer so editing source does not reinstall them.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# README.md is referenced by pyproject's `readme` field, so it must be present
# before the package install below.
COPY pyproject.toml README.md ./
COPY .streamlit ./.streamlit
COPY app ./app
COPY scripts ./scripts
COPY sample_data ./sample_data
COPY tests ./tests

# --no-deps: requirements.txt already pins the full resolved set. Re-resolving
# here would defeat the lockfile.
RUN pip install --no-deps -e .

# SQLite and Qdrant are written at runtime. Keep the directory writable by the
# unprivileged user and mountable so an operator's knowledge survives a restart.
RUN mkdir -p /opt/resolveflow/app/data \
 && useradd --create-home --uid 10001 resolveflow \
 && chown -R resolveflow:resolveflow /opt/resolveflow
USER resolveflow

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health',timeout=4).read().strip()==b'ok' else 1)"

CMD ["streamlit", "run", "app/dashboard.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
