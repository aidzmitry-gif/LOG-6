"""Реакции модуля Logistics на события других модулей (через шину, §2.5)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from modules.logistics.models import CarrierRfq, ImportShipment, Shipment


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
