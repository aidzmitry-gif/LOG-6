"""ORM-модели модуля Logistics (схема ``logistics.*``)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class Shipment(Base):
    """Доставка по РБ/РФ: получатель, маршрут, перевозчик, груз, стоимость, статус.

    ``status`` ведёт доставку по воронке (см. ``DELIVERY_STAGES``): из ``planned``
    (создаётся по событию ``sales.document.posted``) до ``delivered``. Переход в
    ``delivered`` публикует ``logistics.shipment.delivered`` → закрытие сделки
    (logistics → sales). Стоимость доставки (``amount``) — в BYN.
    """

    __tablename__ = "shipment"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64), default="", server_default="")
    customer: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(255), default="", server_default="")
    route_from: Mapped[str] = mapped_column(String(128), default="", server_default="")
    route_to: Mapped[str] = mapped_column(String(128), default="", server_default="")
    carrier: Mapped[str] = mapped_column(String(128), default="", server_default="")
    cargo: Mapped[str] = mapped_column(String(255), default="", server_default="")
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), server_default="0")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), server_default="0")
    priority: Mapped[str] = mapped_column(String(32), default="Средний", server_default="Средний")
    owner: Mapped[str] = mapped_column(String(128), default="", server_default="")
    status: Mapped[str] = mapped_column(String(32), default="planned", server_default="planned")
    eta: Mapped[str | None] = mapped_column(String(32))
    tracking_no: Mapped[str] = mapped_column(String(64), default="", server_default="")
    deal_id: Mapped[int | None] = mapped_column(Integer)
    insight: Mapped[str] = mapped_column(String(400), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImportShipment(Base):
    """Импорт из Китая: фабрика/поставщик, контейнер, маршрут, Incoterms, таможня.

    ``stage`` ведёт поставку по цепочке (см. ``IMPORT_STAGES``): фабрика →
    консолидация → плечо → таможня → склад. Переход в ``customs``/``warehouse``
    публикует информационные события для панели владельца. Себестоимость доставки
    (``amount``) — в BYN; физический приход на склад остаётся за procurement (QC),
    поэтому logistics здесь приход не делает (нет двойного учёта).
    """

    __tablename__ = "import_shipment"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64), default="", server_default="")
    supplier: Mapped[str] = mapped_column(String(255))
    flag: Mapped[str] = mapped_column(String(8), default="🇨🇳", server_default="🇨🇳")
    container_no: Mapped[str] = mapped_column(String(64), default="", server_default="")
    route: Mapped[str] = mapped_column(String(255), default="", server_default="")
    incoterms: Mapped[str] = mapped_column(String(16), default="FOB", server_default="FOB")
    mode: Mapped[str] = mapped_column(String(32), default="море", server_default="море")
    cargo: Mapped[str] = mapped_column(String(255), default="", server_default="")
    qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), server_default="0")
    priority: Mapped[str] = mapped_column(String(32), default="Средний", server_default="Средний")
    owner: Mapped[str] = mapped_column(String(128), default="", server_default="")
    stage: Mapped[str] = mapped_column(String(32), default="factory", server_default="factory")
    customs_status: Mapped[str] = mapped_column(String(64), default="", server_default="")
    eta: Mapped[str | None] = mapped_column(String(32))
    po_ref: Mapped[str] = mapped_column(String(64), default="", server_default="")
    insight: Mapped[str] = mapped_column(String(400), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Carrier(Base):
    """Перевозчик/подрядчик: тип, плечо, способ интеграции, метрики надёжности.

    Метрики (``on_time_pct``, ``avg_days``) питают дашборд логистики (log-8).
    Способ интеграции (``integration``: api/csv/edi/manual) — под коннекторы (log-5);
    в прототипе обмен ручной/файловый, реальные API — Итерация 1+.
    """

    __tablename__ = "carrier"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), default="РБ", server_default="РБ")
    mode: Mapped[str] = mapped_column(String(32), default="авто", server_default="авто")
    contact: Mapped[str] = mapped_column(String(255), default="", server_default="")
    integration: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    on_time_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    shipments_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
