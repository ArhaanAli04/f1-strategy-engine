from fastapi import APIRouter

from backend.apis.v1.auth import router as auth_router
from backend.apis.v1.drivers import router as drivers_router
from backend.apis.v1.races import router as races_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(races_router)
api_v1_router.include_router(drivers_router)
