"""Модуль Logistics (Логистика) — реализация ModuleContract."""
from __future__ import annotations

from core.runtime.contract import ModuleContract, Widget
from core.runtime.core import Core
from modules.logistics import routes
from modules.logistics.events import on_document_posted


class LogisticsModule(ModuleContract):
    name = "logistics"
    version = "0.1.0"
    api_prefix = "/logistics"

    def register(self, core: Core) -> None:
        core.include_router(routes.router, prefix=self.api_prefix)
        # межмодульная связь: заказ из sales → отгрузка в logistics (§2.5)
        core.subscribe("sales.document.posted", on_document_posted)
        core.register_widget(Widget("logistics", "Логистика", source="logistics.shipments"))


def get_module() -> ModuleContract:
    return LogisticsModule()
