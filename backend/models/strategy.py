import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.driver import Driver
    from backend.models.race import Session


class StrategyPrediction(Base):
    __tablename__ = "strategy_predictions"
    # Backs alert_service._latest_undercut_scores: filters by session_id,
    # groups by driver_id, aggregates MAX(predicted_at) — then re-joins on
    # (driver_id, predicted_at) to fetch the winning row. DESC matches the
    # "most recent prediction" access pattern; the single-column indexes
    # below on session_id/driver_id don't help SQLAlchemy plan the composite
    # filter+group+max as one index scan.
    __table_args__ = (
        Index(
            "ix_strategy_predictions_session_driver_predicted_at",
            "session_id",
            "driver_id",
            text("predicted_at DESC"),
        ),
        # Backs strategy_service.get_strategy_prediction_history: filters by
        # session_id + driver_id, orders by lap_number ASC — a distinct access
        # pattern from the DESC-by-predicted_at index above (progression-over-
        # time view vs. "most recent" lookup), so it needs its own index rather
        # than reusing that one.
        Index(
            "ix_strategy_predictions_session_driver_lap_number",
            "session_id",
            "driver_id",
            "lap_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No standalone index=True here: ix_strategy_predictions_session_driver_predicted_at
    # above already leads with session_id, so a separate single-column index
    # on it would be a pure duplicate.
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False, index=True
    )
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Nullable: added 2026-08-26 (Day 42), after this table already had rows
    # from earlier days — those existing rows have no way to backfill the lap
    # they were predicted for, so they stay NULL permanently. Populated going
    # forward by prediction_worker._persist_and_publish from the same
    # lap-completion context that drives the rest of the prediction. See
    # strategy_service.get_strategy_prediction_history for the NULLS LAST
    # ordering this requires.
    lap_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pit_predictor's own quick classifier-driven estimate — lap_number +
    # predicted_life_remaining (a genuine laps-until-degradation-threshold
    # count; was previously the raw tire_deg lap-time-delta prediction, a
    # bug fixed as part of the core-feature-rebuild's Checkpoint 4 — see
    # CLAUDE.md's now-closed "StrategyPrediction.optimal_pit_lap" Deferred
    # Wiring entry). Deliberately kept alongside recommended_pit_lap below,
    # not replaced by it — this is a different, complementary mechanism
    # (pit_predictor's degradation-threshold read vs. tire_deg's full
    # counterfactual stint-1/stint-2 search), not a duplicate.
    optimal_pit_lap: Mapped[int] = mapped_column(Integer, nullable=False)
    pit_probability: Mapped[float] = mapped_column(Float, nullable=False)
    undercut_score: Mapped[float] = mapped_column(Float, nullable=False)
    overcut_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Laps remaining before predicted tyre-degradation crosses tire_deg_model.
    # DEGRADATION_THRESHOLD_SECONDS (tire_deg_model.predict_life_remaining_
    # batch's own output) — was previously the raw tire_deg lap-time-delta
    # prediction itself (a small, sometimes-negative float with no
    # "remaining laps" meaning at all despite the column name), a bug fixed
    # alongside optimal_pit_lap's own fix above (Checkpoint 4 — see
    # CLAUDE.md's now-closed "StrategyPrediction.tire_life_remaining"
    # Deferred Wiring entry).
    tire_life_remaining: Mapped[float] = mapped_column(Float, nullable=False)
    # P(the recommended_pit_lap candidate below is still optimal under
    # noise) — build_pit_recommendation's vectorized Monte Carlo. Was
    # previously always hardcoded 0.0 (never computed at all); real values
    # only exist from Checkpoint 4 onward — a NULL-free but meaningless 0.0
    # on any row predating this fix, same "no way to distinguish real-zero
    # from never-computed" caveat lap_number's own pre-migration NULL rows
    # already carry for a different column.
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    # --- The 5 columns below are new as of the core-feature-rebuild's
    # Checkpoint 4 migration (20260904_add_pit_recommendation_fields_to_
    # strategy_predictions) — strategy_service.build_pit_recommendation's
    # own output (Checkpoint 2: batched tire_deg counterfactual search over
    # stint-1/stint-2 candidates) plus get_pit_window_with_explanation's
    # combined tire_deg+pit_predictor explanation (Checkpoint 3), persisted
    # here so the live/replay per-lap pipeline (prediction_worker.
    # _persist_and_publish) carries the SAME rich recommendation the
    # on-demand /pit-window REST endpoint already computes, not just
    # pit_predictor's older, cruder optimal_pit_lap/pit_probability above.
    # All nullable: a row predating this migration has none of them, and a
    # row where build_pit_recommendation's own candidate search returns
    # nothing (e.g. current_lap >= total_laps, race essentially over) or any
    # sub-computation raises degrades to NULL here too — see
    # prediction_worker._compute_recommendation_fields's own docstring.
    recommended_pit_lap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommended_compound: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Schemas.strategy_schema.PitRecommendationExplanation, serialized via
    # .model_dump(mode="json") — facts/narrative/tire_deg_shap/pit_predictor_shap.
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["Session"] = relationship(back_populates="strategy_predictions")
    driver: Mapped["Driver"] = relationship(back_populates="strategy_predictions")


class PitEvent(Base):
    __tablename__ = "pit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False, index=True
    )
    lap_number: Mapped[int] = mapped_column(Integer, nullable=False)
    compound_in: Mapped[str] = mapped_column(String(10), nullable=False)
    compound_out: Mapped[str] = mapped_column(String(10), nullable=False)
    pit_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    was_predicted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped["Session"] = relationship(back_populates="pit_events")
    driver: Mapped["Driver"] = relationship(back_populates="pit_events")
