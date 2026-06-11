"""HTTP-API модуля Logistics. Монтируется под префиксом ``/logistics`` (§5.7).

Две воронки-доски: доставка по РБ/РФ (``/board``) и импорт из Китая
(``/imports/board``), плюс реестр перевозчиков (``/carriers``) и дашборд
(``/dashboard``, log-8). AI-координация (log-6) — Итерация 1, в прототипе поле
``insight`` остаётся пустым (фронт бейджит его как «Итерация 1 · ИИ»).
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.core import Core
from core.runtime.deps import get_core, get_session
from core.runtime.funnel import FunnelBoardOut, FunnelCard, build_board
from modules.logistics import pricing, seeds
from modules.logistics.models import (
    Carrier,
    CarrierScorecard,
    CarrierTariff,
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
    CarrierCatalogItem,
    CarrierCostStat,
    CarrierCreate,
    CarrierOrderRequest,
    CarrierOut,
    CarrierStat,
    CarrierTariffOut,
    CostReportOut,
    DashboardOut,
    ImportShipmentCreate,
    ImportShipmentOut,
    ImportStageUpdate,
    QuoteOut,
    QuoteRequest,
    ScorecardOut,
    ShipmentCreate,
    ShipmentOut,
    StatusUpdate,
    TrackingUpdate,
    ZoneOut,
)
from modules.logistics.stages import DELIVERY_STAGES, IMPORT_STAGES

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
    session: AsyncSession = Depends(get_session),
):
    """Обновить статус трекинга от перевозчика (текстовый статус + ETA + трек-номер)."""
    obj = await session.get(Shipment, shipment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    obj.tracking_status = payload.tracking_status
    if payload.eta is not None:
        obj.eta = payload.eta
    if payload.tracking_no is not None:
        obj.tracking_no = payload.tracking_no
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
