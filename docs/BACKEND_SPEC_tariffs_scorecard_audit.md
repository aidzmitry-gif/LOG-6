# BACKEND SPEC — Тарифы/зоны, Scorecard перевозчиков, Аудит счетов

Спека для реализации в `LOG-6` (`modules/logistics`). Стиль — как существующие `models.py`/`schemas.py`:
schema `logistics.*`, у всех новых колонок `server_default`, идемпотентные seed-эндпоинты.
Все цифры/тарифы ниже совпадают с эталонным макетом `ui/logistics-module.html` (вкладки «Тарифы и зоны», «Перевозчики», «Расходы»).

---

## 1. Таблица `zones` — зоны доставки по РБ

```python
# models.py
class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = {"schema": "logistics"}

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)        # "z1".."z4"
    name = Column(String, nullable=False)                     # "Минск", "Областные центры", ...
    coverage = Column(String, server_default="")              # описание покрытия
    cities = Column(JSON, server_default="[]")                # ["Гомель","Витебск",...]
    sla_days_min = Column(Integer, server_default="1")
    sla_days_max = Column(Integer, server_default="2")
```

Seed (4 зоны):
| code | name | sla_days | cities (примеры) |
|------|------|----------|------------------|
| z1 | Минск | 1-1 | Минск и Минский район |
| z2 | Областные центры | 1-2 | Гомель, Витебск, Гродно, Брест, Могилёв |
| z3 | Районные центры | 2-3 | Барановичи, Бобруйск, Лида, Молодечно, Орша, Пинск |
| z4 | Отдалённые | 3-4 | малые города, г.п., агрогородки |

`POST /logistics/zones/seed` — идемпотентно по `code`.
`GET /logistics/zones` — список.

---

## 2. Таблица `carrier_tariffs` — прайс-матрица (перевозчик × зона)

```python
class CarrierTariff(Base):
    __tablename__ = "carrier_tariffs"
    __table_args__ = (
        UniqueConstraint("carrier_code", "zone_code", "effective_from", name="uq_tariff"),
        {"schema": "logistics"},
    )
    id = Column(Integer, primary_key=True)
    carrier_code = Column(String, nullable=False)             # "dpd","autolight","cdek","evropochta","belpost"
    zone_code = Column(String, nullable=False)                # "z1".."z4"
    price_w5 = Column(Numeric(10, 2), nullable=False)         # до 5 кг
    price_w10 = Column(Numeric(10, 2), nullable=False)        # до 10 кг
    price_w30 = Column(Numeric(10, 2), nullable=False)        # до 30 кг
    over30_per_kg = Column(Numeric(10, 2), nullable=False)    # ставка за кг свыше 30
    pickup_fee = Column(Numeric(10, 2), server_default="0")   # забор (0 = нет)
    cod_pct = Column(Numeric(5, 3), server_default="0")       # наложка, % суммы
    insurance_pct = Column(Numeric(5, 3), server_default="0") # страховка, % объявленной ценности
    effective_from = Column(Date, nullable=False)             # действует с
```

Seed-значения (BYN, действуют с 2026-06-01) — ровно как в матрице макета:

