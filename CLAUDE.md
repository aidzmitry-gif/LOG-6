# Модуль logistics — контекст для Claude

**Тип:** git submodule → LOG-6 (правка = коммит в этот репозиторий)
**API-префикс:** `/logistics`
**Схема БД:** `logistics`
**Версия:** 0.4.0
**Статус:** наполнен — доставка РБ/РФ + импорт из Китая + реестр перевозчиков + дашборд +
**тарифы/зоны/scorecard/аудит счетов** (Блок 1) + **парк машин/пригодность груза** (Блок 2) +
**тендер на перевозку** (Блок 4) + **сквозная связка с office** (Блок 3) +
**аналитика стоимости** (cost-insights) + **реализм тендера** (токен публичной ссылки на
ставку + журнал рассылки/уведомление перевозчиков).

## Назначение
Единая точка управления перевозками: доставка по РБ/РФ (`Shipment`), импорт из Китая
(`ImportShipment`), реестр перевозчиков (`Carrier`) с тарифами, парком машин и оценкой
(scorecard). Поддерживает два сценария найма перевозчика: **спот** (мгновенный заказ по
тарифу) и **тендер по договору** (рассылка → предложения → торг → договор).

## Файлы
- `module.py` — `LogisticsModule(ModuleContract)` + фабрика `get_module()`; `register()`.
- `models.py` — ORM: `Shipment`, `ImportShipment`, `Carrier`, `Zone`, `CarrierTariff`,
  `CarrierScorecard`, `FreightAuditLog`, `CarrierVehicle`, `CarrierCargoCapability`,
  `CarrierRfq`, `CarrierRfqInvite`, `CarrierBid` (схема `logistics`).
- `pricing.py` — расчёт тарифа (`quote_tariff`, объёмный/оплач. вес) + балл/грейд (`score_carrier`/`grade_for`).
- `fleet.py` — подбор пригодного перевозчика (`carrier_eligible`/`vehicle_fits`/`capability_allows`).
- `analytics.py` — аналитика стоимости (`cost-insights`): разброс тарифов по зонам + самый
  дешёвый перевозчик, экономия торга по тендерам, сумма к возврату по аудиту, рекомендации.
- `notify.py` — уведомление перевозчиков о тендере: выбор канала (email/telegram/phone), текст,
  отправка. Реальная доставка **за конфигом** (SMTP `AIOS_SMTP_*` / Telegram `AIOS_TELEGRAM_BOT_TOKEN`);
  без конфига — MVP-лог. Ошибку отправки фиксирует явно (`status=failed`).
- `seeds.py` — сид-данные (зоны, прайс-матрица, scorecard, аудит, парк машин, демо-тендер).
- `routes.py` — HTTP-API `/logistics/*` (доставка, импорт, перевозчики, тарифы, парк, тендер, дашборд).
- `schemas.py` — Pydantic-схемы всех групп.
- `events.py` — `on_document_posted` (sales) + `on_office_delivery_requested` (office → спот/тендер).
- `stages.py` — `DELIVERY_STAGES`, `IMPORT_STAGES`, `TENDER_STAGES`.
- `ui/logistics-module.html` — макет UI (вкладки: доставка/импорт/перевозчики/тарифы/расходы/парк/тендер).

## Что регистрирует в ядре (register())
- **Роуты:** `routes.router` под префиксом `/logistics`.
- **Подписки:** `sales.document.posted` → `on_document_posted`;
  `logistics.delivery.requested` (от office) → `on_office_delivery_requested`.
- **Widgets:** `logistics` (доставка) + `logistics_imports` (импорт).
- Workflow / permissions / roles / telegram — не регистрирует.

## События
- **Публикует:** `logistics.shipment.created`, `logistics.shipment.delivered` (→ sales),
  `logistics.delivery.tracking` / `logistics.delivery.delivered` (→ office, статус перевозки),
  `logistics.carrier_order.created`, `logistics.import.customs_cleared` / `.arrived`,
  `logistics.rfq.broadcast` / `logistics.rfq.created` / `logistics.contract.signed` (тендер).
- **Подписан на:** `sales.document.posted` (`kind == "order"`),
  `logistics.delivery.requested` (заявка перевозчику из office).

## Модель данных (таблицы схемы `logistics`)
- `shipment` — доставка РБ/РФ (маршрут, перевозчик, груз, вес, тариф, трекинг, `status`).
- `import_shipment` — импорт из Китая (поставщик, контейнер, Incoterms, таможня, `stage`).
- `carrier` — реестр перевозчиков (надёжность, интеграция, трекинг-URL).
- `zones` / `carrier_tariffs` — зоны z1..z4 + прайс-матрица перевозчик×зона (Блок 1).
- `carrier_scorecard` — оценка перевозчика (OTD/брак/счета/претензии → балл, грейд A/B/C).
- `freight_audit_log` — сверка счёта перевозчика с тарифом (variance, к возврату).
- `carrier_vehicle` / `carrier_cargo_capability` — парк машин + допуски по категориям (Блок 2).
- `carrier_rfq` / `carrier_rfq_invite` / `carrier_bid` — тендер: запрос/рассылка/предложения (Блок 4).
  `carrier_rfq_invite` несёт `token` (секрет публичной ссылки на ставку) + `notified_at`/`detail`
  (журнал рассылки) — миграция 0030.
- Связь со сделкой — только по `deal_id` (число, без FK; модули в разных схемах).

## API-эндпоинты (ключевые)
- **Доставка/импорт:** `/shipments(+board, +/carrier-order, +/tracking, +/{id}/quote)`, `/imports(+board)`.
- **Перевозчики:** `/carriers(+catalog, +seed, +scorecard)`, `/carriers/{code}/vehicles`,
  `/carriers/{code}/cargo-capabilities`, `/carriers/eligible` (подбор под груз).
- **Тарифы:** `/zones(+seed)`, `/carrier-tariffs(+seed)`, `/costs(+/audit)`.
- **Тендер:** `POST /rfqs(+seed)`, `/rfqs/{id}/broadcast|bids|negotiate|award`, `GET /rfqs/board`,
  `POST /rfqs/bid/{token}` (публичный приём ставки перевозчиком по ссылке, без авторизации).
- **Дашборд:** `/dashboard`, `/costs`, `/cost-insights` (аналитика «улучшение стоимости»: зоны/тендер/аудит).

## Межмодульные связи и зависимости
- **sales → logistics:** `sales.document.posted` → `Shipment` (`planned`) + `logistics.shipment.created`.
- **logistics → sales:** `logistics.shipment.delivered` → закрытие сделки (won).
- **office → logistics:** `logistics.delivery.requested` → спот-отгрузка (`Shipment`, `number`=log_ref)
  либо тендер (`CarrierRfq`, при `mode=contract`).
- **logistics → office:** `logistics.delivery.tracking` / `.delivered` → офис видит статус перевозки
  на карточке документа (сопоставление по `logistics_ref` = `number` отгрузки).
- Импорты только из `core.*` и `modules.logistics.*`; в чужие модули не лезет — связь через события/`core`.

## Подводные камни / детали
- Роуты `create_shipment` / `update_shipment` **сами вызывают `session.commit()`** (не через
  репозиторий) — отступление от паттерна «репозитории не коммитят».
- `on_document_posted` имеет сигнатуру `(payload, ctx)`: при `ctx is None` или
  `kind != "order"` молча выходит; пишет через `ctx.session`, эмитит через `ctx.services.event_bus`.
- `deal_id` — обычный `Integer` без ForeignKey (модули в разных схемах, связь логическая).
- `module.py` не объявляет роли/права/workflow/telegram — каркас минимален.