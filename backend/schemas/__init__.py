from backend.schemas.alert_schema import AlertCreate, AlertResponse, AlertType
from backend.schemas.common import APIVersion, ErrorResponse, HealthResponse, PaginatedResponse
from backend.schemas.driver_schema import (
    DriverAnalysisResponse,
    DriverContractResponse,
    DriverListResponse,
    DriverResponse,
    TeamResponse,
)
from backend.schemas.race_schema import (
    CircuitResponse,
    RaceListResponse,
    RaceResponse,
    SessionResponse,
)
from backend.schemas.simulate_schema import (
    SimulatedRaceOutcome,
    SimulateStrategyRequest,
    SimulateStrategyResponse,
    SimulateTaskAccepted,
    SimulateTaskStatusResponse,
)
from backend.schemas.strategy_schema import (
    CompetitorStrategyEntry,
    FeatureContributionResponse,
    PitWindowResponse,
    StrategyComparisonEntry,
    StrategyComparisonResponse,
    StrategyOverviewResponse,
    StrategyPredictionResponse,
    UndercutThreatResponse,
)
from backend.schemas.telemetry_schema import (
    DriverGap,
    LapCompletedEvent,
    LapDataCreate,
    LapDataResponse,
    LapHistoryBucket,
    LiveTelemetryEvent,
    LiveTelemetryResponse,
    SectorTimeResponse,
    SessionGapsResponse,
    TelemetryStreamMessage,
    TireStintResponse,
)
from backend.schemas.user_schema import (
    FCMTokenUpdate,
    LoginResponse,
    RefreshTokenRequest,
    SubscriptionCreate,
    SubscriptionResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

__all__ = [
    # alert
    "AlertCreate",
    "AlertResponse",
    "AlertType",
    # common
    "APIVersion",
    "ErrorResponse",
    "HealthResponse",
    "PaginatedResponse",
    # driver
    "DriverAnalysisResponse",
    "DriverContractResponse",
    "DriverListResponse",
    "DriverResponse",
    "TeamResponse",
    # race
    "CircuitResponse",
    "RaceListResponse",
    "RaceResponse",
    "SessionResponse",
    # simulate
    "SimulatedRaceOutcome",
    "SimulateStrategyRequest",
    "SimulateStrategyResponse",
    "SimulateTaskAccepted",
    "SimulateTaskStatusResponse",
    # strategy
    "CompetitorStrategyEntry",
    "FeatureContributionResponse",
    "PitWindowResponse",
    "StrategyComparisonEntry",
    "StrategyComparisonResponse",
    "StrategyOverviewResponse",
    "StrategyPredictionResponse",
    "UndercutThreatResponse",
    # telemetry
    "DriverGap",
    "LapCompletedEvent",
    "LapDataCreate",
    "LapDataResponse",
    "LapHistoryBucket",
    "LiveTelemetryEvent",
    "LiveTelemetryResponse",
    "SectorTimeResponse",
    "SessionGapsResponse",
    "TelemetryStreamMessage",
    "TireStintResponse",
    # user
    "FCMTokenUpdate",
    "LoginResponse",
    "RefreshTokenRequest",
    "SubscriptionCreate",
    "SubscriptionResponse",
    "TokenResponse",
    "UserCreate",
    "UserLogin",
    "UserResponse",
]
