"""Internal caller-authorized logistics snapshot; no no-shipment assertion."""
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from core.services.logistics import (
    ExactInvoiceIdentity,
    ProducerSourcesSnapshotV1,
    snapshot_hash,
    snapshot_row,
)
from core.services.shipping_payload import ShippingIntent, shipping_intent_digest
from modules.logistics.models import (
    CarrierRfq,
    Shipment,
    ShipmentIntake,
    ShipmentInvoiceBinding,
    ShippingExecution,
)
from modules.logistics.shipment_bindings import digest
from modules.logistics.shipment_writer import gate


class CancelReceiptIdentity(BaseModel):
    """Address of caller-owned durable receipt, never an authorization proof."""
    model_config = ConfigDict(strict=True, extra="forbid")
    exact_invoice: ExactInvoiceIdentity
    cancellation_receipt_id: str
    cancellation_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("cancellation_receipt_id")
    @classmethod
    def canonical_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Canonical cancellation UUID required")
        return value


def withdrawable(snapshot):
    for record in snapshot["shipments"]:
        row = record["row"]
        # Preselection may be only a preference, but no persisted distinction
        # proves that here. Do not pretend this subset is complete cancel-ready.
        if (row["status"] != "planned" or any(row[key] for key in (
                "carrier", "carrier_code", "carrier_order_no", "tracking_no", "tracking_status", "eta"))
                or row["amount"] != "0.00"):
            raise HTTPException(409, "Shipment requires separate carrier/fulfillment review")
    if snapshot["bids"] or snapshot["invites"]:
        raise HTTPException(409, "Tender engagement requires separate review")
    for record in snapshot["rfqs"]:
        row = record["row"]
        if (row["status"] != "draft" or row["shipment_id"] is not None
                or row["awarded_carrier_code"] or row["awarded_price"] != "0.00"):
            raise HTTPException(409, "Tender is not an unexecuted draft")
    for record in snapshot["intakes"]:
        row = record["row"]
        if row["state"] not in {"pending", "resolved"} or (row["state"] == "pending" and (
                row["shipment_id"] is not None or row["rfq_id"] is not None)):
            raise HTTPException(409, "Intake is not eligible for withdrawal")


