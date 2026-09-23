"""Routeurs de l'API v1. Les préfixes viennent de `settings.api_prefix`."""

from fastapi import APIRouter

from app.api.v1 import audit, code, export, health, plan, projects, sections, sources, system

router = APIRouter()
router.include_router(health.router)
router.include_router(system.router)
router.include_router(projects.router)
router.include_router(sources.router)
router.include_router(plan.router)
router.include_router(sections.router)
router.include_router(export.router)
router.include_router(audit.router)
router.include_router(code.router)

__all__ = ["router"]
