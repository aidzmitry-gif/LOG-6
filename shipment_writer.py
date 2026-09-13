"""Application serialization only; this is not protection against direct SQL."""
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from config.access import is_package_allowed
from core.services.auth import has_permission
from core.services.logistics import ExactInvoiceIdentity
from modules.logistics.models import (
    CarrierRfq,
    RfqInvoiceBinding,
    Shipment,
    ShipmentIntake,
    ShipmentInvoiceBinding,
    ShipmentJournal,
)
from modules.logistics.shipment_bindings import digest


def permission(core, user, action):
    if not is_package_allowed("logistics", user.roles) or not has_permission(
            core, user, "logistics." + action):
        raise HTTPException(403, "Logistics permission required")


def public_source_key(key):
    """HTTP callers cannot claim keys owned by persisted workflow producers."""
    if key.startswith(("execution:", "intake:", "rfq-award:")):
        raise HTTPException(422, "Source key namespace is reserved for internal shipping workflows")


def accounting(core):
    service = core.services.accounting
    if service is None:
        raise HTTPException(503, "Organization access service unavailable")
    return service


async def visible_orgs(session, core, user):
    return [r["id"] for r in await accounting(core).source_organizations(session, user)]


def identity(binding):
    return ExactInvoiceIdentity(organization_id=binding.organization_id,
        document_id=binding.document_id, expected_version=binding.document_version,
        expected_content_sha256=binding.content_sha256)


async def source(session, core, exact, user=None, *, operation="fulfill"):
    exact = ExactInvoiceIdentity.model_validate(exact)
    if user is not None:
        permission(core, user, "shipment.write")
        actor = await accounting(core).source_member(session, exact.organization_id, user)
    else:
        await accounting(core).lock_event_organization(session, exact.organization_id)
        actor = "logistics:persisted-producer"
    gateway = core.services.sales_source
    if gateway is None:
        raise HTTPException(503, "Invoice source unavailable")
    try:
        facts = await gateway.invoice_shipping_source(session, **exact.model_dump(), operation=operation)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if operation == "fulfill" and not facts.get("fulfillment_allowed"):
        raise HTTPException(409, "Invoice does not allow fulfillment")
    return facts, actor


async def gate(session):
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    await session.execute(insert(ShipmentJournal).values(id=1, generation=0).on_conflict_do_nothing())
    return await session.scalar(select(ShipmentJournal).where(ShipmentJournal.id == 1)
        .with_for_update().execution_options(populate_existing=True))


def expose(row, binding):
    row.organization_id = binding.organization_id
    row.invoice_document_id = binding.document_id
    row.invoice_version = binding.document_version
    row.invoice_content_sha256 = binding.content_sha256
    return row


async def rows(session, core, user, *, rfq=False):
    permission(core, user, "shipment.read")
    orgs = await visible_orgs(session, core, user)
    model, binding, pk = (CarrierRfq, RfqInvoiceBinding, "rfq_id") if rfq else (
        Shipment, ShipmentInvoiceBinding, "shipment_id")
    pairs = (await session.execute(select(model, binding).join(binding, model.id == getattr(binding, pk))
        .where(binding.organization_id.in_(orgs)).order_by(model.id.desc()))).all()
    return [expose(row, bound) for row, bound in pairs]


async def read(session, core, user, row_id, *, rfq=False):
    permission(core, user, "shipment.read")
    binding = await session.get(RfqInvoiceBinding if rfq else ShipmentInvoiceBinding, row_id)
    if binding is None or binding.organization_id not in await visible_orgs(session, core, user):
        raise HTTPException(404, "Shipping record not found")
    row = await session.get(CarrierRfq if rfq else Shipment, row_id)
    if row is None:
        raise HTTPException(404, "Shipping record not found")
    return expose(row, binding)


