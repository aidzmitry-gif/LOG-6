"""HTTP-API модуля Logistics. Монтируется под префиксом ``/logistics`` (§5.7).

Две воронки-доски: доставка по РБ/РФ (``/board``) и импорт из Китая
(``/imports/board``), плюс реестр перевозчиков (``/carriers``) и дашборд
(``/dashboard``, log-8). AI-координация (log-6) — Итерация 1, в прототипе поле
``insight`` остаётся пустым (фронт бейджит его как «Итерация 1 · ИИ»).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.core import Core
from core.runtime.deps import get_core, get_session
from core.runtime.funnel import FunnelBoardOut, FunnelCard, build_board
from core.services.config import get_settings
from modules.logistics import analytics, fleet, notify, pricing, seeds
from modules.logistics.models import (
    Carrier,
    CarrierBid,
    CarrierCargoCapability,
    CarrierRfq,
    CarrierRfqInvite,
    CarrierScorecard,
    CarrierTariff,
    CarrierVehicle,
    FreightAuditLog,
    ImportShipment,
    Shipment,
    Zone,
)
from modules.logistics.schemas import (
    CARRIERS_RB,
    AuditEntryCreate,
    AuditEntryOut,
    AuditReportOut,
    AwardOut,
    AwardRequest,
    BidCreate,
    BidOut,
    BroadcastOut,
    CapabilityCreate,
    CapabilityOut,
    CarrierCatalogItem,
    CarrierCostStat,
    CarrierCreate,
    CarrierOrderRequest,
    CarrierOut,
    CarrierStat,
    CarrierTariffOut,
    CostInsightsOut,
    CostReportOut,
    DashboardOut,
    EligibleCarrierOut,
    ImportShipmentCreate,
    ImportShipmentOut,
    ImportStageUpdate,
    InviteOut,
    NegotiateRequest,
    PublicBidCreate,
    QuoteOut,
    QuoteRequest,
    RfqCreate,
    RfqOut,
    ScorecardOut,
    ShipmentCreate,
    ShipmentOut,
    StatusUpdate,
    TrackingUpdate,
    VehicleCreate,
    VehicleOut,
    ZoneOut,
)
from modules.logistics.stages import DELIVERY_STAGES, IMPORT_STAGES, TENDER_STAGES

router = APIRouter(tags=["logistics"])

DELIVERED_STATUS = "delivered"  # доставка завершена → закрытие сделки (logistics → sales)
ASSIGNED_STATUS = "assigned"    # перевозчик назначен / заказ у перевозчика оформлен
WAREHOUSE_STAGE = "warehouse"   # импорт принят на склад (информационное событие)
CUSTOMS_STAGE = "customs"
CLIENT_PAYER = "клиент"         # доставка за счёт клиента (не расход компании)


def _route(frm: str, to: str, fallback: str = "") -> str:
    """Маршрут строкой: «Откуда → Куда» (или ``fallback``, если точки не заданы)."""
    parts = [p for p in (frm, to) if p]
    return " → ".join(parts) if parts else fallback


def _qty_tag(qty: int) -> list[str]:
    return [f"{qty} шт"] if qty else []


# --- Карточки воронок ---------------------------------------------------------
def _shipment_card(r: Shipment) -> FunnelCard:
    tags = [
        t
        for t in (
            r.cargo,
            f"{float(r.weight_kg):g} кг" if r.weight_kg else "",
            f"№ {r.carrier_order_no}" if r.carrier_order_no else "",
        )
        if t
    ]
    return FunnelCard(
        id=r.id,
        code=r.number or f"ЛОГ-{r.id}",
        title=r.customer,
        subtitle=_route(r.route_from, r.route_to, r.address),
        amount=float(r.amount),
        priority=r.priority,
        status_tag=r.carrier,
        owner=r.owner,
        date=r.eta or "",
        next_step=r.tracking_status,
        score=f"📍 {r.tracking_no}" if r.tracking_no else "",
        insight=r.insight,
        tags=tags,
    )


def _import_card(r: ImportShipment) -> FunnelCard:
    tags = [t for t in (r.container_no, r.incoterms, *_qty_tag(r.qty)) if t]
    status_tag = (r.customs_status or "Оформление") if r.stage == CUSTOMS_STAGE else r.mode
    return FunnelCard(
        id=r.id,
        code=r.number or f"ИМП-{r.id}",
        title=r.cargo or r.supplier,
        subtitle=r.route or r.supplier,
        flag=r.flag,
        amount=float(r.amount),
        priority=r.priority,
        status_tag=status_tag,
        owner=r.owner,
        date=r.eta or "",
        score=f"PO {r.po_ref}" if r.po_ref else "",
        insight=r.insight,
        tags=tags,
    )


# --- Доставка РБ/РФ (log-1) ---------------------------------------------------
@router.get("/shipments", response_model=list[ShipmentOut])
async def list_shipments(session: AsyncSession = Depends(get_session)):
    """Доставки РБ/РФ (плоский список — для аналитики и совместимости)."""
    return (await session.execute(select(Shipment).order_by(Shipment.id.desc()))).scalars().all()


@router.get("/board", response_model=FunnelBoardOut)
async def delivery_board(session: AsyncSession = Depends(get_session)) -> FunnelBoardOut:
    """Воронка доставок РБ/РФ: заявки сгруппированы по статусу."""
    rows = (await session.execute(select(Shipment))).scalars().all()
    return build_board(DELIVERY_STAGES, rows, _shipment_card, stage_of=lambda r: r.status)


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
async def create_shipment(payload: ShipmentCreate, session: AsyncSession = Depends(get_session)):
    """Создать доставку. Номер генерируется автоматически, если не задан."""
    data = payload.model_dump()
    data["weight_kg"] = Decimal(str(data["weight_kg"]))
    data["amount"] = Decimal(str(data["amount"]))
    obj = Shipment(**data)
    session.add(obj)
    await session.flush()
    if not obj.number:
        obj.number = f"ЛОГ-2026-{obj.id:04d}"
    await session.commit()
    await session.refresh(obj)
    return obj


@router.patch("/shipments/{shipment_id}", response_model=ShipmentOut)
async def update_shipment(
    shipment_id: int,
    payload: StatusUpdate,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Сменить статус доставки. При ``delivered`` — закрытие сделки (logistics → sales)."""
    obj = await session.get(Shipment, shipment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    obj.status = payload.status
    if payload.status == DELIVERED_STATUS:
        core.event_bus.emit(
            session,
            "logistics.shipment.delivered",
            {"deal_id": obj.deal_id, "customer": obj.customer, "entity_ref": f"shipment:{obj.id}"},
        )
        # обратно в офис: документ закрывается по доставке (matched по logistics_ref = number)
        core.event_bus.emit(
            session,
            "logistics.delivery.delivered",
            {"log_ref": obj.number, "number": obj.number, "carrier_name": obj.carrier,
             "delivered_at": obj.eta or "", "entity_ref": obj.number},
        )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.post("/shipments/{shipment_id}/carrier-order", response_model=ShipmentOut)
async def create_carrier_order(
    shipment_id: int,
    payload: CarrierOrderRequest,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Оформить заказ на доставку у перевозчика (DPD / Автолайт Экспресс / …, log-5).

    Прототип: фиксирует перевозчика и тариф (расход на доставку), генерирует
    № заказа, при необходимости трек-номер, переводит доставку в ``assigned`` и
    публикует ``logistics.carrier_order.created``. Реальный вызов API перевозчика
    (создание накладной и получение трек-номера) — Итерация 1.
    """
    obj = await session.get(Shipment, shipment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")

    code = payload.carrier_code or obj.carrier_code
    name = payload.carrier or obj.carrier
    if code and not name:
        match = next((c for c in CARRIERS_RB if c["code"] == code), None)
        if match:
            name = match["name"]
    if not name:
        raise HTTPException(status_code=400, detail="Не указан перевозчик")

    obj.carrier_code = code
    obj.carrier = name
    obj.carrier_order_no = payload.carrier_order_no or f"{(code or 'CRR').upper()}-2026-{obj.id:04d}"
    if payload.tracking_no:
        obj.tracking_no = payload.tracking_no
    if payload.shipping_cost is not None:
        obj.amount = Decimal(str(payload.shipping_cost))
    if payload.payer:
        obj.payer = payload.payer
    if payload.eta:
        obj.eta = payload.eta
    if obj.status == "planned":
        obj.status = ASSIGNED_STATUS

    core.event_bus.emit(
        session,
        "logistics.carrier_order.created",
        {
            "shipment_id": obj.id,
            "carrier": obj.carrier,
            "carrier_order_no": obj.carrier_order_no,
            "amount": float(obj.amount),
            "entity_ref": f"shipment:{obj.id}",
        },
    )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.patch("/shipments/{shipment_id}/tracking", response_model=ShipmentOut)
async def update_tracking(
    shipment_id: int,
    payload: TrackingUpdate,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Обновить статус трекинга от перевозчика (текстовый статус + ETA + трек-номер).

    Публикует ``logistics.delivery.tracking`` → офис видит состояние перевозки на
    карточке документа (matched по ``logistics_ref`` = ``number``).
    """
    obj = await session.get(Shipment, shipment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    obj.tracking_status = payload.tracking_status
    if payload.eta is not None:
        obj.eta = payload.eta
    if payload.tracking_no is not None:
        obj.tracking_no = payload.tracking_no
    core.event_bus.emit(
        session,
        "logistics.delivery.tracking",
        {"log_ref": obj.number, "number": obj.number, "tracking_status": obj.tracking_status,
         "carrier_name": obj.carrier, "eta": obj.eta or "", "entity_ref": obj.number},
    )
    await session.commit()
    await session.refresh(obj)
    return obj


# --- Импорт из Китая (log-2, log-4) -------------------------------------------
@router.get("/imports", response_model=list[ImportShipmentOut])
async def list_imports(session: AsyncSession = Depends(get_session)):
    """Импортные поставки (плоский список)."""
    return (
        await session.execute(select(ImportShipment).order_by(ImportShipment.id.desc()))
    ).scalars().all()


@router.get("/imports/board", response_model=FunnelBoardOut)
async def import_board(session: AsyncSession = Depends(get_session)) -> FunnelBoardOut:
    """Цепочка импорта: фабрика → консолидация → плечо → таможня → склад."""
    rows = (await session.execute(select(ImportShipment))).scalars().all()
    return build_board(IMPORT_STAGES, rows, _import_card)


@router.post("/imports", response_model=ImportShipmentOut, status_code=201)
async def create_import(payload: ImportShipmentCreate, session: AsyncSession = Depends(get_session)):
    """Создать импортную поставку. Номер генерируется автоматически, если не задан."""
    data = payload.model_dump()
    data["amount"] = Decimal(str(data["amount"]))
    obj = ImportShipment(**data)
    session.add(obj)
    await session.flush()
    if not obj.number:
        obj.number = f"ИМП-2026-{obj.id:04d}"
    await session.commit()
    await session.refresh(obj)
    return obj


@router.patch("/imports/{import_id}", response_model=ImportShipmentOut)
async def update_import(
    import_id: int,
    payload: ImportStageUpdate,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Сменить стадию импорта. Выход с таможни и приёмка на склад — события для Control Tower."""
    obj = await session.get(ImportShipment, import_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Импортная поставка не найдена")
    prev_stage = obj.stage
    obj.stage = payload.stage
    if payload.customs_status is not None:
        obj.customs_status = payload.customs_status
    if prev_stage == CUSTOMS_STAGE and payload.stage == WAREHOUSE_STAGE:
        core.event_bus.emit(
            session,
            "logistics.import.customs_cleared",
            {"container_no": obj.container_no, "supplier": obj.supplier, "entity_ref": f"import:{obj.id}"},
        )
    if payload.stage == WAREHOUSE_STAGE:
        core.event_bus.emit(
            session,
            "logistics.import.arrived",
            {"cargo": obj.cargo, "qty": obj.qty, "po_ref": obj.po_ref, "entity_ref": f"import:{obj.id}"},
        )
    await session.commit()
    await session.refresh(obj)
    return obj


# --- Перевозчики (log-1, log-5) -----------------------------------------------
@router.get("/carriers/catalog", response_model=list[CarrierCatalogItem])
async def carriers_catalog() -> list[CarrierCatalogItem]:
    """Справочник известных перевозчиков РБ (DPD, Автолайт Экспресс, СДЭК, …)."""
    return [CarrierCatalogItem(**c) for c in CARRIERS_RB]


@router.post("/carriers/seed", response_model=list[CarrierOut])
async def seed_carriers(session: AsyncSession = Depends(get_session)):
    """Заполнить реестр перевозчиками РБ из каталога (идемпотентно — по ``code``)."""
    existing = {
        c.code for c in (await session.execute(select(Carrier))).scalars().all() if c.code
    }
    for item in CARRIERS_RB:
        if item["code"] in existing:
            continue
        session.add(Carrier(
            name=item["name"], code=item["code"], kind=item["kind"], mode=item["mode"],
            integration=item["integration"], track_url=item["track_url"],
        ))
    await session.commit()
    return (await session.execute(select(Carrier).order_by(Carrier.id.desc()))).scalars().all()


@router.get("/carriers", response_model=list[CarrierOut])
async def list_carriers(session: AsyncSession = Depends(get_session)):
    """Реестр перевозчиков и подрядчиков с метриками надёжности."""
    return (await session.execute(select(Carrier).order_by(Carrier.id.desc()))).scalars().all()


@router.post("/carriers", response_model=CarrierOut, status_code=201)
async def create_carrier(payload: CarrierCreate, session: AsyncSession = Depends(get_session)):
    """Добавить перевозчика."""
    obj = Carrier(**payload.model_dump())
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


# --- Дашборд и расходы логистики (log-8) --------------------------------------
def _company_cost_by_carrier(shipments) -> list[CarrierCostStat]:
    """Расход компании на доставку (payer != клиент) в разрезе перевозчика, BYN."""
    agg: dict[str, dict] = {}
    for s in shipments:
        if s.payer == CLIENT_PAYER:
            continue
        name = s.carrier or "Без перевозчика"
        row = agg.setdefault(name, {"shipments": 0, "cost": Decimal("0")})
        row["shipments"] += 1
        row["cost"] += s.amount or Decimal("0")
    stats = [
        CarrierCostStat(carrier=name, shipments=v["shipments"], cost=float(v["cost"]))
        for name, v in agg.items()
    ]
    return sorted(stats, key=lambda x: x.cost, reverse=True)


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(session: AsyncSession = Depends(get_session)) -> DashboardOut:
    """KPI логистики: грузы в пути, на таможне, среднее время доставки, OTD, расходы."""
    shipments = (await session.execute(select(Shipment))).scalars().all()
    imports = (await session.execute(select(ImportShipment))).scalars().all()
    carriers = (await session.execute(select(Carrier))).scalars().all()

    delivery_in_transit = sum(1 for s in shipments if s.status == "in_transit")
    import_in_transit = sum(1 for i in imports if i.stage in ("consolidation", "in_transit"))
    at_customs = sum(1 for i in imports if i.stage == CUSTOMS_STAGE)
    delivered_total = sum(1 for s in shipments if s.status == DELIVERED_STATUS)

    active = [c for c in carriers if c.active]
    with_days = [c for c in active if c.avg_days > 0]
    with_otd = [c for c in active if c.on_time_pct > 0]
    avg_delivery_days = round(sum(c.avg_days for c in with_days) / len(with_days), 1) if with_days else 0.0
    on_time_pct = round(sum(c.on_time_pct for c in with_otd) / len(with_otd), 1) if with_otd else 0.0

    cost = sum((s.amount or Decimal("0")) for s in shipments if s.status != DELIVERED_STATUS)
    cost += sum((i.amount or Decimal("0")) for i in imports if i.stage != WAREHOUSE_STAGE)
    company_cost = sum(
        (s.amount or Decimal("0")) for s in shipments if s.payer != CLIENT_PAYER
    )

    return DashboardOut(
        in_transit=delivery_in_transit + import_in_transit + at_customs,
        delivery_in_transit=delivery_in_transit,
        import_in_transit=import_in_transit,
        at_customs=at_customs,
        delivered_total=delivered_total,
        avg_delivery_days=avg_delivery_days,
        on_time_pct=on_time_pct,
        logistics_cost=float(cost),
        shipping_cost_company=float(company_cost),
        carriers=[
            CarrierStat(
                name=c.name,
                kind=c.kind,
                shipments=c.shipments_count,
                on_time_pct=c.on_time_pct,
                avg_days=c.avg_days,
            )
            for c in active
        ],
        cost_by_carrier=_company_cost_by_carrier(shipments),
    )


@router.get("/costs", response_model=CostReportOut)
async def costs_report(session: AsyncSession = Depends(get_session)) -> CostReportOut:
    """Отчёт по расходам на перевозку (log-8): всего, компания/клиент, по перевозчикам."""
    shipments = (await session.execute(select(Shipment))).scalars().all()
    imports = (await session.execute(select(ImportShipment))).scalars().all()

    total = sum((s.amount or Decimal("0")) for s in shipments)
    client = sum((s.amount or Decimal("0")) for s in shipments if s.payer == CLIENT_PAYER)
    company = total - client
    import_cost = sum((i.amount or Decimal("0")) for i in imports)

    return CostReportOut(
        total=float(total),
        company=float(company),
        client=float(client),
        import_cost=float(import_cost),
        by_carrier=_company_cost_by_carrier(shipments),
    )


# --- Тарифы и зоны (BACKEND_SPEC §1-2) -----------------------------------------
_CARRIER_NAME = {c["code"]: c["name"] for c in CARRIERS_RB}


@router.post("/zones/seed", response_model=list[ZoneOut])
async def seed_zones(session: AsyncSession = Depends(get_session)):
    """Заполнить зоны доставки по РБ (идемпотентно — по ``code``)."""
    existing = {z.code for z in (await session.execute(select(Zone))).scalars().all()}
    for item in seeds.ZONES_SEED:
        if item["code"] not in existing:
            session.add(Zone(**item))
    await session.commit()
    return (await session.execute(select(Zone).order_by(Zone.code))).scalars().all()


@router.get("/zones", response_model=list[ZoneOut])
async def list_zones(session: AsyncSession = Depends(get_session)):
    """Список зон доставки по РБ."""
    return (await session.execute(select(Zone).order_by(Zone.code))).scalars().all()


@router.post("/carrier-tariffs/seed", response_model=list[CarrierTariffOut])
async def seed_carrier_tariffs(session: AsyncSession = Depends(get_session)):
    """Заполнить прайс-матрицу перевозчик×зона (идемпотентно по carrier+zone+effective_from)."""
    existing = {
        (t.carrier_code, t.zone_code, t.effective_from)
        for t in (await session.execute(select(CarrierTariff))).scalars().all()
    }
    for item in seeds.TARIFFS_SEED:
        key = (item["carrier_code"], item["zone_code"], item["effective_from"])
        if key not in existing:
            session.add(CarrierTariff(**item))
    await session.commit()
    return (
        await session.execute(
            select(CarrierTariff).order_by(CarrierTariff.carrier_code, CarrierTariff.zone_code)
        )
    ).scalars().all()


@router.get("/carrier-tariffs", response_model=list[CarrierTariffOut])
async def list_carrier_tariffs(zone: str = "", session: AsyncSession = Depends(get_session)):
    """Тарифы (опц. фильтр по зоне ``?zone=z2``)."""
    q = select(CarrierTariff).order_by(CarrierTariff.carrier_code, CarrierTariff.zone_code)
    if zone:
        q = q.where(CarrierTariff.zone_code == zone)
    return (await session.execute(q)).scalars().all()


@router.post("/shipments/{shipment_id}/quote", response_model=list[QuoteOut])
async def quote_shipment(
    shipment_id: int,
    payload: QuoteRequest,
    session: AsyncSession = Depends(get_session),
):
    """Котировки доставки по всем перевозчикам зоны — расчёт из справочника тарифов.

    Заменяет статичные числа в модалке «Оформление доставки» расчётом ``quote_tariff``
    (вилки веса, забор, наложка, страховка). Сортировка — по итоговой стоимости.
    """
    obj = await session.get(Shipment, shipment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    weight = payload.weight_kg if payload.weight_kg is not None else float(obj.weight_kg or 0)

    zone = (
        await session.execute(select(Zone).where(Zone.code == payload.zone_code))
    ).scalars().first()
    if zone is None:
        raise HTTPException(status_code=422, detail="Неизвестная зона")

    tariffs = (
        await session.execute(
            select(CarrierTariff).where(CarrierTariff.zone_code == payload.zone_code)
        )
    ).scalars().all()
    if not tariffs:
        raise HTTPException(status_code=404, detail="Нет тарифов для зоны (запустите сид)")

    quotes: list[QuoteOut] = []
    for t in tariffs:
        breakdown = pricing.quote_tariff(
            t, weight, pickup=payload.pickup,
            cod_amount=payload.cod_amount, declared_value=payload.declared_value,
        )
        quotes.append(QuoteOut(
            carrier_code=t.carrier_code,
            carrier=_CARRIER_NAME.get(t.carrier_code, t.carrier_code),
            zone_code=t.zone_code, weight_kg=weight,
            sla_days_min=zone.sla_days_min, sla_days_max=zone.sla_days_max,
            **breakdown,
        ))
    return sorted(quotes, key=lambda q: q.total)


# --- Scorecard перевозчиков (BACKEND_SPEC §3) ----------------------------------
@router.post("/carriers/scorecard/seed", response_model=list[ScorecardOut])
async def seed_scorecard(session: AsyncSession = Depends(get_session)):
    """Заполнить scorecard перевозчиков за период (идемпотентно по carrier+period)."""
    existing = {
        (s.carrier_code, s.period)
        for s in (await session.execute(select(CarrierScorecard))).scalars().all()
    }
    for item in seeds.SCORECARD_SEED:
        if (item["carrier_code"], item["period"]) not in existing:
            session.add(CarrierScorecard(**item))
    await session.commit()
    return (
        await session.execute(select(CarrierScorecard).order_by(CarrierScorecard.score.desc()))
    ).scalars().all()


@router.get("/carriers/scorecard", response_model=list[ScorecardOut])
async def carriers_scorecard(period: str = "", session: AsyncSession = Depends(get_session)):
    """Scorecard перевозчиков (опц. фильтр ``?period=2026-06``), сорт. по баллу."""
    q = select(CarrierScorecard).order_by(CarrierScorecard.score.desc())
    if period:
        q = q.where(CarrierScorecard.period == period)
    return (await session.execute(q)).scalars().all()


# --- Аудит счетов перевозчиков (BACKEND_SPEC §4) -------------------------------
async def _expected_for(session: AsyncSession, zone_code: str, weight_kg: float, carrier_code: str):
    """Ожидаемая стоимость по тарифу (для сверки счёта). None, если тарифа нет."""
    t = (
        await session.execute(
            select(CarrierTariff).where(
                CarrierTariff.carrier_code == carrier_code,
                CarrierTariff.zone_code == zone_code,
            )
        )
    ).scalars().first()
    if t is None:
        return None
    return pricing.quote_tariff(t, weight_kg)["total"]


@router.post("/costs/audit/seed", response_model=list[AuditEntryOut])
async def seed_audit(session: AsyncSession = Depends(get_session)):
    """Заполнить демо-расхождения аудита счетов (идемпотентно по shipment_code)."""
    existing = {
        a.shipment_code for a in (await session.execute(select(FreightAuditLog))).scalars().all()
    }
    for item in seeds.AUDIT_SEED:
        if item["shipment_code"] not in existing:
            session.add(FreightAuditLog(**item))
    await session.commit()
    return (
        await session.execute(select(FreightAuditLog).order_by(FreightAuditLog.id.desc()))
    ).scalars().all()


@router.post("/costs/audit", response_model=AuditEntryOut, status_code=201)
async def create_audit_entry(payload: AuditEntryCreate, session: AsyncSession = Depends(get_session)):
    """Зарегистрировать счёт перевозчика и сверить с ожидаемым тарифом.

    Если ``expected_amount`` не задан — считается из ``carrier_tariffs`` по
    ``zone_code``+``weight_kg``. ``variance = invoice - expected``; расхождение
    создаёт запись к разбору.
    """
    expected = payload.expected_amount
    if expected is None:
        expected = await _expected_for(
            session, payload.zone_code, payload.weight_kg, payload.carrier_code
        )
        if expected is None:
            raise HTTPException(
                status_code=422,
                detail="Не задан expected_amount и нет тарифа для зоны/перевозчика",
            )
    variance = round(payload.invoice_amount - expected, 2)
    obj = FreightAuditLog(
        shipment_code=payload.shipment_code,
        carrier_code=payload.carrier_code,
        invoice_amount=Decimal(str(payload.invoice_amount)),
        expected_amount=Decimal(str(expected)),
        variance=Decimal(str(variance)),
        reason=payload.reason,
        status="open" if variance != 0 else "closed",
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


@router.get("/costs/audit", response_model=AuditReportOut)
async def costs_audit(period: str = "", session: AsyncSession = Depends(get_session)) -> AuditReportOut:
    """Сводка аудита счетов: проверено, расхождений, сумма к возврату, позиции."""
    rows = (
        await session.execute(select(FreightAuditLog).order_by(FreightAuditLog.id.desc()))
    ).scalars().all()
    discrepancies = [r for r in rows if r.variance and float(r.variance) != 0]
    to_recover = sum(float(r.variance) for r in discrepancies if float(r.variance) > 0)
    return AuditReportOut(
        period=period or seeds.SCORECARD_PERIOD,
        checked=len(rows),
        discrepancies=len(discrepancies),
        to_recover=round(to_recover, 2),
        items=[AuditEntryOut.model_validate(r) for r in rows],
    )


# --- Парк машин и пригодность груза (Блок 2) -----------------------------------
def _carrier_name(code: str) -> str:
    """Имя перевозчика по коду (каталог РБ + свой транспорт)."""
    return _CARRIER_NAME.get(code) or seeds.CARRIER_NAMES_EXTRA.get(code, code)


@router.post("/fleet/seed", response_model=dict)
async def seed_fleet(session: AsyncSession = Depends(get_session)):
    """Заполнить парк машин и допуски перевозчиков (идемпотентно)."""
    have_vehicles = {
        (v.carrier_code, v.vehicle_class)
        for v in (await session.execute(select(CarrierVehicle))).scalars().all()
    }
    for item in seeds.VEHICLES_SEED:
        if (item["carrier_code"], item["vehicle_class"]) not in have_vehicles:
            session.add(CarrierVehicle(**item))
    have_caps = {
        (c.carrier_code, c.category)
        for c in (await session.execute(select(CarrierCargoCapability))).scalars().all()
    }
    for item in seeds.CAPABILITIES_SEED:
        if (item["carrier_code"], item["category"]) not in have_caps:
            session.add(CarrierCargoCapability(**item))
    await session.commit()
    vehicles = (await session.execute(select(CarrierVehicle))).scalars().all()
    caps = (await session.execute(select(CarrierCargoCapability))).scalars().all()
    return {"vehicles": len(vehicles), "capabilities": len(caps)}


@router.get("/carriers/{code}/vehicles", response_model=list[VehicleOut])
async def list_vehicles(code: str, session: AsyncSession = Depends(get_session)):
    """Парк машин перевозчика."""
    return (
        await session.execute(select(CarrierVehicle).where(CarrierVehicle.carrier_code == code))
    ).scalars().all()


@router.post("/carriers/{code}/vehicles", response_model=VehicleOut, status_code=201)
async def add_vehicle(code: str, payload: VehicleCreate, session: AsyncSession = Depends(get_session)):
    """Добавить машину в парк перевозчика."""
    obj = CarrierVehicle(carrier_code=code, **payload.model_dump())
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


@router.get("/carriers/{code}/cargo-capabilities", response_model=list[CapabilityOut])
async def list_capabilities(code: str, session: AsyncSession = Depends(get_session)):
    """Допуски перевозчика по категориям груза."""
    return (
        await session.execute(
            select(CarrierCargoCapability).where(CarrierCargoCapability.carrier_code == code)
        )
    ).scalars().all()


@router.post("/carriers/{code}/cargo-capabilities", response_model=CapabilityOut, status_code=201)
async def add_capability(code: str, payload: CapabilityCreate, session: AsyncSession = Depends(get_session)):
    """Добавить допуск перевозчика по категории груза."""
    obj = CarrierCargoCapability(carrier_code=code, **payload.model_dump())
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


@router.get("/carriers/eligible", response_model=list[EligibleCarrierOut])
async def eligible_carriers(
    weight_kg: float = 0,
    category: str = "",
    needs_temp: bool = False,
    max_dim_cm: int = 0,
    adr: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Перевозчики, пригодные под груз: достаточная машина + допуск к категории.

    Подбор по парку (``capacity_kg`` ≥ веса, термо-режим при ``needs_temp``) и
    допускам (категория, ADR, лимиты веса/габарита). Это таргет для рассылки
    тендера (Блок 4) и замена статичного флага «тяжёлый груз».
    """
    matches = await _eligible_matches(
        session, weight_kg=weight_kg, category=category,
        needs_temp=needs_temp, max_dim_cm=max_dim_cm, adr=adr,
    )
    result = [
        EligibleCarrierOut(
            carrier_code=code, carrier=_carrier_name(code),
            vehicle_class=m["vehicle_class"], capacity_kg=m["capacity_kg"],
        )
        for code, m in matches
    ]
    return sorted(result, key=lambda e: e.capacity_kg)


async def _eligible_matches(
    session: AsyncSession, *, weight_kg: float, category: str = "",
    needs_temp: bool = False, max_dim_cm: int = 0, adr: bool = False,
) -> list[tuple[str, dict]]:
    """Пригодные перевозчики под груз: ``[(carrier_code, {vehicle_class, capacity_kg})]``."""
    vehicles = (await session.execute(select(CarrierVehicle))).scalars().all()
    caps = (await session.execute(select(CarrierCargoCapability))).scalars().all()
    by_carrier: dict[str, dict] = {}
    for v in vehicles:
        by_carrier.setdefault(v.carrier_code, {"vehicles": [], "caps": []})["vehicles"].append(v)
    for c in caps:
        by_carrier.setdefault(c.carrier_code, {"vehicles": [], "caps": []})["caps"].append(c)
    out: list[tuple[str, dict]] = []
    for code, data in by_carrier.items():
        m = fleet.carrier_eligible(
            data["vehicles"], data["caps"],
            weight_kg=weight_kg, category=category,
            needs_temp=needs_temp, max_dim_cm=max_dim_cm, adr=adr,
        )
        if m is not None:
            out.append((code, m))
    return out


# --- Тендер на перевозку: RFQ / рассылка / предложения / договор (Блок 4) -------
def _bids_with_best(bids: list[CarrierBid]) -> list[BidOut]:
    """Предложения, отсортированные по цене, с пометкой лучшего (минимальная цена)."""
    ordered = sorted(bids, key=lambda b: float(b.price))
    best_id = ordered[0].id if ordered else None
    return [
        BidOut(
            **{k: getattr(b, k) for k in (
                "id", "rfq_id", "carrier_code", "eta_days", "vehicle_class",
                "valid_until", "comment", "round")},
            price=float(b.price), carrier=_carrier_name(b.carrier_code), is_best=(b.id == best_id),
        )
        for b in ordered
    ]


async def _get_rfq(session: AsyncSession, rfq_id: int) -> CarrierRfq:
    rfq = await session.get(CarrierRfq, rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail="Тендер не найден")
    return rfq


def _rfq_card(r: CarrierRfq) -> FunnelCard:
    tags = [t for t in (r.cargo, f"{float(r.weight_kg):g} кг" if r.weight_kg else "", r.category) if t]
    return FunnelCard(
        id=r.id, code=r.number or f"ТНД-{r.id}", title=r.route_to or r.cargo or "Тендер",
        subtitle=_route(r.route_from, r.route_to), amount=float(r.awarded_price or 0),
        owner=r.created_by, date=r.deadline or "", next_step=r.office_doc_ref, tags=tags,
    )


@router.get("/rfqs", response_model=list[RfqOut])
async def list_rfqs(session: AsyncSession = Depends(get_session)):
    """Тендеры на перевозку (плоский список)."""
    return (await session.execute(select(CarrierRfq).order_by(CarrierRfq.id.desc()))).scalars().all()


@router.get("/rfqs/board", response_model=FunnelBoardOut)
async def rfqs_board(session: AsyncSession = Depends(get_session)) -> FunnelBoardOut:
    """Воронка тендеров: черновик → разослан → сбор → переговоры → выбран → договор."""
    rows = (await session.execute(select(CarrierRfq))).scalars().all()
    return build_board(TENDER_STAGES, rows, _rfq_card, stage_of=lambda r: r.status)


@router.post("/rfqs", response_model=RfqOut, status_code=201)
async def create_rfq(payload: RfqCreate, session: AsyncSession = Depends(get_session)):
    """Создать тендер на перевозку (наёмный перевозчик по договору)."""
    data = payload.model_dump()
    data["weight_kg"] = Decimal(str(data["weight_kg"]))
    data["declared_value"] = Decimal(str(data["declared_value"]))
    obj = CarrierRfq(**data)
    session.add(obj)
    await session.flush()
    if not obj.number:
        obj.number = f"ТНД-2026-{obj.id:04d}"
    await session.commit()
    await session.refresh(obj)
    return obj


@router.get("/rfqs/{rfq_id}", response_model=RfqOut)
async def get_rfq(rfq_id: int, session: AsyncSession = Depends(get_session)):
    """Тендер по id."""
    return await _get_rfq(session, rfq_id)


@router.post("/rfqs/{rfq_id}/broadcast", response_model=BroadcastOut)
async def broadcast_rfq(
    rfq_id: int,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Разослать тендер пригодным перевозчикам (по параметрам груза → ``fleet``).

    Создаёт приглашения по перевозчикам, чьи парк и допуски подходят под груз, и
    уведомляет их по контакту из реестра (email/telegram/phone; MVP — лог, реальная
    доставка Итерация 1). Каждому приглашению выдаётся ``token`` публичной ссылки на
    подачу ставки (``POST /logistics/rfqs/bid/{token}``). Повторная рассылка не
    дублирует уже созданные приглашения.
    """
    rfq = await _get_rfq(session, rfq_id)
    matches = await _eligible_matches(
        session, weight_kg=float(rfq.weight_kg or 0), category=rfq.category,
        needs_temp=rfq.needs_temp, max_dim_cm=rfq.max_dim_cm, adr=rfq.adr,
    )
    invited = {
        i.carrier_code
        for i in (
            await session.execute(select(CarrierRfqInvite).where(CarrierRfqInvite.rfq_id == rfq_id))
        ).scalars().all()
    }
    contacts = {
        c.code: c.contact for c in (await session.execute(select(Carrier))).scalars().all()
    }
    settings = get_settings()
    carriers: list[str] = []
    notified = 0
    for code, _m in matches:
        carriers.append(code)
        if code in invited:
            continue
        token = uuid.uuid4().hex
        contact = contacts.get(code, "")
        channel = notify.pick_channel(contact)
        message = notify.invite_message(
            rfq.number, rfq.cargo, float(rfq.weight_kg or 0),
            rfq.route_from, rfq.route_to, _carrier_name(code), f"/logistics/rfqs/bid/{token}",
        )
        result = notify.send_invite(channel, contact, message, settings=settings)
        session.add(CarrierRfqInvite(
            rfq_id=rfq_id, carrier_code=code, token=token, channel=result["channel"],
            status="sent" if result["status"] == "sent" else "invited",
            detail=result["detail"], notified_at=datetime.now(),
        ))
        if result["status"] == "sent":
            notified += 1
    rfq.status = "sent"
    core.event_bus.emit(
        session, "logistics.rfq.broadcast",
        {"rfq_id": rfq_id, "number": rfq.number, "carriers": carriers,
         "notified": notified, "entity_ref": rfq.number},
    )
    await session.commit()
    return BroadcastOut(
        rfq_id=rfq_id, status=rfq.status, invited=len(carriers),
        notified=notified, carriers=carriers,
    )


@router.get("/rfqs/{rfq_id}/invites", response_model=list[InviteOut])
async def list_invites(rfq_id: int, session: AsyncSession = Depends(get_session)):
    """Кому разослан тендер."""
    return (
        await session.execute(select(CarrierRfqInvite).where(CarrierRfqInvite.rfq_id == rfq_id))
    ).scalars().all()


@router.post("/rfqs/{rfq_id}/bids", response_model=BidOut, status_code=201)
async def add_bid(rfq_id: int, payload: BidCreate, session: AsyncSession = Depends(get_session)):
    """Зарегистрировать предложение перевозчика (ручной ввод; реальная — Итерация 1)."""
    rfq = await _get_rfq(session, rfq_id)
    obj = CarrierBid(
        rfq_id=rfq_id, carrier_code=payload.carrier_code, price=Decimal(str(payload.price)),
        eta_days=payload.eta_days, vehicle_class=payload.vehicle_class,
        valid_until=payload.valid_until, comment=payload.comment, round=1,
    )
    session.add(obj)
    if rfq.status in ("draft", "sent"):
        rfq.status = "collecting"
    # отметить приглашение откликнувшимся
    inv = (
        await session.execute(
            select(CarrierRfqInvite).where(
                CarrierRfqInvite.rfq_id == rfq_id,
                CarrierRfqInvite.carrier_code == payload.carrier_code,
            )
        )
    ).scalars().first()
    if inv is not None:
        inv.status = "responded"
    await session.commit()
    await session.refresh(obj)
    return BidOut(
        id=obj.id, rfq_id=obj.rfq_id, carrier_code=obj.carrier_code,
        carrier=_carrier_name(obj.carrier_code), price=float(obj.price), eta_days=obj.eta_days,
        vehicle_class=obj.vehicle_class, valid_until=obj.valid_until, comment=obj.comment,
        round=obj.round, is_best=False,
    )


@router.get("/rfqs/{rfq_id}/bids", response_model=list[BidOut])
async def list_bids(rfq_id: int, session: AsyncSession = Depends(get_session)):
    """Предложения по тендеру: сортировка по цене, пометка лучшего."""
    await _get_rfq(session, rfq_id)
    bids = (
        await session.execute(select(CarrierBid).where(CarrierBid.rfq_id == rfq_id))
    ).scalars().all()
    return _bids_with_best(bids)


@router.post("/rfqs/bid/{token}", response_model=BidOut, status_code=201)
async def public_bid(
    token: str, payload: PublicBidCreate, session: AsyncSession = Depends(get_session)
):
    """Публичный приём ставки перевозчика по токену из ссылки-приглашения (без авторизации).

    Перевозчик подаёт предложение сам по ссылке из рассылки — без ручного ввода
    офисом. ``token`` идентифицирует приглашение (тендер + перевозчик); тендер
    переходит в ``collecting``, приглашение — в ``responded``.
    """
    inv = (
        await session.execute(select(CarrierRfqInvite).where(CarrierRfqInvite.token == token))
    ).scalars().first()
    if inv is None:
        raise HTTPException(status_code=404, detail="Приглашение по токену не найдено")
    rfq = await _get_rfq(session, inv.rfq_id)
    obj = CarrierBid(
        rfq_id=inv.rfq_id, carrier_code=inv.carrier_code, price=Decimal(str(payload.price)),
        eta_days=payload.eta_days, vehicle_class=payload.vehicle_class,
        valid_until=payload.valid_until, comment=payload.comment, round=1,
    )
    session.add(obj)
    inv.status = "responded"
    if rfq.status in ("draft", "sent"):
        rfq.status = "collecting"
    await session.commit()
    await session.refresh(obj)
    return BidOut(
        id=obj.id, rfq_id=obj.rfq_id, carrier_code=obj.carrier_code,
        carrier=_carrier_name(obj.carrier_code), price=float(obj.price), eta_days=obj.eta_days,
        vehicle_class=obj.vehicle_class, valid_until=obj.valid_until, comment=obj.comment,
        round=obj.round, is_best=False,
    )


@router.post("/rfqs/{rfq_id}/negotiate", response_model=BidOut, status_code=201)
async def negotiate_rfq(rfq_id: int, payload: NegotiateRequest, session: AsyncSession = Depends(get_session)):
    """Раунд переговоров: новая (сниженная) цена перевозчика → предложение след. раунда."""
    rfq = await _get_rfq(session, rfq_id)
    prev = (
        await session.execute(
            select(CarrierBid).where(
                CarrierBid.rfq_id == rfq_id, CarrierBid.carrier_code == payload.carrier_code
            )
        )
    ).scalars().all()
    if not prev:
        raise HTTPException(status_code=404, detail="Нет предложения этого перевозчика для торга")
    base = max(prev, key=lambda b: b.round)
    obj = CarrierBid(
        rfq_id=rfq_id, carrier_code=payload.carrier_code, price=Decimal(str(payload.new_price)),
        eta_days=base.eta_days, vehicle_class=base.vehicle_class, valid_until=base.valid_until,
        comment=payload.comment, round=base.round + 1,
    )
    session.add(obj)
    rfq.status = "negotiation"
    await session.commit()
    await session.refresh(obj)
    return BidOut(
        id=obj.id, rfq_id=obj.rfq_id, carrier_code=obj.carrier_code,
        carrier=_carrier_name(obj.carrier_code), price=float(obj.price), eta_days=obj.eta_days,
        vehicle_class=obj.vehicle_class, valid_until=obj.valid_until, comment=obj.comment,
        round=obj.round, is_best=False,
    )


@router.post("/rfqs/{rfq_id}/award", response_model=AwardOut)
async def award_rfq(
    rfq_id: int,
    payload: AwardRequest,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Выбрать перевозчика и заключить договор: создаётся отгрузка, тендер закрыт.

    Без ``carrier_code`` выбирается предложение с минимальной ценой. Создаётся
    ``Shipment`` (``assigned``) с выбранным перевозчиком и ценой; тендер переходит
    в ``contracted``; публикуется ``logistics.contract.signed``.
    """
    rfq = await _get_rfq(session, rfq_id)
    bids = (
        await session.execute(select(CarrierBid).where(CarrierBid.rfq_id == rfq_id))
    ).scalars().all()
    if payload.carrier_code:
        bids = [b for b in bids if b.carrier_code == payload.carrier_code]
    if not bids:
        raise HTTPException(status_code=404, detail="Нет предложений для выбора")
    chosen = min(bids, key=lambda b: float(b.price))

    ship = Shipment(
        customer=rfq.office_doc_ref or rfq.number or f"Тендер {rfq_id}",
        cargo=rfq.cargo, weight_kg=rfq.weight_kg or Decimal("0"),
        route_from=rfq.route_from, route_to=rfq.route_to,
        carrier=_carrier_name(chosen.carrier_code), carrier_code=chosen.carrier_code,
        amount=chosen.price, deal_id=rfq.deal_id, status="assigned",
        tracking_status="Договор заключён, ожидаем забор",
    )
    session.add(ship)
    await session.flush()
    if not ship.number:
        ship.number = f"ЛОГ-2026-{ship.id:04d}"

    rfq.status = "contracted"
    rfq.awarded_carrier_code = chosen.carrier_code
    rfq.awarded_price = chosen.price
    rfq.shipment_id = ship.id

    core.event_bus.emit(
        session, "logistics.contract.signed",
        {
            "rfq_id": rfq_id, "rfq_number": rfq.number, "carrier": ship.carrier,
            "carrier_code": chosen.carrier_code, "price": float(chosen.price),
            "shipment_id": ship.id, "shipment_number": ship.number,
            "office_doc_ref": rfq.office_doc_ref, "entity_ref": ship.number,
        },
    )
    await session.commit()
    await session.refresh(ship)
    return AwardOut(
        rfq_id=rfq_id, status=rfq.status, carrier_code=chosen.carrier_code,
        carrier=ship.carrier, price=float(chosen.price),
        shipment_id=ship.id, shipment_number=ship.number,
    )


@router.post("/rfqs/seed", response_model=RfqOut)
async def seed_rfq(session: AsyncSession = Depends(get_session)):
    """Демо-тендер на перевозку с приглашениями и предложениями (идемпотентно по номеру)."""
    existing = (
        await session.execute(
            select(CarrierRfq).where(CarrierRfq.number == seeds.RFQ_DEMO["number"])
        )
    ).scalars().first()
    if existing is not None:
        return existing
    data = dict(seeds.RFQ_DEMO)
    data["weight_kg"] = Decimal(str(data["weight_kg"]))
    data["declared_value"] = Decimal(str(data["declared_value"]))
    rfq = CarrierRfq(**data)
    session.add(rfq)
    await session.flush()
    for code in seeds.RFQ_DEMO_INVITES:
        session.add(CarrierRfqInvite(rfq_id=rfq.id, carrier_code=code, status="responded"))
    for bid in seeds.RFQ_DEMO_BIDS:
        session.add(CarrierBid(
            rfq_id=rfq.id, carrier_code=bid["carrier_code"], price=Decimal(str(bid["price"])),
            eta_days=bid["eta_days"], vehicle_class=bid["vehicle_class"], comment=bid["comment"],
        ))
    await session.commit()
    await session.refresh(rfq)
    return rfq


# --- Аналитика стоимости (cost-insights, «улучшать стоимость постоянно») ------ #
@router.get("/cost-insights", response_model=CostInsightsOut)
async def cost_insights(
    weight_kg: float = 30.0, session: AsyncSession = Depends(get_session)
) -> CostInsightsOut:
    """Сводка «улучшение стоимости» (ТЗ): где возить дешевле (разброс тарифов по зонам
    и самый дешёвый перевозчик на эталонный вес ``weight_kg``), экономия торга по
    заключённым тендерам, сумма к возврату по аудиту счетов и практические рекомендации.

    Считается поверх уже собранных данных (тарифы/ставки/аудит) — без новых таблиц.
    """
    tariffs = (await session.execute(select(CarrierTariff))).scalars().all()
    zone_name = {
        z.code: z.name for z in (await session.execute(select(Zone))).scalars().all()
    }
    by_zone: dict[str, list[dict]] = {}
    for t in tariffs:
        by_zone.setdefault(t.zone_code, []).append({
            "carrier_code": t.carrier_code,
            "carrier": _carrier_name(t.carrier_code),
            "total": pricing.quote_tariff(t, weight_kg)["total"],
        })
    zones = [
        ins for zc in sorted(by_zone)
        if (ins := analytics.zone_cost_insight(zc, zone_name.get(zc, zc), by_zone[zc]))
    ]

    tenders: list[dict] = []
    for r in (await session.execute(select(CarrierRfq))).scalars().all():
        if not r.awarded_price or float(r.awarded_price) <= 0:
            continue   # экономию считаем только по заключённым тендерам
        bids = (
            await session.execute(select(CarrierBid).where(CarrierBid.rfq_id == r.id))
        ).scalars().all()
        ts = analytics.tender_saving(
            r.number, _route(r.route_from, r.route_to),
            _carrier_name(r.awarded_carrier_code),
            [float(b.price) for b in bids if b.price is not None],
        )
        if ts:
            tenders.append(ts)

    audit = (await session.execute(select(FreightAuditLog))).scalars().all()
    to_recover = sum(float(a.variance) for a in audit if float(a.variance) > 0)

    return CostInsightsOut(**analytics.summarize(weight_kg, zones, tenders, to_recover))
