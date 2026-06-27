FROM python:3.12-slim
WORKDIR /app
RUN pip install uv --no-cache-dir
COPY core/ /app/core/
RUN cd /app/core && uv pip install --system .
COPY services/api/ /app/services/api/
RUN cd /app && uv pip install --system fastapi uvicorn httpx structlog apscheduler redis 2>/dev/null; true
CMD ["python", "-m", "services.api.main"]