async def locked(session, core, user, row_id, *, rfq=False, allow_delivered=False, allow_award_replay=False):
    await read(session, core, user, row_id, rfq=rfq)
    binding = await session.get(RfqInvoiceBinding if rfq else ShipmentInvoiceBinding, row_id)
    await source(session, core, identity(binding), user)
    journal = await gate(session)
    model = CarrierRfq if rfq else Shipment
    row = await session.scalar(select(model).where(model.id == row_id).with_for_update()
                               .execution_options(populate_existing=True))
    if row is None or row.deal_id != binding.deal_id:
        raise HTTPException(409, "Shipping row attribution changed")
    if row.status == "withdrawn":
        raise HTTPException(409, "Withdrawn fulfillment is terminal")
    if not rfq and row.status == "delivered" and not allow_delivered:
        raise HTTPException(409, "Delivered history is terminal")
    if rfq and (row.shipment_id is not None or row.status in {"awarded", "contracted"}):
        if not (allow_award_replay and row.shipment_id is not None):
            raise HTTPException(409, "Tender already awarded")
    journal.generation += 1
    return expose(row, binding)


async def create(session, core, exact, key, data, user=None, *, rfq=False):
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise HTTPException(422, "Source key required")
    # Replay attributes existing history; it does not request fresh fulfillment.
    facts, actor = await source(session, core, exact, user, operation="historical_claim")
    journal = await gate(session)
    model, binding, pk = (CarrierRfq, RfqInvoiceBinding, "rfq_id") if rfq else (
        Shipment, ShipmentInvoiceBinding, "shipment_id")
    request_digest = digest({"identity": ExactInvoiceIdentity.model_validate(exact).model_dump(), "data": data})
    prior = await session.scalar(select(binding).where(binding.organization_id == facts["organization_id"],
                                                       binding.source_key == key))
    if prior:
        if prior.request_digest != request_digest:
            raise HTTPException(409, "Source key payload conflict")
        return expose(await session.get(model, getattr(prior, pk)), prior)
    # The same organization and document locks remain held across this recheck.
    await source(session, core, exact, user)
    if data.get("deal_id") not in (None, facts["deal_id"]):
        raise HTTPException(409, "Deal and invoice do not match")
    row = model(**{**data, "deal_id": facts["deal_id"]})
    session.add(row)
    await session.flush()
    if not row.number:
        row.number = ("ТНД-2026-" if rfq else "ЛОГ-2026-") + f"{row.id:04d}"
    bound = binding(**{pk: row.id}, organization_id=facts["organization_id"], deal_id=facts["deal_id"],
        document_id=facts["document_id"], document_version=facts["version"], content_sha256=facts["content_sha256"],
        source_key=key, request_digest=request_digest, actor=actor)
    session.add(bound)
    journal.generation += 1
    await session.flush()
    return expose(row, bound)


async def receive(session, services, kind, payload):
    """Missing identity is persisted, never silently accepted as a shipment."""
    if "schema_version" in payload or "source" in payload:
        from modules.logistics.canonical_intakes import receive as canonical_receive
        return await canonical_receive(session, services, kind, payload)
    key = str(payload.get("source_key") or payload.get("entity_ref") or payload.get("log_ref") or digest(payload))
    revision = str(payload.get("source_revision") or payload.get("version") or "unknown")
    if len(key) > 200 or len(revision) > 80:
        raise HTTPException(422, "Producer identity exceeds supported size")
    request_digest = digest(payload)
    # Resolve BEFORE any gate: a producer may need organization/source locks.
    producer = getattr(services, "shipping_producer", None)
    exact = await producer.resolve_shipping_source(session, source_kind=kind, source_key=key,
        source_revision=revision) if producer and revision != "unknown" else None
    from types import SimpleNamespace
    core = SimpleNamespace(services=services)
    if exact:
        await source(session, core, exact, operation="historical_claim")
        current = await producer.resolve_shipping_source(session, source_kind=kind,
            source_key=key, source_revision=revision)
        if current != exact:
            raise HTTPException(409, "Producer association changed")
    await gate(session)
    row = await session.scalar(select(ShipmentIntake).where(ShipmentIntake.source_kind == kind,
        ShipmentIntake.source_key == key, ShipmentIntake.source_revision == revision).with_for_update()
        .execution_options(populate_existing=True))
    created = row is None
    if row:
        if row.request_digest != request_digest or digest(row.snapshot) != request_digest:
            raise HTTPException(409, "Producer source revision payload conflict")
        if row.state == "withdrawn":
            if exact is None:
                raise HTTPException(409, "Withdrawn producer attribution unavailable")
            row.observed_transition = False
            return row
        if row.state == "resolved":
            await resolved_intake_attribution(session, row, exact)
            row.observed_transition = False
            return row
    else:
        if exact:
            await source(session, core, exact)
        row = ShipmentIntake(source_kind=kind, source_key=key, source_revision=revision,
            snapshot=payload, request_digest=request_digest, pending_reason="persisted_source_required")
        session.add(row)
        await session.flush()
    row.observed_transition = created or exact is not None
    if exact:
        await promote(session, core, row, exact)
    return row


