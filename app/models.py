from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Alignment(str, Enum):
    on_roadmap = "on_roadmap"
    partial = "partial"
    not_on_roadmap = "not_on_roadmap"


class RoadmapItem(Base):
    __tablename__ = "roadmap_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(80), default="Planned")
    quarter: Mapped[str] = mapped_column(String(80), default="")
    priority: Mapped[str] = mapped_column(String(80), default="Medium")
    source: Mapped[str] = mapped_column(String(30), default="manual")
    notion_page_id: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CustomerCheck(Base):
    __tablename__ = "customer_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(240), default="")
    contact: Mapped[str] = mapped_column(String(240), default="")
    request_text: Mapped[str] = mapped_column(Text)
    request_theme: Mapped[str] = mapped_column(String(160), default="")
    deal_value: Mapped[int] = mapped_column(Integer, default=0)
    deal_stage: Mapped[str] = mapped_column(String(80), default="")
    deal_blocker: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    alignment: Mapped[Alignment] = mapped_column(SqlEnum(Alignment))
    matched_roadmap_item_id: Mapped[int | None] = mapped_column(ForeignKey("roadmap_items.id"))
    confidence: Mapped[int] = mapped_column(Integer)
    reasoning: Mapped[str] = mapped_column(Text)
    customer_note: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    product_shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    matched_roadmap_item: Mapped[RoadmapItem | None] = relationship()


class DashboardInsight(Base):
    __tablename__ = "dashboard_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    headline: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
