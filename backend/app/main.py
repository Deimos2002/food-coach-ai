import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_body import router as body_router
from app.api.routes_coach import router as coach_router
from app.api.routes_meals import router as meals_router
from app.api.routes_privacy import router as privacy_router
from app.api.routes_privacy import stale_generation_handler
from app.api.routes_products import router as products_router
from app.api.routes_saved import router as saved_router
from app.api.routes_templates import router as templates_router
from app.api.routes_week import router as week_router
from app.config import Settings
from app.db.engine import get_engine
from app.services.privacy import StaleGeneration, check_ledger_volume
from app.web_static import mount_static


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.require_ledger_volume:
        check_ledger_volume(settings.deletion_ledger_path)
    app = FastAPI()
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.get("/health", response_model=None)
    def health(deep: bool = False) -> dict[str, str] | JSONResponse:
        if not deep:
            return {"status": "ok"}
        # Resolve the engine dependency here (honouring overrides) so a failure to even
        # build the engine is reported as 503, not a 500.
        engine_factory = app.dependency_overrides.get(get_engine, get_engine)
        try:
            with engine_factory().connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except Exception:  # noqa: BLE001 - spec: 503 on any failure
            return JSONResponse(status_code=503, content={"status": "error", "db": "down"})
        return {"status": "ok", "db": "ok"}

    for router in (
        products_router, meals_router, body_router, saved_router,
        privacy_router, week_router, coach_router, templates_router,
    ):
        app.include_router(router, prefix=settings.api_prefix)
    if settings.static_dir is not None:
        mount_static(app, settings.static_dir, settings.api_prefix)
    app.add_exception_handler(StaleGeneration, stale_generation_handler)
    return app
