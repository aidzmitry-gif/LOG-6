"""Exact-bound observations under caller transaction; never a completeness proof."""
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from core.services.logistics import (
    ExactInvoiceIdentity,
    ProducerSourcesSnapshotV1,
    snapshot_hash,
    snapshot_row,
)
from core.services.shipping_payload import (
    ShippingIntent,
    canonical_shipping_payload,
    shipping_intent_digest,
)
from modules.logistics.models import (
    CarrierBid,
    CarrierRfq,
    CarrierRfqInvite,
    RfqInvoiceBinding,
    Shipment,
    ShipmentIntake,
    ShipmentInvoiceBinding,
    ShippingExecution,
)
from modules.logistics.shipment_writer import gate


def exact_filter(model, exact):
    return and_(model.organization_id == exact.organization_id, model.document_id == exact.document_id,
        model.document_version == exact.expected_version, model.content_sha256 == exact.expected_content_sha256)


async def rows(session, model, predicate, order):
    return list(await session.scalars(select(model).where(predicate).order_by(order)
        .with_for_update().execution_options(populate_existing=True)))


def record(row, *, omit=()):
    full = snapshot_row(row)
    return {"row": {key: value for key, value in full.items() if key not in omit}, "row_sha256": snapshot_hash(full)}


@dataclass(frozen=True)
class PreparedInvoiceSnapshot:
    """Internal lock context, not a transferable authorization or coverage proof."""

    session: Any
    transaction: Any
    sources_json: str
    _consumed: bool = False


def clean_root_transaction(session):
    if session.in_nested_transaction():
        raise HTTPException(409, "Fulfillment snapshot requires a root transaction")
    if session.new or session.dirty or session.deleted:
        raise HTTPException(409, "Snapshot requires flushed caller changes")


async def prepare_invoice_fulfillment_snapshot(session, services, *, exact_invoice, user):
    """Lock all producer sources before WMS/money reads and the Logistics gate.

    Trusted caller may only read and acquire technical locks before collection.
    Business writes, including flushed/raw SQL writes, invalidate this contract.
    The frozen value stays in-process and must never be accepted from an API.
    """
    clean_root_transaction(session)
    exact = ExactInvoiceIdentity.model_validate(exact_invoice)
    accounting = getattr(services, "accounting", None)
    producer = getattr(services, "shipping_producer", None)
    if accounting is None or not callable(getattr(producer, "invoice_shipping_sources_snapshot", None)):
        raise HTTPException(503, "Fulfillment snapshot adapters unavailable")
    await accounting.source_member(session, exact.organization_id, user)
    sources = ProducerSourcesSnapshotV1.model_validate(await producer.invoice_shipping_sources_snapshot(
        session, exact_invoice=exact.model_dump(), user=user))
    if sources.exact_invoice != exact:
        raise HTTPException(409, "Producer snapshot invoice changed")
    return PreparedInvoiceSnapshot(session, session.get_transaction(), sources.model_dump_json())


async def invoice_fulfillment_snapshot(session, services, *, exact_invoice, user):
    prepared = await prepare_invoice_fulfillment_snapshot(
        session, services, exact_invoice=exact_invoice, user=user)
    return await collect_prepared_invoice_fulfillment_snapshot(session, prepared)


async def collect_prepared_invoice_fulfillment_snapshot(session, prepared):
    """Finish in the original root transaction without source callbacks/locks."""
    clean_root_transaction(session)
    if (not isinstance(prepared, PreparedInvoiceSnapshot) or prepared.session is not session
            or prepared.transaction is None or not prepared.transaction.is_active
            or session.get_transaction() is not prepared.transaction or prepared._consumed):
        raise HTTPException(409, "Prepared fulfillment snapshot transaction expired")
    # Burn before the first await, including failed collection; prepare again to retry.
    object.__setattr__(prepared, "_consumed", True)
    sources = ProducerSourcesSnapshotV1.model_validate_json(prepared.sources_json)
    return await _collect_locked_snapshot(session, sources)


