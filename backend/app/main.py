"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="Local PDF Pipeline")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
