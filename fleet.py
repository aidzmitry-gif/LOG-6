"""Подбор перевозчика по пригодности груза (парк машин + допуски).

Чистые функции сопоставления: помещается ли груз в машину перевозчика и допускает
ли перевозчик категорию груза. ``eligible_carriers`` собирает из строк парка и
допусков список пригодных перевозчиков с подходящей машиной (минимальной достаточной).
Заменяет статичный флаг ``heavy`` офисного справочника осмысленным подбором.
"""
from __future__ import annotations


def vehicle_fits(vehicle, weight_kg: float, *, needs_temp: bool = False) -> bool:
    """Помещается ли груз веса ``weight_kg`` в машину (+ термо-режим при ``needs_temp``)."""
    if needs_temp and not vehicle.temp_control:
        return False
    return float(vehicle.capacity_kg or 0) >= weight_kg


def capability_allows(cap, weight_kg: float, *, max_dim_cm: int = 0, adr: bool = False) -> bool:
    """Допускает ли запись пригодности груз (ADR, лимиты веса/габарита)."""
    if adr and not cap.adr:
        return False
    if cap.max_weight_kg and float(cap.max_weight_kg) < weight_kg:
        return False
    if cap.max_dim_cm and max_dim_cm and cap.max_dim_cm < max_dim_cm:
        return False
    return True


def carrier_eligible(
    vehicles: list,
    capabilities: list,
    *,
    weight_kg: float,
    category: str = "",
    needs_temp: bool = False,
    max_dim_cm: int = 0,
    adr: bool = False,
) -> dict | None:
    """Пригоден ли перевозчик: есть достаточная машина И допуск к категории груза.

    Возвращает ``{"vehicle_class", "capacity_kg"}`` лучшей (минимальной достаточной)
    машины, либо ``None`` если перевозчик не подходит. Если ``category`` пуста —
    проверяется только наличие машины (любой груз без спец-требований).
    """
    fitting = [v for v in vehicles if vehicle_fits(v, weight_kg, needs_temp=needs_temp)]
    if not fitting:
        return None

    if category or adr:
        matching = [
            c for c in capabilities
            if (not category or c.category == category)
            and capability_allows(c, weight_kg, max_dim_cm=max_dim_cm, adr=adr)
        ]
        if not matching:
            return None

    best = min(fitting, key=lambda v: float(v.capacity_kg or 0))
    return {"vehicle_class": best.vehicle_class, "capacity_kg": float(best.capacity_kg or 0)}
