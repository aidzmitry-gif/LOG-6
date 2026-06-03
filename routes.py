"""HTTP-API модуля Logistics. Монтируется под префиксом ``/logistics``."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.deps import get_session
from modules.logistics.models import Shipment
from modules.logistics.schemas import ShipmentCreate, ShipmentOut

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
