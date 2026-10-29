"""Qualified import-source costs; full tariff/tender/audit coverage stays open."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from core.runtime.deps import get_core, get_session
from core.services.auth import require_permission
from modules.accounting.models import SourceBinding
from modules.logistics.models import ImportShipment


class ImportFreightReview(BaseModel):
    status: Literal["known", "partial", "unknown"]
    qualified_import_count: int = Field(ge=0)
    warehouse_import_count: int = Field(ge=0)
    known_count: int = Field(ge=0)
    unpriced_count: int = Field(ge=0)
    excluded_nonwarehouse_count: int = Field(ge=0)
    known_total: str | None
    total: str | None
    known_average: str | None


class OwnedCostReview(BaseModel):
    version: Literal[1] = 1
    organization_id: int
    principal: str = Field(min_length=1)
    status: Literal["partial"] = "partial"
    complete_cost_coverage: Literal[False] = False
    source: Literal["explicit_own_logistics_imports"] = "explicit_own_logistics_imports"
    posting_status: Literal["not_verified"] = "not_verified"
    unavailable_sections: tuple[Literal["tariffs", "tenders", "audit"], ...] = ("tariffs", "tenders", "audit")
    import_freight: ImportFreightReview


class CostOrganization(BaseModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    unp: str


class CostOrganizations(BaseModel):
    version: Literal[1] = 1
    principal: str = Field(min_length=1)
    items: list[CostOrganization]


@dataclass
class FreightAccumulator:
    qualified: int = 0
    warehouse: int = 0
    known: int = 0
    unpriced: int = 0
    amount: Decimal = Decimal("0.00")

    def add(self, stage, amount):
        self.qualified += 1
        if stage != "warehouse":
            return
        self.warehouse += 1
        # Legacy zero/negative amounts do not establish a free, priced shipment.
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            self.unpriced += 1
            return
        with localcontext() as context:
            context.prec = 50
            self.amount += amount
        self.known += 1

    def result(self):
        with localcontext() as context:
            context.prec = 50
            average = ((self.amount / self.known).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                       if self.known else None)
        return ImportFreightReview(
            status="unknown" if not self.known else "partial" if self.unpriced else "known",
            qualified_import_count=self.qualified, warehouse_import_count=self.warehouse,
            known_count=self.known, unpriced_count=self.unpriced,
            excluded_nonwarehouse_count=self.qualified - self.warehouse,
            known_total=f"{self.amount:.2f}" if self.known else None,
            total=f"{self.amount:.2f}" if self.known and not self.unpriced else None,
            known_average=f"{average:.2f}" if average is not None else None,
        )


router = APIRouter()


def source_access(core, name):
    gateway = getattr(core.services, "accounting", None)
    method = getattr(gateway, name, None)
    if not callable(method):
        raise HTTPException(503, "accounting_source_access_unavailable")
    return method


@router.get("/cost-organizations", response_model=CostOrganizations)
async def cost_organizations(
    response: Response, session=Depends(get_session), core=Depends(get_core),
    user=Depends(require_permission("logistics.shipment.read")),
):
    items = await source_access(core, "source_organizations")(session, user)
    response.headers["Cache-Control"] = "private, no-store"
    return CostOrganizations(principal=user.keycloak_user_id or user.username,
        items=[{**item, "unp": item.get("unp") or ""} for item in items])


@router.get("/organizations/{org_id}/cost-insights", response_model=OwnedCostReview)
async def owned_cost_insights(
    response: Response, org_id: int = Path(gt=0), session=Depends(get_session), core=Depends(get_core),
    user=Depends(require_permission("logistics.shipment.read")),
):
    principal = await source_access(core, "source_member")(session, org_id, user)
    statement = select(ImportShipment.stage, ImportShipment.amount).join(SourceBinding, and_(
        SourceBinding.source_type == "logistics_import", SourceBinding.source_id == ImportShipment.id,
        SourceBinding.organization_id == org_id, SourceBinding.ownership == "own",
    )).execution_options(yield_per=500)
    rows = await session.stream(statement)
    summary = FreightAccumulator()
    try:
        async for stage, amount in rows:
            summary.add(stage, amount)
    finally:
        await rows.close()
    response.headers["Cache-Control"] = "private, no-store"
    return OwnedCostReview(organization_id=org_id, principal=principal, import_freight=summary.result())
