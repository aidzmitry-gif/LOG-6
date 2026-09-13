"""Logistics-owned source identities and application journal helpers."""
import hashlib
import json

from sqlalchemy import event

from modules.logistics.models import RfqInvoiceBinding, ShipmentInvoiceBinding, ShippingExecution


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def immutable(_mapper, _connection, _target):
    raise ValueError("Shipping source bindings are immutable")


for model in (ShipmentInvoiceBinding, RfqInvoiceBinding, ShippingExecution):
    event.listen(model, "before_update", immutable)
    event.listen(model, "before_delete", immutable)
