"""Расчёт стоимости доставки по тарифу перевозчика (ядро справочника тарифов).

Чистые функции без I/O: объёмный вес, оплачиваемый вес и разбивка стоимости
по тарифу (``CarrierTariff``). Используются эндпоинтом ``/shipments/{id}/quote``
и аудитом счетов (сверка фактического счёта с ожидаемым тарифом).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _d(value) -> Decimal:
    """Безопасно привести число (float/int/Decimal/str) к Decimal."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def volumetric_weight(l_cm: float, w_cm: float, h_cm: float, divisor: int = 5000) -> float:
    """Объёмный вес, кг: ``Д×Ш×В / divisor`` (по умолчанию авиаделитель 5000)."""
    return (l_cm * w_cm * h_cm) / divisor


def chargeable_weight(physical_kg: float, volumetric_kg: float) -> float:
    """Оплачиваемый вес = максимум из физического и объёмного (правило перевозчиков)."""
    return max(physical_kg, volumetric_kg)


def quote_tariff(
    tariff,
    weight_kg: float,
    *,
    pickup: bool = False,
    cod_amount: float = 0.0,
    declared_value: float = 0.0,
) -> dict:
    """Разбивка стоимости доставки для одного перевозчика/зоны по тарифу.

    Вилки веса: ≤5 / ≤10 / ≤30 кг — фиксированная цена; свыше 30 кг — цена за 30 кг
    плюс ``over30_per_kg`` за каждый кг сверх. Сборы: забор (если ``pickup``),
    наложенный платёж (``cod_pct`` % от ``cod_amount``), страховка (``insurance_pct``
    % от ``declared_value``). Возвращает компоненты и итог (округление до копейки).
    """
    weight = _d(weight_kg)
    if weight <= 5:
        base = _d(tariff.price_w5)
    elif weight <= 10:
        base = _d(tariff.price_w10)
    elif weight <= 30:
        base = _d(tariff.price_w30)
    else:
        base = _d(tariff.price_w30) + (weight - 30) * _d(tariff.over30_per_kg)

    pickup_fee = _d(tariff.pickup_fee) if pickup else Decimal("0")
    cod_fee = _d(cod_amount) * (_d(tariff.cod_pct) / 100)
    insurance_fee = _d(declared_value) * (_d(tariff.insurance_pct) / 100)
    total = base + pickup_fee + cod_fee + insurance_fee

    cents = Decimal("0.01")
    return {
        "base": float(base.quantize(cents, ROUND_HALF_UP)),
        "pickup": float(pickup_fee.quantize(cents, ROUND_HALF_UP)),
        "cod_fee": float(cod_fee.quantize(cents, ROUND_HALF_UP)),
        "insurance_fee": float(insurance_fee.quantize(cents, ROUND_HALF_UP)),
        "total": float(total.quantize(cents, ROUND_HALF_UP)),
    }


# --- Scorecard: взвешенный балл и грейд перевозчика (ТЗ §3) ------------------ #
def score_carrier(otd_pct: float, damage_free_pct: float, billing_accuracy_pct: float,
                  claims_ratio_pct: float) -> float:
    """Взвешенный балл перевозчика: OTD 0.40 · брак 0.25 · счета 0.20 · претензии 0.15."""
    raw = (
        otd_pct * 0.40
        + damage_free_pct * 0.25
        + billing_accuracy_pct * 0.20
        + (100 - claims_ratio_pct) * 0.15
    )
    return float(Decimal(str(raw)).quantize(Decimal("0.1"), ROUND_HALF_UP))


def grade_for(score: float) -> str:
    """Грейд по баллу: A ≥ 90, B ≥ 83, иначе C."""
    if score >= 90:
        return "A"
    if score >= 83:
        return "B"
    return "C"
