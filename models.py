"""ORM-модели модуля Logistics (схема ``logistics.*``)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class Shipment(Base):
    """Отгрузка/доставка: получатель, адрес, перевозчик, статус, ссылка на сделку."""

    __tablename__ = "shipment"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    customer: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(255), default="", server_default="")
    carrier: Mapped[str] = mapped_column(String(128), default="", server_default="")
    status: Mapped[str] = mapped_column(String(32), default="planned", server_default="planned")
    deal_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
