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
from modules.logistics.models import Carrier, ImportShipment, Shipment
from modules.logistics.schemas import (
    CarrierCreate,
    CarrierOut,
    CarrierStat,
    DashboardOut,
    ImportShipmentCreate,
    ImportShipmentOut,
    ImportStageUpdate,
    ShipmentCreate,
    ShipmentOut,
    StatusUpdate,
)
from modules.logistics.stages import DELIVERY_STAGES, IMPORT_STAGES

router = APIRouter(tags=["logistics"])

DELIVERED_STATUS = "delivered"  # доставка завершена → закрытие сделки (logistics → sales)
WAREHOUSE_STAGE = "warehouse"   # импорт принят на склад (информационное событие)
CUSTOMS_STAGE = "customs"


def _route(frm: str, to: str, fallback: str = "") -> str:
    """Маршрут строкой: «Откуда → Куда» (или ``fallback``, если точки не заданы)."""
    parts = [p for p in (frm, to) if p]
    return " → ".join(parts) if parts else fallback


def _qty_tag(qty: int) -> list[str]:
    return [f"{qty} шт"] if qty else []


# --- Карточки воронок ---------------------------------------------------------
def _shipment_card(r: Shipment) -> FunnelCard:
    tags = [t for t in (r.cargo, f"{float(r.weight_kg):g} кг" if r.weight_kg else "") if t]
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


# --- Дашборд логистики (log-8) ------------------------------------------------
@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(session: AsyncSession = Depends(get_session)) -> DashboardOut:
    """KPI логистики: грузы в пути, на таможне, среднее время доставки, OTD, стоимость."""
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

    return DashboardOut(
        in_transit=delivery_in_transit + import_in_transit + at_customs,
        delivery_in_transit=delivery_in_transit,
        import_in_transit=import_in_transit,
        at_customs=at_customs,
        delivered_total=delivered_total,
        avg_delivery_days=avg_delivery_days,
        on_time_pct=on_time_pct,
        logistics_cost=float(cost),
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
    )
