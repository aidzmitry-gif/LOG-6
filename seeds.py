"""Сид-данные тарифов/зон/scorecard/аудита (BYN, прототип, ТЗ BACKEND_SPEC).

Цифры совпадают с эталонным макетом ``ui/logistics-module.html`` (вкладки «Тарифы
и зоны», «Перевозчики», «Расходы»). Сиды идемпотентны (см. эндпоинты ``*/seed``).
Перевозчики: dpd / autolight / cdek / evropochta / belpost (= ``CARRIERS_RB``).
"""
from __future__ import annotations

from datetime import date

TARIFF_EFFECTIVE_FROM = date(2026, 6, 1)
SCORECARD_PERIOD = "2026-06"

# --- Зоны доставки по РБ (z1..z4) ------------------------------------------- #
ZONES_SEED: list[dict] = [
    {"code": "z1", "name": "Минск", "coverage": "Минск и Минский район",
     "cities": ["Минск", "Минский район"], "sla_days_min": 1, "sla_days_max": 1},
    {"code": "z2", "name": "Областные центры", "coverage": "Областные центры РБ",
     "cities": ["Гомель", "Витебск", "Гродно", "Брест", "Могилёв"], "sla_days_min": 1, "sla_days_max": 2},
    {"code": "z3", "name": "Районные центры", "coverage": "Районные центры",
     "cities": ["Барановичи", "Бобруйск", "Лида", "Молодечно", "Орша", "Пинск"], "sla_days_min": 2, "sla_days_max": 3},
    {"code": "z4", "name": "Отдалённые", "coverage": "Малые города, г.п., агрогородки",
     "cities": ["малые города", "г.п.", "агрогородки"], "sla_days_min": 3, "sla_days_max": 4},
]

# --- Прайс-матрица перевозчик×зона (BYN) ------------------------------------ #
# (w5, w10, w30, over30_per_kg, pickup_fee, cod_pct, insurance_pct) по зонам z1..z4.
_TARIFF_RAW: dict[str, list[tuple]] = {
    "dpd": [
        (9.50, 14.00, 24.00, 1.50, 5.00, 1.5, 0.3),
        (13.00, 18.00, 28.00, 1.90, 5.00, 1.5, 0.3),
        (16.00, 22.00, 34.00, 2.30, 6.00, 1.5, 0.3),
        (20.00, 27.00, 42.00, 2.80, 7.00, 1.5, 0.3),
    ],
    "autolight": [
        (7.90, 11.50, 20.00, 1.30, 4.00, 1.2, 0.25),
        (10.50, 15.00, 24.00, 1.60, 4.00, 1.2, 0.25),
        (13.00, 18.50, 29.00, 2.00, 5.00, 1.2, 0.25),
        (16.00, 23.00, 36.00, 2.50, 6.00, 1.2, 0.25),
    ],
    "cdek": [
        (8.40, 12.50, 22.00, 1.40, 4.50, 1.5, 0.3),
        (11.00, 16.00, 26.00, 1.75, 4.50, 1.5, 0.3),
        (14.00, 20.00, 31.00, 2.20, 5.50, 1.5, 0.3),
        (17.50, 25.00, 39.00, 2.70, 6.50, 1.5, 0.3),
    ],
    "evropochta": [
        (6.50, 9.50, 16.00, 1.10, 0.00, 1.0, 0.2),
        (8.50, 12.50, 19.50, 1.40, 0.00, 1.0, 0.2),
        (10.50, 15.00, 23.50, 1.80, 0.00, 1.0, 0.2),
        (13.00, 18.50, 29.00, 2.20, 0.00, 1.0, 0.2),
    ],
    "belpost": [
        (4.20, 6.50, 12.00, 0.90, 0.00, 1.0, 0.15),
        (5.50, 8.50, 15.00, 1.15, 0.00, 1.0, 0.15),
        (7.00, 10.50, 19.00, 1.50, 0.00, 1.0, 0.15),
        (9.00, 13.00, 24.00, 1.90, 0.00, 1.0, 0.15),
    ],
}

_ZONE_CODES = ["z1", "z2", "z3", "z4"]


def _tariffs_seed() -> list[dict]:
    rows: list[dict] = []
    for carrier_code, by_zone in _TARIFF_RAW.items():
        for zone_code, t in zip(_ZONE_CODES, by_zone):
            rows.append({
                "carrier_code": carrier_code, "zone_code": zone_code,
                "price_w5": t[0], "price_w10": t[1], "price_w30": t[2], "over30_per_kg": t[3],
                "pickup_fee": t[4], "cod_pct": t[5], "insurance_pct": t[6],
                "effective_from": TARIFF_EFFECTIVE_FROM,
            })
    return rows


TARIFFS_SEED: list[dict] = _tariffs_seed()

