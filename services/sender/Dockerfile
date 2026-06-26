FROM python:3.12-slim
WORKDIR /app
RUN pip install uv --no-cache-dir
COPY core/ /app/core/
RUN cd /app/core && uv pip install --system .
COPY services/sender/ /app/services/sender/
RUN cd /app/services/sender && uv pip install --system --no-deps . 2>/dev/null || true
CMD ["python", "-m", "services.sender.main"]
