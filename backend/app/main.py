import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Portfolio Builder API",
    description="Arma carteras de inversión (acciones EEUU + ADRs argentinos) "
                "con scoring fundamental/técnico + optimización Black-Litterman/Markowitz.",
    version="0.1.0",
)

# Permitir el frontend local (file:// o localhost en cualquier puerto)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # uso local; restringir si esto se deploya
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def root():
    return {"status": "ok", "docs": "/docs"}
