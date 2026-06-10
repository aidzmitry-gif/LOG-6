"""Pydantic-схемы модуля Logistics."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


# --- Доставка РБ/РФ -----------------------------------------------------------
class ShipmentCreate(BaseModel):
    customer: str
    number: str = ""
    address: str = ""
    route_from: str = ""
    route_to: str = ""
    carrier: str = ""
    cargo: str = ""
    weight_kg: float = 0
    amount: float = 0
    priority: str = "Средний"
    owner: str = ""
    status: str = "planned"
    eta: str | None = None
    tracking_no: str = ""
    deal_id: int | None = None


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str = ""
    customer: str
    address: str = ""
    route_from: str = ""
    route_to: str = ""
    carrier: str = ""
    cargo: str = ""
    weight_kg: float = 0
    amount: float = 0
    priority: str = "Средний"
    owner: str = ""
    status: str
    eta: str | None = None
    tracking_no: str = ""
    deal_id: int | None = None
    insight: str = ""


class StatusUpdate(BaseModel):
    """Смена статуса доставки РБ/РФ (planned → assigned → in_transit → delivered)."""

    status: str


# --- Импорт из Китая ----------------------------------------------------------
class ImportShipmentCreate(BaseModel):
    supplier: str
    number: str = ""
    flag: str = "🇨🇳"
    container_no: str = ""
    route: str = ""
    incoterms: str = "FOB"
    mode: str = "море"
    cargo: str = ""
    qty: int = 0
    amount: float = 0
    priority: str = "Средний"
    owner: str = ""
    stage: str = "factory"
    customs_status: str = ""
    eta: str | None = None
    po_ref: str = ""


class ImportShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str = ""
    supplier: str
    flag: str = "🇨🇳"
    container_no: str = ""
    route: str = ""
    incoterms: str = "FOB"
    mode: str = "море"
    cargo: str = ""
    qty: int = 0
    amount: float = 0
    priority: str = "Средний"
    owner: str = ""
    stage: str
    customs_status: str = ""
    eta: str | None = None
    po_ref: str = ""
    insight: str = ""


class ImportStageUpdate(BaseModel):
    """Смена стадии импорта (factory → consolidation → in_transit → customs → warehouse)."""

    stage: str
    customs_status: str | None = None


# --- Перевозчики --------------------------------------------------------------
class CarrierCreate(BaseModel):
    name: str
    kind: str = "РБ"
    mode: str = "авто"
    contact: str = ""
    integration: str = "manual"
    on_time_pct: int = 0
    avg_days: int = 0
    shipments_count: int = 0
    active: bool = True


class CarrierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    mode: str
    contact: str = ""
    integration: str
    on_time_pct: int
    avg_days: int
    shipments_count: int
    active: bool


# --- Дашборд логистики (log-8) ------------------------------------------------
class CarrierStat(BaseModel):
    name: str
    kind: str
    shipments: int
    on_time_pct: int
    avg_days: int


class DashboardOut(BaseModel):
    in_transit: int            # всего грузов в пути (доставка + импорт в движении + таможня)
    delivery_in_transit: int   # доставки РБ/РФ в пути
    import_in_transit: int     # импорт в движении (консолидация + плечо)
    at_customs: int            # на таможенном оформлении
    delivered_total: int       # доставлено (накопительно)
    avg_delivery_days: float   # среднее время доставки по активным перевозчикам
    on_time_pct: float         # средний OTD по активным перевозчикам, %
    logistics_cost: float      # стоимость логистики в работе, BYN
    carriers: list[CarrierStat]
