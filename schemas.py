"""Pydantic-схемы модуля Logistics."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ShipmentCreate(BaseModel):
    customer: str
    address: str = ""
    carrier: str = ""
    status: str = "planned"


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer: str
    address: str
    carrier: str
    status: str