```
DPD       z1: 9,50/14,00/24,00 + 1,50  забор 5,00  cod 1,5%  ins 0,3%
          z2: 13,00/18,00/28,00 + 1,90 забор 5,00  cod 1,5%  ins 0,3%
          z3: 16,00/22,00/34,00 + 2,30 забор 6,00  cod 1,5%  ins 0,3%
          z4: 20,00/27,00/42,00 + 2,80 забор 7,00  cod 1,5%  ins 0,3%
Автолайт  z1: 7,90/11,50/20,00 + 1,30  забор 4,00  cod 1,2%  ins 0,25%
          z2: 10,50/15,00/24,00 + 1,60 забор 4,00  cod 1,2%  ins 0,25%
          z3: 13,00/18,50/29,00 + 2,00 забор 5,00  cod 1,2%  ins 0,25%
          z4: 16,00/23,00/36,00 + 2,50 забор 6,00  cod 1,2%  ins 0,25%
СДЭК      z1: 8,40/12,50/22,00 + 1,40  забор 4,50  cod 1,5%  ins 0,3%
          z2: 11,00/16,00/26,00 + 1,75 забор 4,50  cod 1,5%  ins 0,3%
          z3: 14,00/20,00/31,00 + 2,20 забор 5,50  cod 1,5%  ins 0,3%
          z4: 17,50/25,00/39,00 + 2,70 забор 6,50  cod 1,5%  ins 0,3%
Европочта z1: 6,50/9,50/16,00 + 1,10   забор —     cod 1,0%  ins 0,2%
          z2: 8,50/12,50/19,50 + 1,40  забор —     cod 1,0%  ins 0,2%
          z3: 10,50/15,00/23,50 + 1,80 забор —     cod 1,0%  ins 0,2%
          z4: 13,00/18,50/29,00 + 2,20 забор —     cod 1,0%  ins 0,2%
Белпочта  z1: 4,20/6,50/12,00 + 0,90   забор —     cod 1,0%  ins 0,15%
          z2: 5,50/8,50/15,00 + 1,15   забор —     cod 1,0%  ins 0,15%
          z3: 7,00/10,50/19,00 + 1,50  забор —     cod 1,0%  ins 0,15%
          z4: 9,00/13,00/24,00 + 1,90  забор —     cod 1,0%  ins 0,15%
```

### Хелпер расчёта (ядро справочника)

```python
def volumetric_weight(l_cm, w_cm, h_cm, divisor=5000):
    return (l_cm * w_cm * h_cm) / divisor

def chargeable_weight(physical_kg, volumetric_kg):
    return max(physical_kg, volumetric_kg)

def quote_tariff(tariff: CarrierTariff, weight_kg: float, *, pickup=False,
                 cod_amount=0.0, declared_value=0.0) -> dict:
    """Возвращает разбивку стоимости доставки для одного перевозчика/зоны."""
    if weight_kg <= 5:    base = tariff.price_w5
    elif weight_kg <= 10: base = tariff.price_w10
    elif weight_kg <= 30: base = tariff.price_w30
    else:                 base = tariff.price_w30 + (weight_kg - 30) * tariff.over30_per_kg
    pickup_fee = tariff.pickup_fee if pickup else 0
    cod_fee = cod_amount * (tariff.cod_pct / 100)
    ins_fee = declared_value * (tariff.insurance_pct / 100)
    total = base + pickup_fee + cod_fee + ins_fee
    return {"base": base, "pickup": pickup_fee, "cod_fee": cod_fee,
            "insurance_fee": ins_fee, "total": round(total, 2)}
```

Проверка консистентности с макетом (пример из окна оформления):
Минск->Гомель (z2), 64 кг, Автолайт -> `24,00 + (64-30)*1,60 = 78,40 BYN`. OK

### Эндпоинты
- `POST /logistics/carrier-tariffs/seed` — идемпотентный сид матрицы.
- `GET /logistics/carrier-tariffs?zone=z2` — выборка.
- `POST /logistics/shipments/{id}/quote` — body `{zone_code, weight_kg, pickup?, cod_amount?, declared_value?}`
  -> возвращает массив котировок по всем перевозчикам (base/total/срок), отсортированный по `total`.
  Это заменяет статичные числа в модалке «Оформление доставки» на расчёт из справочника.

---

## 3. Таблица `carrier_scorecard` — оценка перевозчика (вкладка «Перевозчики»)

