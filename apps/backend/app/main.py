from app.api.routers.health import router as health_router
from app.api.routers.plan import router as plan_router
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Travxy API", version="0.4.0")
    app.include_router(health_router, tags=["health"])
    app.include_router(plan_router, tags=["plan"])
    return app


app = create_app()
