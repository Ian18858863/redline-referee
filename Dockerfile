# Redline Referee: the interface, the replayed demo cases and live reviews in one small container.
#   docker build -t redline-referee .
#   docker run -p 8787:8787 -e CLOCKED_API_KEY=... redline-referee
# Without CLOCKED_API_KEY it serves the recorded demo only. Limits for a public instance:
#   LIVE_RUNS_PER_HOUR (per address, default 12), LIVE_RUNS_PER_DAY (all addresses, default 100),
#   CLOCKED_CREDIT_CAP (per review, default 1500), RUN_TTL_HOURS (default 24).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8787 TRUST_FORWARDED_FOR=1
WORKDIR /app
RUN pip install --no-cache-dir "clocked-sdk==0.7.0" "pymupdf>=1.24" "reportlab>=4.0" "fastapi>=0.110" "uvicorn>=0.29" "python-multipart>=0.0.9"
COPY referee ./referee
COPY web ./web
COPY docs/cases ./docs/cases
COPY data/playbook.json ./data/playbook.json
RUN useradd --create-home app && mkdir -p out && chown -R app /app
USER app
EXPOSE 8787
CMD ["sh", "-c", "python -m uvicorn referee.server:app --host 0.0.0.0 --port ${PORT}"]
