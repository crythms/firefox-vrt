"""SQLAlchemy models: Capture, CaptureTask, Comparison, Result."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# --- Status constants ---

CAPTURE_PENDING = "pending"
CAPTURE_FETCHING = "fetching"
CAPTURE_READY = "ready"
CAPTURE_FAILED = "failed"

TASK_PENDING = "pending"
TASK_FETCHING = "fetching"
TASK_READY = "ready"
TASK_FAILED = "failed"

COMPARISON_PENDING = "pending"
COMPARISON_DIFFING = "diffing"
COMPARISON_READY = "ready"
COMPARISON_FAILED = "failed"

# Result statuses align with diff.STATUS_* plus pairing-specific outcomes.
RESULT_IDENTICAL = "identical"
RESULT_DIFFERS = "differs"
RESULT_KNOWN_NOISE = "known-noise"
RESULT_ORPHAN = "orphan"
RESULT_SIZE_MISMATCH = "size-mismatch"

TRIAGE_UNTRIAGED = "untriaged"
TRIAGE_EXPECTED = "expected"
TRIAGE_REGRESSION = "regression"
TRIAGE_NEEDS_INVESTIGATION = "needs-investigation"
TRIAGE_VALUES = {
    TRIAGE_UNTRIAGED,
    TRIAGE_EXPECTED,
    TRIAGE_REGRESSION,
    TRIAGE_NEEDS_INVESTIGATION,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Capture(Base):
    __tablename__ = "captures"
    __table_args__ = (UniqueConstraint("revision", "project", name="uq_capture_rev_proj"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[str] = mapped_column(String, nullable=False)
    project: Mapped[str] = mapped_column(String, nullable=False)
    push_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[str] = mapped_column(String, nullable=False, default=_now)
    status: Mapped[str] = mapped_column(String, nullable=False, default=CAPTURE_PENDING)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    dir_path: Mapped[str] = mapped_column(String, nullable=False, default="")

    tasks: Mapped[list["CaptureTask"]] = relationship(
        back_populates="capture", cascade="all, delete-orphan"
    )


class CaptureTask(Base):
    __tablename__ = "capture_tasks"
    __table_args__ = (Index("idx_capture_tasks_cap", "capture_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capture_id: Mapped[int] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default=TASK_PENDING)
    artifact_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    downloaded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The MOZSCREENSHOTS_SETS value this task's CI job ran with (e.g.
    # "Toolbars,Tabs"), read from the Taskcluster task definition at fetch
    # time. None when unknown — the task def had no such env var, or the
    # capture predates set-tracking. Persisted so the provenance survives the
    # task definition's expiry (~4 weeks on try), letting us compare an old
    # capture against a new one and know they ran the same sets.
    mozscreenshots_sets: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    capture: Mapped[Capture] = relationship(back_populates="tasks")


class Comparison(Base):
    __tablename__ = "comparisons"
    __table_args__ = (
        UniqueConstraint(
            "base_capture_id", "candidate_capture_id", name="uq_comparison_pair"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_capture_id: Mapped[int] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    candidate_capture_id: Mapped[int] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_now)
    status: Mapped[str] = mapped_column(String, nullable=False, default=COMPARISON_PENDING)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.001)

    base_capture: Mapped[Capture] = relationship(foreign_keys=[base_capture_id])
    candidate_capture: Mapped[Capture] = relationship(
        foreign_keys=[candidate_capture_id]
    )
    results: Mapped[list["Result"]] = relationship(
        back_populates="comparison", cascade="all, delete-orphan"
    )


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        Index("idx_results_comparison", "comparison_id"),
        Index("idx_results_status", "comparison_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("comparisons.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String, nullable=False)
    combination: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    diff_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    changed_pixels: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    base_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    candidate_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    diff_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    triage: Mapped[str] = mapped_column(String, nullable=False, default=TRIAGE_UNTRIAGED)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    noise_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    comparison: Mapped[Comparison] = relationship(back_populates="results")


__all__ = [
    "CAPTURE_FAILED",
    "CAPTURE_FETCHING",
    "CAPTURE_PENDING",
    "CAPTURE_READY",
    "COMPARISON_DIFFING",
    "COMPARISON_FAILED",
    "COMPARISON_PENDING",
    "COMPARISON_READY",
    "Capture",
    "CaptureTask",
    "Comparison",
    "RESULT_DIFFERS",
    "RESULT_IDENTICAL",
    "RESULT_KNOWN_NOISE",
    "RESULT_ORPHAN",
    "RESULT_SIZE_MISMATCH",
    "Result",
    "TASK_FAILED",
    "TASK_FETCHING",
    "TASK_PENDING",
    "TASK_READY",
    "TRIAGE_EXPECTED",
    "TRIAGE_NEEDS_INVESTIGATION",
    "TRIAGE_REGRESSION",
    "TRIAGE_UNTRIAGED",
    "TRIAGE_VALUES",
]
