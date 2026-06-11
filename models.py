"""ORM-модели модуля Logistics (схема ``logistics.*``)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class Shipment(Base):
    """Доставка по РБ/РФ: получатель, маршрут, перевозчик, груз, стоимость, статус.

    ``status`` ведёт доставку по воронке (см. ``DELIVERY_STAGES``): из ``planned``
    (создаётся по событию ``sales.document.posted``) до ``delivered``. Переход в
    ``delivered`` публикует ``logistics.shipment.delivered`` → закрытие сделки
    (logistics → sales). Стоимость доставки (``amount``) — в BYN.
    """

    __tablename__ = "shipment"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64), default="", server_default="")
    customer: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(255), default="", server_default="")
    route_from: Mapped[str] = mapped_column(String(128), default="", server_default="")
    route_to: Mapped[str] = mapped_column(String(128), default="", server_default="")
    carrier: Mapped[str] = mapped_column(String(128), default="", server_default="")
    carrier_code: Mapped[str] = mapped_column(String(32), default="", server_default="")  # slug из каталога (dpd/autolight/...)
    carrier_order_no: Mapped[str] = mapped_column(String(64), default="", server_default="")  # № заказа/накладной у перевозчика
    cargo: Mapped[str] = mapped_column(String(255), default="", server_default="")
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), server_default="0")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), server_default="0")  # тариф перевозчика (расход на доставку), BYN
    payer: Mapped[str] = mapped_column(String(32), default="компания", server_default="компания")  # кто оплачивает доставку (компания/клиент)
    priority: Mapped[str] = mapped_column(String(32), default="Средний", server_default="Средний")
    owner: Mapped[str] = mapped_column(String(128), default="", server_default="")
    status: Mapped[str] = mapped_column(String(32), default="planned", server_default="planned")
    eta: Mapped[str | None] = mapped_column(String(32))
    tracking_no: Mapped[str] = mapped_column(String(64), default="", server_default="")
    tracking_status: Mapped[str] = mapped_column(String(128), default="", server_default="")  # последний статус от перевозчика
    deal_id: Mapped[int | None] = mapped_column(Integer)
    insight: Mapped[str] = mapped_column(String(400), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImportShipment(Base):
    """Импорт из Китая: фабрика/поставщик, контейнер, маршрут, Incoterms, таможня.

    ``stage`` ведёт поставку по цепочке (см. ``IMPORT_STAGES``): фабрика →
    консолидация → плечо → таможня → склад. Переход в ``customs``/``warehouse``
    публикует информационные события для панели владельца. Себестоимость доставки
    (``amount``) — в BYN; физический приход на склад остаётся за procurement (QC),
    поэтому logistics здесь приход не делает (нет двойного учёта).
    """

    __tablename__ = "import_shipment"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64), default="", server_default="")
    supplier: Mapped[str] = mapped_column(String(255))
    flag: Mapped[str] = mapped_column(String(8), default="🇨🇳", server_default="🇨🇳")
    container_no: Mapped[str] = mapped_column(String(64), default="", server_default="")
    route: Mapped[str] = mapped_column(String(255), default="", server_default="")
    incoterms: Mapped[str] = mapped_column(String(16), default="FOB", server_default="FOB")
    mode: Mapped[str] = mapped_column(String(32), default="море", server_default="море")
    cargo: Mapped[str] = mapped_column(String(255), default="", server_default="")
    qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), server_default="0")
    priority: Mapped[str] = mapped_column(String(32), default="Средний", server_default="Средний")
    owner: Mapped[str] = mapped_column(String(128), default="", server_default="")
    stage: Mapped[str] = mapped_column(String(32), default="factory", server_default="factory")
    customs_status: Mapped[str] = mapped_column(String(64), default="", server_default="")
    eta: Mapped[str | None] = mapped_column(String(32))
    po_ref: Mapped[str] = mapped_column(String(64), default="", server_default="")
    insight: Mapped[str] = mapped_column(String(400), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Carrier(Base):
    """Перевозчик/подрядчик: тип, плечо, способ интеграции, метрики надёжности.

    Метрики (``on_time_pct``, ``avg_days``) питают дашборд логистики (log-8).
    Способ интеграции (``integration``: api/csv/edi/manual) — под коннекторы (log-5);
    в прототипе обмен ручной/файловый, реальные API — Итерация 1+. ``track_url`` —
    шаблон ссылки трекинга ({n} = трек-номер); ``code`` — slug каталога.
    """

    __tablename__ = "carrier"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(32), default="", server_default="")  # slug для связи и сидов (dpd/autolight/...)
    kind: Mapped[str] = mapped_column(String(32), default="РБ", server_default="РБ")
    mode: Mapped[str] = mapped_column(String(32), default="авто", server_default="авто")
    contact: Mapped[str] = mapped_column(String(255), default="", server_default="")
    integration: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    track_url: Mapped[str] = mapped_column(String(255), default="", server_default="")  # шаблон ссылки трекинга, {n} = трек-номер
    on_time_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    shipments_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Zone(Base):
    """Зона доставки по РБ (``z1``..``z4``): покрытие, города, SLA по срокам.

    Зоны — ось прайс-матрицы (``CarrierTariff``): тариф задаётся на пару
    перевозчик×зона. ``cities`` — JSON-список городов зоны (для подсказки
    направления в модалке оформления доставки).
    """

    __tablename__ = "zones"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)          # "z1".."z4"
    name: Mapped[str] = mapped_column(String(128))
    coverage: Mapped[str] = mapped_column(String(255), default="", server_default="")
    cities: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    sla_days_min: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    sla_days_max: Mapped[int] = mapped_column(Integer, default=2, server_default="2")


class CarrierTariff(Base):
    """Прайс-матрица перевозчик×зона: вилки веса (≤5/≤10/≤30 кг) + ставка свыше 30.

    Плюс сборы: забор (``pickup_fee``), наложенный платёж (``cod_pct``, % суммы),
    страховка (``insurance_pct``, % объявленной ценности). Расчёт итоговой
    стоимости — ``pricing.quote_tariff``. ``effective_from`` даёт версионность
    тарифа (новый прайс = новая строка с более поздней датой).
    """

    __tablename__ = "carrier_tariffs"
    __table_args__ = (
        UniqueConstraint("carrier_code", "zone_code", "effective_from", name="uq_tariff"),
        {"schema": "logistics"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_code: Mapped[str] = mapped_column(String(32))             # dpd/autolight/cdek/evropochta/belpost
    zone_code: Mapped[str] = mapped_column(String(8))                 # z1..z4
    price_w5: Mapped[Decimal] = mapped_column(Numeric(10, 2))         # до 5 кг
    price_w10: Mapped[Decimal] = mapped_column(Numeric(10, 2))        # до 10 кг
    price_w30: Mapped[Decimal] = mapped_column(Numeric(10, 2))        # до 30 кг
    over30_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2))    # ставка за кг свыше 30
    pickup_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")
    cod_pct: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("0"), server_default="0")
    insurance_pct: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("0"), server_default="0")
    effective_from: Mapped[date] = mapped_column(Date)


class CarrierScorecard(Base):
    """Оценка перевозчика за период: OTD/OTIF, брак, точность счетов, претензии, балл.

    ``score`` (взвешенный балл) и ``grade`` (A/B/C) — вход для распределения
    объёмов: A приоритетно, C на доработке. ``cost_per_delivery`` — средняя
    стоимость доставки, метрика «постоянно улучшать стоимость» (ТЗ).
    """

    __tablename__ = "carrier_scorecard"
    __table_args__ = (
        UniqueConstraint("carrier_code", "period", name="uq_scorecard"),
        {"schema": "logistics"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_code: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(16))                   # "2026-06"
    otd_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default="0")
    otif_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default="0")
    damage_free_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default="0")
    billing_accuracy_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default="0")
    claims_ratio_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default="0")
    cost_per_delivery: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")
    shipments: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    score: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=Decimal("0"), server_default="0")
    grade: Mapped[str] = mapped_column(String(2), default="C", server_default="C")


class FreightAuditLog(Base):
    """Аудит счёта перевозчика: фактический счёт vs ожидаемый тариф, расхождение.

    При загрузке счёта ``invoice_amount`` сверяется с ``quote_tariff(...)``
    (``expected_amount``); ``variance = invoice - expected``. Положительное
    расхождение → запись к разбору (``status``: open/return/dispute/closed) и
    минус в точность счетов перевозчика (scorecard ``billing_accuracy``).
    """

    __tablename__ = "freight_audit_log"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_code: Mapped[str] = mapped_column(String(64))            # "ЛОГ-2026-0031"
    carrier_code: Mapped[str] = mapped_column(String(32))
    invoice_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))   # фактический счёт
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # тариф из carrier_tariffs
    variance: Mapped[Decimal] = mapped_column(Numeric(10, 2))         # invoice - expected
    reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CarrierVehicle(Base):
    """Машина в парке перевозчика: класс, грузоподъёмность, объём, температурный режим.

    Парк определяет, какой груз перевозчик физически может взять (подбор по весу
    в ``fleet.eligible_carriers``). ``temp_control`` — рефрижератор (термо-груз);
    ``count`` — сколько таких машин (для оценки доступности).
    """

    __tablename__ = "carrier_vehicle"
    __table_args__ = {"schema": "logistics"}

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_code: Mapped[str] = mapped_column(String(32))
    vehicle_class: Mapped[str] = mapped_column(String(64))            # "Газель 1.5т"/"Тент 5т"/"Фура 20т"/…
    capacity_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")
    volume_m3: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), server_default="0")
    temp_control: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # рефрижератор
    count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CarrierCargoCapability(Base):
    """Какие грузы возит перевозчик: категория + ограничения (вес/габарит/ADR).

    Категории прототипа: ``обычный`` / ``АКБ`` / ``опасный_ADR`` / ``хрупкое`` /
    ``негабарит`` / ``температурный``. Лимиты ``0`` = без явного ограничения.
    ``adr`` — допуск к опасным грузам (ДОПОГ). Подбор — ``fleet.eligible_carriers``.
    """

    __tablename__ = "carrier_cargo_capability"
    __table_args__ = (
        UniqueConstraint("carrier_code", "category", name="uq_cargo_capability"),
        {"schema": "logistics"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_code: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32))                 # обычный/АКБ/опасный_ADR/хрупкое/негабарит/температурный
    adr: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")          # допуск к опасным грузам
    oversize: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")     # негабарит
    max_weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")  # 0 = без лимита
    max_dim_cm: Mapped[int] = mapped_column(Integer, default=0, server_default="0")            # макс. габарит, 0 = без лимита
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
