from fastapi import APIRouter

from backend.apis.v1.alerts import router as alerts_router
from backend.apis.v1.auth import router as auth_router
from backend.apis.v1.drivers import router as drivers_router
from backend.apis.v1.races import router as races_router
from backend.apis.v1.strategy import router as strategy_router
from backend.apis.v1.telemetry import router as telemetry_router
from backend.apis.v1.telemetry import ws_router as telemetry_ws_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(races_router)
api_v1_router.include_router(drivers_router)
api_v1_router.include_router(telemetry_router)
api_v1_router.include_router(telemetry_ws_router)
api_v1_router.include_router(strategy_router)
api_v1_router.include_router(alerts_router)
