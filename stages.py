"""Стадии воронок логистики (порядок списка = порядок колонок на канбане).

Две доски (§5.7):
- ``DELIVERY_STAGES`` — доставка по РБ/РФ (log-1): заявка → перевозчик назначен →
  в пути → доставлено. Статусы совместимы с шиной: ``planned`` (создаётся по
  событию ``sales.document.posted``) и ``delivered`` (закрывает сделку в sales).
- ``IMPORT_STAGES`` — импорт из Китая (log-2, log-4): фабрика → консолидация →
  международное плечо → таможня → приёмка на склад. Соответствует цепочке
  фрахт-форвардинга (export haulage → consolidation → main leg → import customs →
  delivery), см. исследование по freight forwarding.
"""

# Доставка по РБ/РФ (log-1).
DELIVERY_STAGES: list[dict] = [
    {"id": "planned", "title": "Заявка", "color": "#3B82F6"},
    {"id": "assigned", "title": "Перевозчик назначен", "color": "#8B5CF6"},
    {"id": "in_transit", "title": "В пути", "color": "#F59E0B"},
    {"id": "delivered", "title": "Доставлено", "color": "#22C55E"},
]

# Тендер на перевозку (Блок 4): черновик → разослан → сбор предложений →
# переговоры → выбран перевозчик → договор. Наёмный перевозчик по договору.
TENDER_STAGES: list[dict] = [
    {"id": "draft", "title": "Черновик", "color": "#94A3B8"},
    {"id": "sent", "title": "Разослан", "color": "#3B82F6"},
    {"id": "collecting", "title": "Сбор предложений", "color": "#8B5CF6"},
    {"id": "negotiation", "title": "Переговоры", "color": "#F59E0B"},
    {"id": "awarded", "title": "Выбран перевозчик", "color": "#14B8A6"},
    {"id": "contracted", "title": "Договор заключён", "color": "#22C55E"},
]

# Импорт из Китая (log-2, log-4): фабрика → консолидация → плечо → таможня → склад.
IMPORT_STAGES: list[dict] = [
    {"id": "factory", "title": "Фабрика", "color": "#3B82F6"},
    {"id": "consolidation", "title": "Консолидация", "color": "#8B5CF6"},
    {"id": "in_transit", "title": "В пути", "color": "#F59E0B"},
    {"id": "customs", "title": "Таможня", "color": "#EC4899"},
    {"id": "warehouse", "title": "Приёмка на склад", "color": "#22C55E"},
]