async def _collect_locked_snapshot(session, sources):
    """Internal re-read: caller retains all prepared source locks and gate order."""
    exact = sources.exact_invoice
    await gate(session)
    sections, targets, bindings = {}, {}, {}
    for name, model, binding, pk in (
        ("shipments", Shipment, ShipmentInvoiceBinding, "shipment_id"),
        ("rfqs", CarrierRfq, RfqInvoiceBinding, "rfq_id"),
    ):
        bound = await rows(session, binding, exact_filter(binding, exact), getattr(binding, pk))
        ids = [getattr(item, pk) for item in bound]
        actual = await rows(session, model, model.id.in_(ids), model.id)
        if {item.id for item in actual} != set(ids):
            raise HTTPException(409, "Bound fulfillment target unavailable")
        bindings[name] = {getattr(item, pk): item for item in bound}
        targets[name] = set(ids)
        sections[name] = [{**record(item), "binding": record(bindings[name][item.id])} for item in actual]
    sections["bids"] = [record(item) for item in await rows(
        session, CarrierBid, CarrierBid.rfq_id.in_(targets["rfqs"]), CarrierBid.id)]
    sections["invites"] = [record(item, omit=("token", "detail")) for item in await rows(
        session, CarrierRfqInvite, CarrierRfqInvite.rfq_id.in_(targets["rfqs"]), CarrierRfqInvite.id)]
    executions = await rows(session, ShippingExecution, exact_filter(ShippingExecution, exact), ShippingExecution.id)
    execution_map = {item.id: item for item in executions}
    for item in executions:
        if str(UUID(item.id)) != item.id or shipping_intent_digest(exact, ShippingIntent.model_validate(item.intent)) != item.intent_digest:
            raise HTTPException(409, "Persisted shipping execution changed")
    source_map = {}
    for item in sources.sources:
        execution = execution_map.get(item.execution_id)
        if (execution is None or execution.intent_digest != item.intent_digest
                or execution.intent != item.envelope.payload["intent"]):
            raise HTTPException(409, "Producer execution attribution changed")
        source_map[(item.source.kind, item.source.key, item.source.revision)] = item
    predicates = [and_(ShipmentIntake.source_kind == kind, ShipmentIntake.source_key == key,
        ShipmentIntake.source_revision == revision) for kind, key, revision in source_map]
    predicates.extend((ShipmentIntake.shipment_id.in_(targets["shipments"]), ShipmentIntake.rfq_id.in_(targets["rfqs"])))
    intakes = await rows(session, ShipmentIntake, or_(*predicates), ShipmentIntake.id)
    for item in intakes:
        source = source_map.get((item.source_kind, item.source_key, item.source_revision))
        if source is None:
            raise HTTPException(409, "unverified_intake_attribution")
        if canonical_shipping_payload(item.snapshot)[1] != source.envelope.payload_sha256 or item.request_digest != source.envelope.payload_sha256:
            raise HTTPException(409, "Intake payload changed")
        for name, target_id in (("shipments", item.shipment_id), ("rfqs", item.rfq_id)):
            if target_id is not None and (target_id not in targets[name]
                    or bindings[name][target_id].source_key != "execution:" + source.execution_id):
                raise HTTPException(409, "Intake target attribution changed")
        if item.state == "resolved":
            contract = source.envelope.payload["intent"]["mode"] == "contract"
            if (item.rfq_id is None if contract else item.shipment_id is None) or (item.shipment_id is not None if contract else item.rfq_id is not None):
                raise HTTPException(409, "Resolved intake target changed")
    body = {"schema_version": 1, "exact_invoice": exact.model_dump(),
        **sections, "executions": [record(item) for item in executions], "intakes": [record(item) for item in intakes],
        "producer_sources": sources.model_dump(), "coverage_complete": False,
        "coverage_gaps": ["unassociated_sources_not_attributable", "legacy_history_not_verified"],
        "enforcement": "application_only"}
    return {**body, "sha256": snapshot_hash(body)}
