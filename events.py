"""Реакции модуля Logistics на события других модулей (через шину, §2.5)."""
from __future__ import annotations

from modules.logistics.models import Shipment


async def on_document_posted(payload: dict, ctx) -> None:
    """Заказ проведён в 1С → планируем отгрузку (sales → logistics)."""
    if payload.get("kind") != "order" or ctx is None:
        return
    ctx.session.add(Shipment(customer=payload.get("counterparty", ""), status="planned"))
    ctx.services.event_bus.emit(
        ctx.session,
        "logistics.shipment.created",
        {
            "customer": payload.get("counterparty"),
            "deal_id": payload.get("deal_id"),
            "entity_ref": payload.get("entity_ref"),
        },
    )
