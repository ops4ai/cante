FROM python:3.12-slim
WORKDIR /app
RUN pip install uv --no-cache-dir
COPY core/ /app/core/
RUN cd /app/core && uv pip install --system .
COPY services/ingress/ /app/services/ingress/
RUN cd /app/services/ingress && uv pip install --system --no-deps . 2>/dev/null || true
CMD ["python", "-m", "services.ingress.main"]
