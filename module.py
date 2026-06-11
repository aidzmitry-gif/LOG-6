"""Модуль Logistics (Логистика) — реализация ModuleContract.

Единая точка управления перевозками (§5.7): доставка по РБ/РФ, импорт из Китая
(фабрика → консолидация → таможня → склад), реестр перевозчиков и дашборд.
Связи по шине: заказ из sales → отгрузка; доставлено → закрытие сделки.
AI-координация логистики (log-6) — Итерация 1, в прототипе не активна.
"""
from __future__ import annotations

from core.runtime.contract import ModuleContract, Widget
from core.runtime.core import Core
from modules.logistics import routes
from modules.logistics.events import on_document_posted, on_office_delivery_requested


class LogisticsModule(ModuleContract):
    name = "logistics"
    version = "0.3.0"
    api_prefix = "/logistics"

    def register(self, core: Core) -> None:
        core.include_router(routes.router, prefix=self.api_prefix)
        # межмодульная связь: заказ из sales → отгрузка в logistics (§2.5)
        core.subscribe("sales.document.posted", on_document_posted)
        # офис → логистика: заявка перевозчику (спот → отгрузка, договор → тендер)
        core.subscribe("logistics.delivery.requested", on_office_delivery_requested)
        # виджеты панели владельца (AI Control Tower)
        core.register_widget(Widget("logistics", "Логистика", source="logistics.shipments"))
        core.register_widget(
            Widget("logistics_imports", "Импорт из Китая", source="logistics.imports_board")
        )


def get_module() -> ModuleContract:
    return LogisticsModule()
