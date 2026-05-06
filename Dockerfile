FROM ghcr.io/astral-sh/uv:python3.11-alpine

ADD . /app

WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "120", "--log-level", "info", "--capture-output", "--access-logfile", "-", "--error-logfile", "-", "main:app"]
