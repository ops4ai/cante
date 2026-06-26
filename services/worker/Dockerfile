FROM python:3.12-slim
WORKDIR /app
RUN pip install uv --no-cache-dir
COPY core/ /app/core/
RUN cd /app/core && uv pip install --system .
COPY services/worker/ /app/services/worker/
RUN cd /app/services/worker && uv pip install --system --no-deps . 2>/dev/null || true
CMD ["python", "-m", "services.worker.main"]
