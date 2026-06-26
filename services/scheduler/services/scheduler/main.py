"""Cante scheduler service."""
import structlog
from fastapi import FastAPI

logger = structlog.get_logger(__name__)
app = FastAPI(title=f"Cante {svc}", version="0.1.0")

@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "scheduler"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
