"""Canonical producer intake: persisted provenance, shared execution, exact retry."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from core.services.logistics import ExactInvoiceIdentity
from core.services.shipping_payload import (
    ShippingSourceIdentity,
    canonical_shipping_payload,
    shipping_intent_digest,
)
from modules.logistics import shipment_writer as writer
from modules.logistics.models import (
    CarrierRfq,
    RfqInvoiceBinding,
    Shipment,
    ShipmentIntake,
    ShipmentInvoiceBinding,
    ShippingExecution,
)


def _shipment_status(row):
    # Only operational status fields; never return invites, tokens, bids or costs.
    return {"id": row.id, "number": row.number, "status": row.status,
            "carrier_name": row.carrier, "tracking_status": row.tracking_status,
            "tracking_no": row.tracking_no, "eta": row.eta}


async def read_status(session, *, source, payload_sha256, expected_association):
    """One source-authorized informational read; no gate, row locks or writes."""
    try:
        with session.no_autoflush:
            return await _read_status(session, source=source, payload_sha256=payload_sha256,
                                      expected_association=expected_association)
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, "Saved shipping status attribution is invalid") from exc


async def _read_status(session, *, source, payload_sha256, expected_association):
    source = ShippingSourceIdentity.model_validate(source)
    row = await session.scalar(select(ShipmentIntake).where(
        ShipmentIntake.source_kind == source.kind, ShipmentIntake.source_key == source.key,
        ShipmentIntake.source_revision == source.revision,
    ).execution_options(populate_existing=True))
    if row is None:
        return {"state": "not_received", "intake_id": None, "pending_reason": "",
                "target": None, "shipment": None}
    model, computed = canonical_shipping_payload(row.snapshot)
    if model.source != source or computed != payload_sha256 or row.request_digest != computed:
        raise HTTPException(409, "Shipping intake payload differs from its source")
    result = {"state": row.state, "intake_id": row.id, "pending_reason": row.pending_reason,
              "target": None, "shipment": None}
    if row.state in {"pending", "withdrawn"}:
        if row.shipment_id is not None or row.rfq_id is not None:
            raise HTTPException(409, "Unresolved intake already names a target")
        return result
    if row.state != "resolved" or expected_association is None:
        raise HTTPException(409, "Resolved intake has no verified source association")
    exact = ExactInvoiceIdentity.model_validate(expected_association["exact_invoice"])
    execution_id = expected_association["execution_id"]
    if str(UUID(execution_id)) != execution_id:
        raise ValueError("Invalid execution identity")
    execution = await session.get(ShippingExecution, execution_id, populate_existing=True)
    intent_digest = shipping_intent_digest(exact, model.intent)
    if (execution is None or execution.organization_id != exact.organization_id
            or execution.document_id != exact.document_id or execution.document_version != exact.expected_version
            or execution.content_sha256 != exact.expected_content_sha256
            or execution.intent != model.intent.model_dump() or execution.intent_digest != intent_digest
            or expected_association["intent_digest"] != intent_digest):
        raise HTTPException(409, "Shipping execution differs from the Office association")
    contract = model.intent.mode == "contract"
    target_id = row.rfq_id if contract else row.shipment_id
    if target_id is None or (row.shipment_id if contract else row.rfq_id) is not None:
        raise HTTPException(409, "Resolved intake target does not match its mode")
    binding_type, target_type = (RfqInvoiceBinding, CarrierRfq) if contract else (ShipmentInvoiceBinding, Shipment)
    binding = await session.get(binding_type, target_id, populate_existing=True)
    target = await session.get(target_type, target_id, populate_existing=True)
    if (binding is None or target is None or writer.identity(binding) != exact
            or binding.source_key != "execution:" + execution_id):
        raise HTTPException(409, "Shipping target binding differs from the Office association")
    result["target"] = {"kind": "rfq" if contract else "shipment", "id": target.id,
                        "number": target.number, "status": target.status}
    if not contract:
        result["shipment"] = _shipment_status(target)
    elif target.shipment_id is not None:
        awarded = await session.get(Shipment, target.shipment_id, populate_existing=True)
        awarded_binding = await session.get(ShipmentInvoiceBinding, target.shipment_id, populate_existing=True)
        if (awarded is None or awarded_binding is None or writer.identity(awarded_binding) != exact
                or awarded_binding.source_key != f"rfq-award:{target.id}"):
            raise HTTPException(409, "Awarded shipment differs from its RFQ and invoice")
        result["shipment"] = _shipment_status(awarded)
    return result


async def receive(session, services, kind, payload, *, user=None, core=None, expected_intake_id=None):
    try:
        model, payload_hash = canonical_shipping_payload(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(422, "Invalid canonical shipping payload") from exc
    if model.source.kind != kind:
        raise HTTPException(409, "Producer kind changed")
    lookup = dict(source_kind=kind, source_key=model.source.key, source_revision=model.source.revision)
    if expected_intake_id is not None:
        original = await session.get(ShipmentIntake, expected_intake_id)
        if (original is None or any(getattr(original, name) != value for name, value in lookup.items())
                or original.request_digest != payload_hash):
            raise HTTPException(409, "Requested intake identity or snapshot changed")
    access = dict(source_kind=kind, source_key=model.source.key, user=user)
    producer = services.shipping_producer
    core = core or SimpleNamespace(services=services)
    if user is not None:
        if producer is None:
            raise HTTPException(503, "Shipping producer unavailable")
        await producer.authorize_shipping_intake(session, **access)
    exact = await producer.resolve_shipping_source(session, **lookup) if producer else None
    verified = None
    if exact is not None:
        exact = ExactInvoiceIdentity.model_validate(exact)
        await writer.source(session, core, exact, user, operation="historical_claim")
        if user is not None:
            await producer.authorize_shipping_intake(session, **access)
        verified = await producer.verify_shipping_source(session, **lookup, actual_payload_hash=payload_hash, user=user)
        if (not verified or ExactInvoiceIdentity.model_validate(verified["exact_invoice"]) != exact
                or verified["payload_sha256"] != payload_hash
                or type(verified.get("fulfillment_allowed")) is not bool):
            raise HTTPException(409, "Producer association or payload changed")
    await writer.gate(session)
    intake = await session.scalar(select(ShipmentIntake).where(
        ShipmentIntake.source_kind == kind, ShipmentIntake.source_key == model.source.key,
        ShipmentIntake.source_revision == model.source.revision,
    ).with_for_update().execution_options(populate_existing=True))
    created = intake is None
    if expected_intake_id is not None and (intake is None or intake.id != expected_intake_id):
        raise HTTPException(409, "Requested intake changed while waiting")
    if intake is not None:
        if (intake.request_digest != payload_hash
                or canonical_shipping_payload(intake.snapshot)[1] != payload_hash):
            raise HTTPException(409, "Producer source revision payload conflict")
    else:
        if verified is not None:
            if verified["fulfillment_allowed"] is not True:
                raise HTTPException(409, "Producer source is not eligible for new fulfillment")
            await writer.source(session, core, exact, user)
        intake = ShipmentIntake(**lookup, request_digest=payload_hash, snapshot=model.model_dump(),
                                pending_reason="persisted_source_required")
        session.add(intake)
        await session.flush()
    intake.observed_transition = created
    if verified is None:
        if intake.state in {"resolved", "withdrawn"}:
            raise HTTPException(409, "Resolved producer association unavailable")
        return intake
    execution = await session.get(ShippingExecution, verified["execution_id"], populate_existing=True)
    intent_digest = shipping_intent_digest(exact, model.intent)
    if (execution is None or execution.organization_id != exact.organization_id
            or execution.document_id != exact.document_id or execution.document_version != exact.expected_version
            or execution.content_sha256 != exact.expected_content_sha256
            or execution.intent_digest != intent_digest or verified["intent_digest"] != intent_digest
            or execution.intent != model.intent.model_dump()):
        raise HTTPException(409, "Persisted shipping execution changed")
    rfq = model.intent.mode == "contract"
    binding, target, pk = (RfqInvoiceBinding, CarrierRfq, "rfq_id") if rfq else (ShipmentInvoiceBinding, Shipment, "shipment_id")
    key = "execution:" + execution.id
    prior = await session.scalar(select(binding).where(binding.organization_id == exact.organization_id,
                                                       binding.source_key == key))
    if intake.state == "resolved":
        if prior is None or getattr(intake, pk) != getattr(prior, pk) or writer.identity(prior) != exact:
            raise HTTPException(409, "Resolved intake attribution changed")
        return intake
    if intake.state == "withdrawn":
        if user is not None:
            raise HTTPException(409, "Withdrawn intake is terminal")
        intake.observed_transition = False
        return intake
    if intake.state != "pending":
        raise HTTPException(409, "Intake is not eligible for promotion")
    if verified["fulfillment_allowed"] is not True:
        raise HTTPException(409, "Producer source is not eligible for new fulfillment")
    await writer.source(session, core, exact, user)
    intent = model.intent
    data = dict(cargo=intent.cargo, weight_kg=Decimal(intent.weight_kg),
                route_from=intent.route_from, route_to=intent.route_to, number="EXEC-" + execution.id)
    if rfq:
        data.update(zone_code=intent.zone_code, pickup_date=intent.pickup_date,
                    declared_value=Decimal(intent.declared_value_byn), status="draft")
    else:
        data.update(customer=intent.customer, address=intent.address, carrier=intent.carrier_name,
                    carrier_code=intent.carrier_code, status="planned")
    row = await writer.create(session, core, exact, key, data, user, rfq=rfq)
    intake.rfq_id, intake.shipment_id = (row.id, None) if rfq else (None, row.id)
    intake.state, intake.pending_reason, intake.observed_transition = "resolved", "", True
    if prior is None:
        services.event_bus.emit(session, "logistics.rfq.created" if rfq else "logistics.shipment.created", {
            pk: row.id, "execution_id": execution.id, "organization_id": exact.organization_id,
            "invoice_document_id": exact.document_id, "number": row.number,
            "entity_ref": ("rfq:" if rfq else "shipment:") + str(row.id),
        })
    await session.flush()
    return intake
