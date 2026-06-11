"""Реакции модуля Logistics на события других модулей (через шину, §2.5)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from modules.logistics.models import CarrierRfq, Shipment


def _to_decimal(value) -> Decimal:
    """Парсинг веса/суммы из payload (office шлёт вес строкой) → Decimal."""
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


async def on_document_posted(payload: dict, ctx) -> None:
    """Заказ проведён в 1С → планируем отгрузку (sales → logistics)."""
    if payload.get("kind") != "order" or ctx is None:
        return
    ctx.session.add(
        Shipment(
            customer=payload.get("counterparty", ""),
            deal_id=payload.get("deal_id"),
            status="planned",
        )
    )
    ctx.services.event_bus.emit(
        ctx.session,
        "logistics.shipment.created",
        {
            "customer": payload.get("counterparty"),
            "deal_id": payload.get("deal_id"),
            "entity_ref": payload.get("entity_ref"),
        },
    )


async def on_office_delivery_requested(payload: dict, ctx) -> None:
    """Офис → логистика: заявка на доставку из карточки документа.

    Ветвление по типу перевозчика (``mode``):
    - ``spot`` (по умолчанию): сразу создаётся ``Shipment`` (planned) с выбранным
      перевозчиком — экспресс по тарифу. ``number`` = ``log_ref`` офиса, чтобы
      обратные tracking-события нашли документ офиса (по ``logistics_ref``).
    - ``contract``: наёмный по договору → заводится тендер ``CarrierRfq`` (draft)
      с параметрами груза для последующей рассылки/торга (Блок 4).
    """
    if ctx is None:
        return
    session = ctx.session
    bus = ctx.services.event_bus
    log_ref = payload.get("log_ref") or payload.get("entity_ref") or ""
    office_ref = payload.get("number") or ""
    region = payload.get("region") or ""

    if payload.get("mode") == "contract":
        rfq = CarrierRfq(
            cargo=payload.get("title") or payload.get("cargo") or "",
            weight_kg=_to_decimal(payload.get("weight")),
            route_to=region, zone_code=payload.get("zone_code") or "",
            declared_value=_to_decimal(payload.get("amount")),
            office_doc_ref=office_ref, created_by=payload.get("owner") or "",
            status="draft",
        )
        session.add(rfq)
        await session.flush()
        if not rfq.number:
            rfq.number = f"ТНД-2026-{rfq.id:04d}"
        bus.emit(session, "logistics.rfq.created", {
            "rfq_id": rfq.id, "number": rfq.number, "office_doc_ref": office_ref,
            "entity_ref": rfq.number,
        })
        return

    ship = Shipment(
        number=log_ref, customer=payload.get("company") or "",
        cargo=payload.get("title") or "", weight_kg=_to_decimal(payload.get("weight")),
        route_to=region, carrier=payload.get("carrier_name") or "",
        carrier_code=payload.get("carrier") or "", deal_id=payload.get("deal_id"),
        status="planned", tracking_status="Заявка принята логистикой",
    )
    session.add(ship)
    await session.flush()
    if not ship.number:
        ship.number = f"ЛОГ-2026-{ship.id:04d}"
    bus.emit(session, "logistics.shipment.created", {
        "customer": ship.customer, "deal_id": ship.deal_id,
        "office_doc_ref": office_ref, "log_ref": ship.number, "entity_ref": ship.number,
    })
