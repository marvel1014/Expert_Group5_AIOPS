"""AIOps 机房智能告警诊断平台 - FastAPI 入口。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.db import init_db, session_scope
from app.maintenance import maintenance_loop
from app.topology.service import load_topology

logger = logging.getLogger(__name__)

maintenance_task: asyncio.Task[None] | None = None


def _startup_tasks() -> None:
    """可选初始化：载入场景库、回收上次进程残留的诊断任务。"""
    try:
        from app.evaluation.scenarios import load_scenarios_from_dir

        with session_scope() as db:
            count = load_scenarios_from_dir(db)
        logger.info("loaded %s scenarios", count)
    except Exception:
        logger.exception("scenario seed skipped/failed")

    try:
        from app.diagnosis.service import recover_stale_diagnoses

        with session_scope() as db:
            count = recover_stale_diagnoses(db)
        if count:
            logger.info("recovered %s stale diagnoses", count)
    except Exception:
        logger.exception("diagnosis recovery skipped/failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global maintenance_task
    setup_logging()
    init_db()
    load_topology(force=True)
    _startup_tasks()
    maintenance_task = asyncio.create_task(maintenance_loop())
    try:
        yield
    finally:
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        maintenance_task = None


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="机房智能告警诊断平台：降噪、拓扑关联、根因分析与报告生成",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    from app.api import dashboard, incidents, simulate
    from app.dedup.router import router as dedup_router
    from app.diagnosis.router import router as diagnosis_router
    from app.evaluation.router import router as evaluation_router
    from app.ingestion.router import router as ingestion_router
    from app.reporting.router import router as reporting_router
    from app.topology.router import router as topology_router

    app.include_router(ingestion_router)
    app.include_router(dedup_router)
    app.include_router(topology_router)
    app.include_router(incidents.router)
    app.include_router(dashboard.router)
    app.include_router(simulate.router)
    app.include_router(diagnosis_router)
    app.include_router(reporting_router)
    app.include_router(evaluation_router)

    @app.get("/healthz")
    def healthz() -> dict:
        from sqlalchemy import text

        from app.db import get_engine

        db_status = "ok"
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - 仅异常环境
            db_status = f"error: {exc}"
        return {
            "status": "ok" if db_status == "ok" else "degraded",
            "version": settings.version,
            "database": db_status,
            "llm_mode": settings.effective_llm_mode,
            "llm_configured": settings.llm_configured,
        }

    dist = settings.path(settings.frontend_dist)
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    else:

        @app.get("/")
        def root() -> JSONResponse:
            return JSONResponse(
                {
                    "name": settings.app_name,
                    "version": settings.version,
                    "message": "前端尚未构建，请运行 make build；接口文档见 /docs",
                }
            )

    return app


app = create_app()
