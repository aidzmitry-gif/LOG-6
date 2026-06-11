"""Pydantic-схемы модуля Logistics."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


# --- Доставка РБ/РФ -----------------------------------------------------------
class ShipmentCreate(BaseModel):
    customer: str
    number: str = ""
    address: str = ""
    route_from: str = ""
    route_to: str = ""
    carrier: str = ""
    carrier_code: str = ""
    carrier_order_no: str = ""
    cargo: str = ""
    weight_kg: float = 0
    amount: float = 0          # тариф перевозчика (расход на доставку), BYN
    payer: str = "компания"    # кто оплачивает доставку (компания/клиент)
    priority: str = "Средний"
    owner: str = ""
    status: str = "planned"
    eta: str | None = None
    tracking_no: str = ""
    tracking_status: str = ""
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
    carrier_code: str = ""
    carrier_order_no: str = ""
    cargo: str = ""
    weight_kg: float = 0
    amount: float = 0
    payer: str = "компания"
    priority: str = "Средний"
    owner: str = ""
    status: str
    eta: str | None = None
    tracking_no: str = ""
    tracking_status: str = ""
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
    code: str = ""
    kind: str = "РБ"
    mode: str = "авто"
    contact: str = ""
    integration: str = "manual"
    track_url: str = ""
    on_time_pct: int = 0
    avg_days: int = 0
    shipments_count: int = 0
    active: bool = True


class CarrierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str = ""
    kind: str
    mode: str
    contact: str = ""
    integration: str
    track_url: str = ""
    on_time_pct: int
    avg_days: int
    shipments_count: int
    active: bool


class CarrierCatalogItem(BaseModel):
    """Элемент справочника известных перевозчиков РБ (для выбора в UI, log-5)."""

    code: str
    name: str
    kind: str
    mode: str
    integration: str
    track_url: str = ""


class CarrierOrderRequest(BaseModel):
    """Оформление заказа у перевозчика (DPD / Автолайт Экспресс / …).

    В прототипе создаёт заказ локально: фиксирует перевозчика, тариф (расход),
    трек-номер и переводит доставку в статус ``assigned``. Реальный вызов API
    перевозчика (создание накладной, забор трек-номера) — Итерация 1 (log-5).
    """

    carrier_code: str = ""              # slug из каталога; либо произвольный carrier
    carrier: str = ""                   # название (если перевозчик не из каталога)
    carrier_order_no: str = ""          # № накладной у перевозчика (если уже есть)
    tracking_no: str = ""               # трек-номер (если уже выдан)
    shipping_cost: float | None = None  # тариф перевозчика, BYN
    payer: str | None = None            # кто платит (компания/клиент)
    eta: str | None = None


class TrackingUpdate(BaseModel):
    """Обновление статуса трекинга от перевозчика (текстовый статус + ETA)."""

    tracking_status: str
    eta: str | None = None
    tracking_no: str | None = None


# --- Дашборд и расходы логистики (log-8) --------------------------------------
class CarrierStat(BaseModel):
    name: str
    kind: str
    shipments: int
    on_time_pct: int
    avg_days: int


class CarrierCostStat(BaseModel):
    """Расход на доставку в разрезе перевозчика, BYN."""

    carrier: str
    shipments: int
    cost: float


class DashboardOut(BaseModel):
    in_transit: int             # всего грузов в пути (доставка + импорт в движении + таможня)
    delivery_in_transit: int    # доставки РБ/РФ в пути
    import_in_transit: int      # импорт в движении (консолидация + плечо)
    at_customs: int             # на таможенном оформлении
    delivered_total: int        # доставлено (накопительно)
    avg_delivery_days: float    # среднее время доставки по активным перевозчикам
    on_time_pct: float          # средний OTD по активным перевозчикам, %
    logistics_cost: float       # стоимость логистики в работе, BYN
    shipping_cost_company: float  # расход компании на доставку РБ/РФ (payer = компания), BYN
    carriers: list[CarrierStat]
    cost_by_carrier: list[CarrierCostStat]


class CostReportOut(BaseModel):
    """Отчёт по расходам на перевозку (log-8): итоги и разбивка по перевозчикам."""

    total: float                # все расходы на доставку РБ/РФ, BYN
    company: float              # за счёт компании, BYN
    client: float               # за счёт клиента, BYN
    import_cost: float          # себестоимость доставки импорта, BYN
    by_carrier: list[CarrierCostStat]


# --- Тарифы и зоны (BACKEND_SPEC §1-2) ----------------------------------------
class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    coverage: str = ""
    cities: list[str] = []
    sla_days_min: int = 1
    sla_days_max: int = 2


class CarrierTariffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    carrier_code: str
    zone_code: str
    price_w5: float
    price_w10: float
    price_w30: float
    over30_per_kg: float
    pickup_fee: float = 0
    cod_pct: float = 0
    insurance_pct: float = 0
    effective_from: date


class QuoteRequest(BaseModel):
    """Запрос котировки доставки: зона + вес (+ опц. забор/наложка/страховка)."""

    zone_code: str
    weight_kg: float | None = None      # если не задан — берётся вес из отгрузки
    pickup: bool = False
    cod_amount: float = 0.0             # сумма наложенного платежа, BYN
    declared_value: float = 0.0        # объявленная ценность (для страховки), BYN


class QuoteOut(BaseModel):
    """Котировка одного перевозчика по зоне: разбивка + итог + срок."""

    carrier_code: str
    carrier: str = ""
    zone_code: str
    weight_kg: float
    base: float
    pickup: float
    cod_fee: float
    insurance_fee: float
    total: float
    sla_days_min: int = 0
    sla_days_max: int = 0


# --- Scorecard перевозчиков (BACKEND_SPEC §3) ---------------------------------
class ScorecardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    carrier_code: str
    period: str
    otd_pct: float = 0
    otif_pct: float = 0
    damage_free_pct: float = 0
    billing_accuracy_pct: float = 0
    claims_ratio_pct: float = 0
    cost_per_delivery: float = 0
    shipments: int = 0
    score: float = 0
    grade: str = "C"


# --- Аудит счетов перевозчиков (BACKEND_SPEC §4) ------------------------------
class AuditEntryCreate(BaseModel):
    """Зарегистрировать счёт перевозчика для сверки с ожидаемым тарифом."""

    shipment_code: str
    carrier_code: str
    invoice_amount: float
    expected_amount: float | None = None  # если не задан — считается из тарифа (zone_code+weight_kg)
    zone_code: str = ""
    weight_kg: float = 0
    reason: str = ""


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_code: str
    carrier_code: str
    invoice_amount: float
    expected_amount: float
    variance: float
    reason: str = ""
    status: str = "open"


class AuditReportOut(BaseModel):
    """Сводка аудита счетов за период: проверено, расхождений, к возврату, позиции."""

    period: str
    checked: int
    discrepancies: int
    to_recover: float       # сумма положительных расхождений (переплата), BYN
    items: list[AuditEntryOut]


# --- Парк машин и пригодность груза (Блок 2) ----------------------------------
class VehicleCreate(BaseModel):
    vehicle_class: str
    capacity_kg: float = 0
    volume_m3: float = 0
    temp_control: bool = False
    count: int = 1


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    carrier_code: str
    vehicle_class: str
    capacity_kg: float = 0
    volume_m3: float = 0
    temp_control: bool = False
    count: int = 1


class CapabilityCreate(BaseModel):
    category: str
    adr: bool = False
    oversize: bool = False
    max_weight_kg: float = 0
    max_dim_cm: int = 0


class CapabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    carrier_code: str
    category: str
    adr: bool = False
    oversize: bool = False
    max_weight_kg: float = 0
    max_dim_cm: int = 0


class EligibleCarrierOut(BaseModel):
    """Пригодный перевозчик под груз: код, имя, подходящая машина."""

    carrier_code: str
    carrier: str
    vehicle_class: str
    capacity_kg: float


# --- Тендер на перевозку: RFQ / приглашения / предложения (Блок 4) ------------
class RfqCreate(BaseModel):
    """Создать тендер на перевозку (наёмный перевозчик по договору)."""

    cargo: str = ""
    weight_kg: float = 0
    max_dim_cm: int = 0
    category: str = ""              # обычный/АКБ/опасный_ADR/хрупкое/негабарит/температурный
    needs_temp: bool = False
    adr: bool = False
    route_from: str = ""
    route_to: str = ""
    zone_code: str = ""
    pickup_date: str = ""
    declared_value: float = 0
    office_doc_ref: str = ""        # связь с заявкой офиса (Блок 3)
    deal_id: int | None = None
    created_by: str = ""
    deadline: str = ""


class RfqOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str = ""
    cargo: str = ""
    weight_kg: float = 0
    max_dim_cm: int = 0
    category: str = ""
    needs_temp: bool = False
    adr: bool = False
    route_from: str = ""
    route_to: str = ""
    zone_code: str = ""
    pickup_date: str = ""
    declared_value: float = 0
    status: str
    office_doc_ref: str = ""
    deal_id: int | None = None
    created_by: str = ""
    deadline: str = ""
    awarded_carrier_code: str = ""
    awarded_price: float = 0
    shipment_id: int | None = None


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    carrier_code: str
    channel: str = "manual"
    status: str = "sent"
    token: str = ""                     # секрет публичной ссылки на подачу ставки
    notified_at: datetime | None = None
    detail: str = ""                    # результат уведомления (журнал рассылки)


class BroadcastOut(BaseModel):
    """Итог рассылки тендера: сколько перевозчиков приглашено, уведомлено и кто."""

    rfq_id: int
    status: str
    invited: int
    notified: int = 0                   # фактически уведомлено (по контакту), без skip
    carriers: list[str]


class BidCreate(BaseModel):
    """Зарегистрировать предложение перевозчика (ручной ввод; реальная — Итерация 1)."""

    carrier_code: str
    price: float
    eta_days: int = 0
    vehicle_class: str = ""
    valid_until: str = ""
    comment: str = ""


class PublicBidCreate(BaseModel):
    """Ставка перевозчика по публичной ссылке тендера (без авторизации; перевозчик
    идентифицируется токеном приглашения, поэтому ``carrier_code`` не нужен)."""

    price: float
    eta_days: int = 0
    vehicle_class: str = ""
    valid_until: str = ""
    comment: str = ""


class BidOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    carrier_code: str
    carrier: str = ""
    price: float
    eta_days: int = 0
    vehicle_class: str = ""
    valid_until: str = ""
    comment: str = ""
    round: int = 1
    is_best: bool = False           # вычисляется при выдаче (минимальная цена)


class NegotiateRequest(BaseModel):
    """Раунд переговоров: новая (сниженная) цена перевозчика."""

    carrier_code: str
    new_price: float
    comment: str = ""


class AwardRequest(BaseModel):
    """Выбрать перевозчика и заключить договор. Без ``carrier_code`` — лучший по цене."""

    carrier_code: str = ""


class AwardOut(BaseModel):
    """Итог заключения тендера: выбранный перевозчик, цена, созданная отгрузка."""

    rfq_id: int
    status: str
    carrier_code: str
    carrier: str
    price: float
    shipment_id: int
    shipment_number: str


# --- Аналитика стоимости (cost-insights, «улучшать стоимость постоянно») -------
class ZoneCostInsight(BaseModel):
    """Свод по зоне: самый дешёвый перевозчик, средний/макс итог, разброс (потенциал)."""

    zone_code: str
    zone_name: str = ""
    carriers: int = 0
    cheapest_carrier: str = ""
    cheapest_carrier_name: str = ""
    cheapest_total: float = 0
    avg_total: float = 0
    max_total: float = 0
    spread_pct: float = 0


class TenderSaving(BaseModel):
    """Экономия по заключённому тендеру: стартовая цена vs выигравшая."""

    rfq_number: str
    route: str = ""
    carrier: str = ""
    baseline: float = 0
    awarded: float = 0
    saved: float = 0
    saved_pct: float = 0


class CostInsightsOut(BaseModel):
    """Сводка «улучшение стоимости»: где дешевле, экономия торга, к возврату, рекомендации."""

    reference_weight_kg: float
    zones: list[ZoneCostInsight]
    potential_savings: float = 0       # Σ(avg − cheapest) по зонам на эталонный вес, BYN
    best_savings_zone: str = ""
    tender_savings_total: float = 0
    tenders: list[TenderSaving]
    audit_to_recover: float = 0
    recommendations: list[str] = []


# --- Справочник перевозчиков РБ (сиды + каталог, log-5) -----------------------
# Способ интеграции в прототипе: manual/csv. Реальные API — Итерация 1+.
# track_url — шаблон ссылки трекинга ({n} = трек-номер), уточняется при подключении.
CARRIERS_RB: list[dict] = [
    {"code": "dpd", "name": "DPD", "kind": "РБ", "mode": "авто",
     "integration": "api", "track_url": "https://www.dpd.by/tracking/?number={n}"},
    {"code": "autolight", "name": "Автолайт Экспресс", "kind": "РБ", "mode": "авто",
     "integration": "csv", "track_url": "https://autolight.by/tracking/?number={n}"},
    {"code": "cdek", "name": "СДЭК", "kind": "РБ/СНГ", "mode": "авто",
     "integration": "api", "track_url": "https://www.cdek.by/tracking?order_id={n}"},
    {"code": "evropochta", "name": "Европочта", "kind": "РБ", "mode": "авто",
     "integration": "csv", "track_url": "https://evropochta.by/tracking/?number={n}"},
    {"code": "belpost", "name": "Белпочта", "kind": "РБ", "mode": "авто",
     "integration": "manual", "track_url": "https://webservices.belpost.by/searchRu/{n}"},
]