```python
class CarrierScorecard(Base):
    __tablename__ = "carrier_scorecard"
    __table_args__ = (
        UniqueConstraint("carrier_code", "period", name="uq_scorecard"),
        {"schema": "logistics"},
    )
    id = Column(Integer, primary_key=True)
    carrier_code = Column(String, nullable=False)
    period = Column(String, nullable=False)                  # "2026-06"
    otd_pct = Column(Numeric(5, 2), server_default="0")      # доставка в срок
    otif_pct = Column(Numeric(5, 2), server_default="0")     # в срок и в полном
    damage_free_pct = Column(Numeric(5, 2), server_default="0")
    billing_accuracy_pct = Column(Numeric(5, 2), server_default="0")
    claims_ratio_pct = Column(Numeric(5, 2), server_default="0")
    cost_per_delivery = Column(Numeric(10, 2), server_default="0")
    shipments = Column(Integer, server_default="0")
    score = Column(Numeric(5, 1), server_default="0")        # взвешенный балл
    grade = Column(String, server_default="C")               # "A"/"B"/"C"
```

Формула балла (как в макете): `score = otd*0.40 + damage_free*0.25 + billing_accuracy*0.20 + (100-claims_ratio)*0.15`.
Грейд: `A` >= 90, `B` >= 83, иначе `C`. Балл — вход для распределения объёмов (A приоритет, C на доработке).

Seed/факт (период 2026-06):
| carrier | otd | damage_free | billing | claims | cost/dost | score | grade |
|---------|-----|-------------|---------|--------|-----------|-------|-------|
| dpd | 96 | 99.1 | 97 | 0.9 | 90.8 | 92 | A |
| autolight | 93 | 98.4 | 95 | 1.6 | 53.3 | 87 | B |
| cdek | 91 | 98.8 | 96 | 1.2 | 60.0 | 85 | B |
| evropochta | 89 | 97.5 | 92 | 2.5 | 21.7 | 79 | C |
| belpost | 84 | 96.9 | 90 | 3.1 | 14.0 | 74 | C |

`GET /logistics/carriers/scorecard?period=2026-06`.

---

## 4. Таблица `freight_audit_log` — аудит счетов (вкладка «Расходы»)

```python
class FreightAuditLog(Base):
    __tablename__ = "freight_audit_log"
    __table_args__ = {"schema": "logistics"}

    id = Column(Integer, primary_key=True)
    shipment_code = Column(String, nullable=False)           # "ЛОГ-2026-0031"
    carrier_code = Column(String, nullable=False)
    invoice_amount = Column(Numeric(10, 2), nullable=False)  # фактический счёт
    expected_amount = Column(Numeric(10, 2), nullable=False) # тариф из carrier_tariffs
    variance = Column(Numeric(10, 2), nullable=False)        # invoice - expected
    reason = Column(String, server_default="")               # "лишний забор", "вес округлён", "неверная зона"
    status = Column(String, server_default="open")           # open/return/dispute/closed
    created_at = Column(DateTime, server_default=func.now())
```

Логика: при загрузке счёта перевозчика сверять `invoice_amount` с `quote_tariff(...)`;
при расхождении создавать запись и плюсовать в `billing_accuracy` перевозчика (scorecard).
Примеры расхождений (из макета): DPD повторный «забор» +5,00; СДЭК вес 30->32 +12,00; Европочта зона 3 вместо 2 +9,00.

`GET /logistics/costs/audit?period=2026-06` -> `{checked, discrepancies, to_recover, items[]}`.

---

## 5. Порядок реализации
1. Модели (§1-4) в `models.py`, схемы в `schemas.py`.
2. `alembic revision --autogenerate -m "logistics: zones, tariffs, scorecard, audit"` -> `upgrade head` (всё через `server_default`, безопасно).
3. Хелпер `quote_tariff` + эндпоинт `/shipments/{id}/quote`.
4. Seed-эндпоинты (идемпотентные) + прогнать сиды.
5. pytest: расчёт тарифа (пример 78,40), грейд scorecard, variance аудита -> coverage >= 90%.
6. Фронт: модалка оформления тянет `/quote`; вкладки Scorecard и Аудит — из соответствующих эндпоинтов.
