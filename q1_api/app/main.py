"""Securities account identity verification: API entry point.

Keep the ASGI entry point importable as `app.main:app` from q1_api/.
Organize application modules within this package. The repository README
defines fixed paths and names in section 2.
"""
from fastapi import FastAPI

app = FastAPI(title="Securities Account Identity Verification")


@app.get("/api/health")
def health():
    return {"status": "ok"}
