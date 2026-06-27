"""Tiny mock backend for Barber/Trainer preset tools."""
from fastapi import FastAPI

app = FastAPI(title="Mock Backend", version="0.1.0")

@app.get("/healthz")
async def health():
    return {"status": "ok"}

@app.get("/availability")
async def availability(date: str):
    return {"slots": [f"{date}T09:00", f"{date}T10:00", f"{date}T11:00"]}

@app.post("/appointments")
async def book(data: dict):
    return {"id": "apt-123", "status": "booked", **data}

@app.delete("/appointments/{apt_id}")
async def cancel(apt_id: str):
    return {"id": apt_id, "status": "cancelled"}

@app.get("/schedule")
async def schedule(team: str = ""):
    return {"games": [{"date": "2026-07-01", "time": "10:00", "location": "Field A"}]}

@app.post("/absences")
async def report_absence(data: dict):
    return {"id": "abs-456", "status": "reported"}

@app.post("/messages")
async def message_parent(data: dict):
    return {"sent": True, "to": data.get("parent_phone", "")}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
