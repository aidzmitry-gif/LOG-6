"""HTTP-API модуля Logistics. Монтируется под префиксом ``/logistics``."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.core import Core
from core.runtime.deps import get_core, get_session
from modules.logistics.models import Shipment
from modules.logistics.schemas import ShipmentCreate, ShipmentOut, StatusUpdate

router = APIRouter(tags=["logistics"])


@router.get("/shipments", response_model=list[ShipmentOut])
async def list_shipments(session: AsyncSession = Depends(get_session)):
    """Отгрузки и доставки."""
    return (await session.execute(select(Shipment).order_by(Shipment.id.desc()))).scalars().all()


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
async def create_shipment(payload: ShipmentCreate, session: AsyncSession = Depends(get_session)):
    """Создать отгрузку/доставку."""
    obj = Shipment(**payload.model_dump())
    session.add(obj)
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
    """Сменить статус отгрузки. При ``delivered`` — сделка закрывается успешно (logistics → sales)."""
    obj = await session.get(Shipment, shipment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    obj.status = payload.status
    if payload.status == "delivered":
        core.event_bus.emit(
            session,
            "logistics.shipment.delivered",
            {"deal_id": obj.deal_id, "customer": obj.customer, "entity_ref": f"shipment:{obj.id}"},
        )
    await session.commit()
    await session.refresh(obj)
    return obj