# --- Scorecard перевозчиков (период 2026-06) -------------------------------- #
# (otd, damage_free, billing, claims, cost_per_delivery, shipments, score, grade)
SCORECARD_SEED: list[dict] = [
    {"carrier_code": "dpd", "period": SCORECARD_PERIOD, "otd_pct": 96, "otif_pct": 95,
     "damage_free_pct": 99.1, "billing_accuracy_pct": 97, "claims_ratio_pct": 0.9,
     "cost_per_delivery": 90.8, "shipments": 142, "score": 92, "grade": "A"},
    {"carrier_code": "autolight", "period": SCORECARD_PERIOD, "otd_pct": 93, "otif_pct": 91,
     "damage_free_pct": 98.4, "billing_accuracy_pct": 95, "claims_ratio_pct": 1.6,
     "cost_per_delivery": 53.3, "shipments": 218, "score": 87, "grade": "B"},
    {"carrier_code": "cdek", "period": SCORECARD_PERIOD, "otd_pct": 91, "otif_pct": 89,
     "damage_free_pct": 98.8, "billing_accuracy_pct": 96, "claims_ratio_pct": 1.2,
     "cost_per_delivery": 60.0, "shipments": 167, "score": 85, "grade": "B"},
    {"carrier_code": "evropochta", "period": SCORECARD_PERIOD, "otd_pct": 89, "otif_pct": 86,
     "damage_free_pct": 97.5, "billing_accuracy_pct": 92, "claims_ratio_pct": 2.5,
     "cost_per_delivery": 21.7, "shipments": 304, "score": 79, "grade": "C"},
    {"carrier_code": "belpost", "period": SCORECARD_PERIOD, "otd_pct": 84, "otif_pct": 80,
     "damage_free_pct": 96.9, "billing_accuracy_pct": 90, "claims_ratio_pct": 3.1,
     "cost_per_delivery": 14.0, "shipments": 411, "score": 74, "grade": "C"},
]

# --- Аудит счетов перевозчиков (демо-расхождения, период 2026-06) ----------- #
AUDIT_SEED: list[dict] = [
    {"shipment_code": "ЛОГ-2026-0031", "carrier_code": "dpd",
     "invoice_amount": 33.00, "expected_amount": 28.00, "variance": 5.00,
     "reason": "Повторный «забор» (pickup) — уже включён", "status": "dispute"},
    {"shipment_code": "ЛОГ-2026-0044", "carrier_code": "cdek",
     "invoice_amount": 38.00, "expected_amount": 26.00, "variance": 12.00,
     "reason": "Вес округлён 30 → 32 кг", "status": "return"},
    {"shipment_code": "ЛОГ-2026-0052", "carrier_code": "evropochta",
     "invoice_amount": 23.50, "expected_amount": 14.50, "variance": 9.00,
     "reason": "Зона 3 вместо фактической 2", "status": "open"},
]

# --- Парк машин перевозчиков (Блок 2) --------------------------------------- #
# Свой транспорт — не из CARRIERS_RB; имя для подбора берётся отсюда.
CARRIER_NAMES_EXTRA: dict[str, str] = {"own": "Свой транспорт"}

# (carrier_code, vehicle_class, capacity_kg, volume_m3, temp_control, count)
_VEHICLES_RAW: list[tuple] = [
    ("dpd", "Газель 1.5т", 1500, 9, False, 12),
    ("dpd", "Фургон 3.5т", 3500, 16, False, 6),
    ("autolight", "Тент 5т", 5000, 30, False, 8),
    ("autolight", "Фура 20т", 20000, 86, False, 4),
    ("cdek", "Газель 1.5т", 1500, 9, False, 10),
    ("cdek", "Фургон 3.5т", 3500, 16, False, 5),
    ("evropochta", "Фургон 2т", 2000, 12, False, 7),
    ("belpost", "Фургон 1.5т", 1500, 10, False, 9),
    ("own", "Тент 5т", 5000, 30, False, 2),
    ("own", "Манипулятор 10т", 10000, 0, False, 1),
    ("own", "Рефрижератор 8т", 8000, 40, True, 1),
]
VEHICLES_SEED: list[dict] = [
    {"carrier_code": c, "vehicle_class": cls, "capacity_kg": cap,
     "volume_m3": vol, "temp_control": temp, "count": n}
    for c, cls, cap, vol, temp, n in _VEHICLES_RAW
]

# (carrier_code, category, adr, oversize, max_weight_kg, max_dim_cm)
_CAPABILITIES_RAW: list[tuple] = [
    ("dpd", "обычный", False, False, 0, 0),
    ("dpd", "хрупкое", False, False, 0, 0),
    ("autolight", "обычный", False, False, 0, 0),
    ("autolight", "АКБ", False, False, 0, 0),
    ("autolight", "негабарит", False, True, 0, 600),
    ("autolight", "опасный_ADR", True, False, 0, 0),
    ("cdek", "обычный", False, False, 0, 0),
    ("cdek", "хрупкое", False, False, 0, 0),
    ("cdek", "АКБ", False, False, 1000, 0),
    ("evropochta", "обычный", False, False, 30, 0),
    ("belpost", "обычный", False, False, 20, 0),
    ("own", "обычный", False, False, 0, 0),
    ("own", "АКБ", False, False, 0, 0),
    ("own", "негабарит", False, True, 0, 800),
    ("own", "температурный", False, False, 0, 0),
]
CAPABILITIES_SEED: list[dict] = [
    {"carrier_code": c, "category": cat, "adr": adr, "oversize": ov,
     "max_weight_kg": mw, "max_dim_cm": md}
    for c, cat, adr, ov, mw, md in _CAPABILITIES_RAW
]
