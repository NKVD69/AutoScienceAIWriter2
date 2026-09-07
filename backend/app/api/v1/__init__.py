"""Routeurs de l'API v1. Les préfixes viennent de `settings.api_prefix`."""

from fastapi import APIRouter

from app.api.v1 import audit, health

router = APIRouter()
router.include_router(health.router)
router.include_router(audit.router)

__all__ = ["router"]
