"""Реакции модуля Logistics на события других модулей (через шину, §2.5)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from modules.logistics.models import ImportShipment


def _to_decimal(value) -> Decimal:
    """Парсинг веса/суммы из payload (office шлёт вес строкой) → Decimal."""
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


async def on_document_posted(payload: dict, ctx) -> None:
    if payload.get("kind") != "order" and "schema_version" not in payload and "source" not in payload:
        return
    if ctx is None:
        raise ValueError("Logistics order intake requires event context")
    from modules.logistics.shipment_writer import receive
    intake = await receive(ctx.session, ctx.services, "order", payload)
    # Persisted pending state is observable independently of outbox retries.
    if intake.observed_transition:
        ctx.services.event_bus.emit(ctx.session, "logistics.intake.observed", {
            "intake_id": intake.id, "state": intake.state,
        })


async def on_office_delivery_requested(payload: dict, ctx) -> None:
    if ctx is None:
        raise ValueError("Logistics office intake requires event context")
    from modules.logistics.shipment_writer import receive
    intake = await receive(ctx.session, ctx.services, "office", payload)
    # Persisted pending state is observable independently of outbox retries.
    if intake.observed_transition:
        ctx.services.event_bus.emit(ctx.session, "logistics.intake.observed", {
            "intake_id": intake.id, "state": intake.state,
        })


_DOMESTIC_COUNTRIES = {
    # ponytail: «внутренний рынок» = РБ + РФ (CRM работает по обоим). При появлении
    # отдельной полосы РФ-операций — выделить в config.
    "РБ", "BY", "BLR", "BELARUS", "БЕЛАРУСЬ",
    "РФ", "RU", "RUS", "RUSSIA", "РОССИЯ",
}


def _looks_like_import(payload: dict) -> bool:
    """Эвристика признака импортной закупки в payload procurement.received.

    Маркеры: явный ``origin='import'``, непустой ``incoterms`` (FOB/CIF/EXW/…), либо
    страна явно не из «внутреннего» списка (РБ+РФ, см. ``_DOMESTIC_COUNTRIES``). Без
    маркера логистика НЕ заводит свою ``ImportShipment`` — это была бы пустышка для
    внутренних закупок (засоряла бы импорт-доску). См. LOG3-3.
    """
    # ponytail: маркер импорта по эвристике payload (origin/incoterms/country)
    if str(payload.get("origin") or "").lower() == "import":
        return True
    if str(payload.get("incoterms") or "").strip():
        return True
    country = str(payload.get("country") or "").strip().upper()
    if country and country not in _DOMESTIC_COUNTRIES:
        return True
    return False


async def on_procurement_received(payload: dict, ctx) -> None:
    """Приёмка закупки (procurement → wms) → ИНФО-отметка в импорт-цепочке логистики.

    Логистика лишь ОТРАЖАЕТ факт приёмки для дашборда («приход закупки»):
    - если ``ImportShipment`` по ``po_ref`` УЖЕ есть — двигаем ``stage`` в ``warehouse``
      и эмитим ``logistics.import.received`` (info);
    - если записи НЕТ и payload не несёт import-маркера (см. ``_looks_like_import``) —
      молчим (это была внутренняя закупка, импорт-доске она не интересна, LOG3-3);
    - если записи нет, но маркер есть — заводим ``ImportShipment`` в ``warehouse``
      и эмитим info-эвент с расширенным payload (LOG3-4: freight_amount/eta/supplier).
    НИКАКИХ движений склада/остатков здесь не происходит — приход на склад делает wms
    (он подписан на то же ``procurement.received``, мы не дублируем).
    """
    if ctx is None:
        return
    session = ctx.session
    bus = ctx.services.event_bus
    entity_ref = str(payload.get("entity_ref") or "")
    po_ref = entity_ref if entity_ref.startswith("purchase:") else ""
    if not po_ref:
        return

    obj = (
        await session.execute(select(ImportShipment).where(ImportShipment.po_ref == po_ref))
    ).scalars().first()
    if obj is None:
        if not _looks_like_import(payload):
            return  # LOG3-3: внутренняя закупка без import-маркера — не плодим запись
        obj = ImportShipment(
            supplier=str(payload.get("supplier") or ""),
            cargo=str(payload.get("item") or ""),
            qty=int(payload.get("qty") or 0),
            po_ref=po_ref,
            stage="warehouse",
            customs_status="Принято закупкой",
        )
        session.add(obj)
        await session.flush()
        if not obj.number:
            obj.number = f"ИМП-2026-{obj.id:04d}"
    else:
        obj.stage = "warehouse"
        if not obj.customs_status:
            obj.customs_status = "Принято закупкой"

    # LOG3-4: расширенный payload — freight_amount (str BYN), eta, supplier.
    # M2 (ревью): защита от None.amount у свежесозданной записи — `or Decimal(0)`.
    from decimal import Decimal as _Decimal

    freight_amount = obj.amount if obj.amount is not None else _Decimal("0")
    bus.emit(
        session,
        "logistics.import.received",
        {
            "import_id": obj.id,
            "number": obj.number,
            "po_ref": po_ref,
            "item": payload.get("item"),
            "qty": payload.get("qty"),
            "freight_amount": str(freight_amount),
            "eta": obj.eta,
            "supplier": obj.supplier,
            "entity_ref": obj.number,
        },
    )