class ShipmentGateway:
    async def office_shipping_projection_context(self, session, *, intake_id=None, rfq_id=None, shipment_id=None):
        from sqlalchemy import or_

        from core.services.shipping_payload import canonical_shipping_payload
        from modules.logistics.canonical_intakes import _shipment_status

        selectors = [value for value in (intake_id, rfq_id, shipment_id) if value is not None]
        if len(selectors) != 1 or type(selectors[0]) is not int or selectors[0] <= 0:
            raise HTTPException(422, "One positive saved shipping ID is required")
        with session.no_autoflush:
            shipment = None
            query = select(ShipmentIntake).where(ShipmentIntake.source_kind == "office")
            if intake_id is not None:
                anchor = await session.get(ShipmentIntake, intake_id, populate_existing=True)
                if anchor is None:
                    raise HTTPException(409, "Saved shipping intake is missing")
                if "schema_version" not in anchor.snapshot and "source" not in anchor.snapshot:
                    return {"sources": [], "shipment": None}
                model, hashed = canonical_shipping_payload(anchor.snapshot)
                if (model.source.model_dump() != {"kind": anchor.source_kind, "key": anchor.source_key,
                        "revision": anchor.source_revision} or hashed != anchor.request_digest):
                    raise HTTPException(409, "Saved intake anchor attribution changed")
                if anchor.state == "resolved":
                    if (anchor.shipment_id is None) == (anchor.rfq_id is None):
                        raise HTTPException(409, "Resolved intake must name one saved target")
                    query = query.where(ShipmentIntake.rfq_id == anchor.rfq_id) if anchor.rfq_id is not None else query.where(
                        ShipmentIntake.shipment_id == anchor.shipment_id)
                else:
                    if anchor.shipment_id is not None or anchor.rfq_id is not None:
                        raise HTTPException(409, "Unresolved intake already names a target")
                    query = query.where(ShipmentIntake.id == intake_id)
            elif rfq_id is not None:
                if await session.get(CarrierRfq, rfq_id, populate_existing=True) is None:
                    raise HTTPException(409, "Saved RFQ is missing")
                query = query.where(ShipmentIntake.rfq_id == rfq_id)
            else:
                row = await session.get(Shipment, shipment_id, populate_existing=True)
                if row is None:
                    raise HTTPException(409, "Saved shipment is missing")
                shipment = _shipment_status(row)
                awarded = select(CarrierRfq.id).where(CarrierRfq.shipment_id == shipment_id)
                query = query.where(or_(ShipmentIntake.shipment_id == shipment_id, ShipmentIntake.rfq_id.in_(awarded)))
            sources = []
            for row in await session.scalars(query.order_by(ShipmentIntake.id).execution_options(populate_existing=True)):
                if "schema_version" not in row.snapshot and "source" not in row.snapshot:
                    continue  # Legacy noncanonical Office records retain their existing guards.
                model, hashed = canonical_shipping_payload(row.snapshot)
                source = {"kind": row.source_kind, "key": row.source_key, "revision": row.source_revision}
                if model.source.model_dump() != source or row.request_digest != hashed:
                    raise HTTPException(409, "Saved Office intake attribution changed")
                sources.append({"intake_id": row.id, "source": source, "payload_sha256": hashed})
            return {"sources": sources, "shipment": shipment}

    async def shipping_source_status(self, session, *, source, payload_sha256, expected_association):
        from modules.logistics.canonical_intakes import read_status

        return await read_status(session, source=source, payload_sha256=payload_sha256,
                                 expected_association=expected_association)

    def __init__(self, services=None):
        self.services = services

    def assert_unexecuted_invoice(self, snapshot):
        """Classify a caller-collected snapshot; not authorization or freshness proof."""
        withdrawable(snapshot)

    async def withdraw_unexecuted_invoice(self, session, *, prepared, current_snapshot,
                                         expected_digest, cancel_receipt_identity):
        """Internal app-only mutation. Caller finalizes receipt/cancel in same TX.

        No business writes between preparation, collection and withdrawal.
        No source callbacks, commit, external carrier stop or completeness proof.
        Receipt replay belongs to Sales; terminal rows alone cannot prove it.
        """
        from modules.logistics.fulfillment_snapshot import (
            PreparedInvoiceSnapshot,
            _collect_locked_snapshot,
            clean_root_transaction,
        )

        clean_root_transaction(session)
        transaction = session.get_transaction()
        if (not isinstance(prepared, PreparedInvoiceSnapshot) or prepared.session is not session
                or transaction is None or transaction is not prepared.transaction
                or not transaction.is_active or not prepared._consumed):
            raise HTTPException(409, "Withdrawal requires collected snapshot in original transaction")
        consumed = session.info.setdefault("logistics.withdrawal_contexts", [])
        consumed[:] = [(ctx, tx) for ctx, tx in consumed if tx is transaction]
        if any(ctx is prepared for ctx, _ in consumed):
            raise HTTPException(409, "Withdrawal context already consumed")
        receipt = CancelReceiptIdentity.model_validate(cancel_receipt_identity)
        sources = ProducerSourcesSnapshotV1.model_validate_json(prepared.sources_json)
        if receipt.exact_invoice != sources.exact_invoice:
            raise HTTPException(409, "Cancellation receipt invoice changed")
        fresh = await _collect_locked_snapshot(session, sources)
        if fresh != current_snapshot or type(expected_digest) is not str or fresh["sha256"] != expected_digest:
            raise HTTPException(409, "Fulfillment snapshot changed before withdrawal")
        withdrawable(fresh)
        consumed.append((prepared, transaction))  # burn before first business mutation
        changes = []
        for section, model in (("shipments", Shipment), ("rfqs", CarrierRfq), ("intakes", ShipmentIntake)):
            for record in fresh[section]:
                previous = record["row"]
                if section == "intakes" and previous["state"] == "resolved":
                    continue
                row = await session.get(model, previous["id"])
                if section == "intakes":
                    row.state = "withdrawn"
                    row.pending_reason = "invoice_cancelled:" + receipt.cancellation_receipt_id
                else:
                    row.status = "withdrawn"
                changes.append({"kind": section, "id": row.id, "before_sha256": record["row_sha256"],
                    "after_sha256": snapshot_hash(snapshot_row(row))})
        if changes:
            # Reuse already-held gate. Caller rolls back every change on failure.
            journal = await gate(session)
            journal.generation += 1
        await session.flush()
        body = {"schema_version": 1, "exact_invoice": sources.exact_invoice.model_dump(),
            "cancel_receipt_identity": receipt.model_dump(), "before_snapshot_sha256": expected_digest,
            "changes": changes, "resolved_intake_ids_preserved": [r["row"]["id"] for r in fresh["intakes"]
                if r["row"]["state"] == "resolved"], "enforcement": "application_only", "coverage_complete": False,
            "coverage_gaps": [*fresh["coverage_gaps"], "carrier_preselection_requires_separate_review"]}
        return {**body, "sha256": snapshot_hash(body)}

    async def prepare_invoice_fulfillment_snapshot(self, session, *, exact_invoice, user):
        from modules.logistics.fulfillment_snapshot import prepare_invoice_fulfillment_snapshot

        return await prepare_invoice_fulfillment_snapshot(
            session, self.services, exact_invoice=exact_invoice, user=user)

    async def collect_prepared_invoice_fulfillment_snapshot(self, session, prepared):
        from modules.logistics.fulfillment_snapshot import (
            collect_prepared_invoice_fulfillment_snapshot,
        )

        return await collect_prepared_invoice_fulfillment_snapshot(session, prepared)

    async def invoice_fulfillment_snapshot(self, session, *, exact_invoice, user):
        from modules.logistics.fulfillment_snapshot import invoice_fulfillment_snapshot

        return await invoice_fulfillment_snapshot(session, self.services, exact_invoice=exact_invoice, user=user)

    async def claim_execution(self, session, *, exact_invoice, intent, expected_digest):
        """Caller owns authorization and org/document/source locks; never commits."""
        if not session.in_transaction():
            raise RuntimeError("Execution claim requires caller transaction and source locks")
        exact = ExactInvoiceIdentity.model_validate(exact_invoice)
        intent = ShippingIntent.model_validate(intent)
        computed = shipping_intent_digest(exact, intent)
        if computed != expected_digest:
            raise HTTPException(409, "Shipping intent digest changed")
        journal = await gate(session)
        row = await session.scalar(select(ShippingExecution).where(
            ShippingExecution.organization_id == exact.organization_id,
            ShippingExecution.document_id == exact.document_id,
            ShippingExecution.document_version == exact.expected_version,
        ).with_for_update().execution_options(populate_existing=True))
        if row is not None:
            if (row.content_sha256 != exact.expected_content_sha256 or row.intent_digest != computed
                    or row.intent != intent.model_dump()):
                raise HTTPException(409, "Invoice already has a different shipping intent")
        else:
            row = ShippingExecution(id=str(uuid4()), organization_id=exact.organization_id,
                document_id=exact.document_id, document_version=exact.expected_version,
                content_sha256=exact.expected_content_sha256, intent=intent.model_dump(), intent_digest=computed)
            session.add(row)
            journal.generation += 1
            await session.flush()
        return {"execution_id": row.id, "intent_digest": row.intent_digest,
                "exact_invoice": exact.model_dump(), "intent": row.intent}

    async def invoice_shipments_snapshot(self, session, source):
        journal = await gate(session)
        pairs = (await session.execute(select(Shipment, ShipmentInvoiceBinding).join(
            ShipmentInvoiceBinding, Shipment.id == ShipmentInvoiceBinding.shipment_id).where(
            ShipmentInvoiceBinding.organization_id == source["organization_id"],
            ShipmentInvoiceBinding.document_id == source["document_id"])
            .order_by(Shipment.id).execution_options(populate_existing=True))).all()
        records = [{"shipment": {col.name: getattr(row, col.name) for col in Shipment.__table__.columns},
                    "binding": {col.name: getattr(binding, col.name)
                                for col in ShipmentInvoiceBinding.__table__.columns}}
                   for row, binding in pairs]
        unresolved = list(await session.scalars(select(Shipment.id).outerjoin(
            ShipmentInvoiceBinding, Shipment.id == ShipmentInvoiceBinding.shipment_id)
            .where(ShipmentInvoiceBinding.shipment_id.is_(None)).order_by(Shipment.id)))
        snapshot = {"identity": source, "generation": journal.generation, "records": records,
                    "unresolved_ids": unresolved, "coverage_complete": False,
                    "enforcement": "application_only"}
        return {**snapshot, "sha256": digest(snapshot)}
