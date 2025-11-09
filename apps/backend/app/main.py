from app.api.routers.health import router as health_router
from app.api.routers.plan import router as plan_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="Travxy API", version="0.4.0")

    # Enable frontend access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, tags=["health"])
    app.include_router(plan_router, tags=["plan"])
    return app


app = create_app()