async def resolved_intake_attribution(session, intake, exact):
    if exact is None or (intake.shipment_id is None) == (intake.rfq_id is None):
        raise HTTPException(409, "Resolved intake attribution unavailable")
    binding = await session.get(RfqInvoiceBinding if intake.rfq_id is not None else ShipmentInvoiceBinding,
        intake.rfq_id if intake.rfq_id is not None else intake.shipment_id, populate_existing=True)
    if (binding is None or identity(binding) != ExactInvoiceIdentity.model_validate(exact)
            or binding.source_key != "intake:" + str(intake.id)):
        raise HTTPException(409, "Resolved intake attribution changed")


def shipping_number(payload, field, precision):
    """Optional numeric fields retain zero defaults; malformed values never do."""
    raw = payload.get(field)
    if raw is None:
        return Decimal("0")
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = Decimal(str(raw))
        if (not value.is_finite() or value < 0 or value >= 10 ** (precision - 2)
                or value != value.quantize(Decimal("0.01"))):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, f"Invalid shipping {field}: expected nonnegative two-decimal number") from None
    return value


async def promote(session, core, intake, exact, user=None):
    if intake.state != "pending":
        raise HTTPException(409, "Only pending intake can be promoted")
    p = intake.snapshot
    rfq = intake.source_kind == "office" and p.get("mode") == "contract"
    if rfq:
        data = dict(cargo=p.get("title") or p.get("cargo") or "", weight_kg=shipping_number(p, "weight", 12),
            route_to=p.get("region") or "", zone_code=p.get("zone_code") or "",
            declared_value=shipping_number(p, "amount", 14), office_doc_ref=p.get("number") or "", status="draft")
    else:
        data = dict(customer=p.get("company") or p.get("counterparty") or "",
            cargo=p.get("title") or "", weight_kg=shipping_number(p, "weight", 12),
            route_to=p.get("region") or "", carrier=p.get("carrier_name") or "",
            carrier_code=p.get("carrier") or "", status="planned", number=p.get("log_ref") or "")
    row = await create(session, core, exact, "intake:" + str(intake.id), data, user, rfq=rfq)
    intake.rfq_id, intake.shipment_id = (row.id, None) if rfq else (None, row.id)
    intake.state, intake.pending_reason = "resolved", ""
    if rfq:
        core.services.event_bus.emit(session, "logistics.rfq.created", {
            "rfq_id": row.id, "number": row.number, "entity_ref": row.number,
        })
    else:
        core.services.event_bus.emit(session, "logistics.shipment.created", {
            "shipment_id": row.id, "customer": row.customer, "deal_id": row.deal_id,
            "organization_id": row.organization_id, "invoice_document_id": row.invoice_document_id,
            "log_ref": row.number, "entity_ref": f"shipment:{row.id}",
        })
    await session.flush()
    return intake
