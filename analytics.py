"""Аналитика стоимости перевозок — бизнес-цель ТЗ «постоянно улучшать стоимость».

Чистые функции поверх уже собранных данных (тарифы через котировку, тендерные
ставки, аудит счетов). Считают, где возить дешевле (разброс тарифов по зонам и
самый дешёвый перевозчик на эталонный вес), сколько сэкономлено торгом по
заключённым тендерам и сколько подлежит возврату по аудиту, и формируют
практические рекомендации. Без I/O и ORM — вход готовит роут; удобно юнит-тестировать.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _money(value: float) -> float:
    """Округление до копейки (BYN)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def zone_cost_insight(zone_code: str, zone_name: str, quotes: list[dict]) -> dict | None:
    """Свод по зоне из котировок ``[{carrier_code, carrier, total}]`` на эталонный вес.

    Возвращает самого дешёвого перевозчика, средний/макс итог и разброс
    ``spread_pct = (max − cheapest) / cheapest × 100`` (потенциал экономии в зоне).
    Пустой список котировок → ``None`` (в зоне нет тарифов).
    """
    rows = [q for q in quotes if q.get("total") is not None]
    if not rows:
        return None
    cheapest = min(rows, key=lambda q: q["total"])
    totals = [q["total"] for q in rows]
    base = cheapest["total"]
    spread_pct = ((max(totals) - base) / base * 100) if base else 0.0
    return {
        "zone_code": zone_code,
        "zone_name": zone_name,
        "carriers": len(rows),
        "cheapest_carrier": cheapest["carrier_code"],
        "cheapest_carrier_name": cheapest.get("carrier", cheapest["carrier_code"]),
        "cheapest_total": _money(base),
        "avg_total": _money(sum(totals) / len(totals)),
        "max_total": _money(max(totals)),
        "spread_pct": _money(spread_pct),
    }


def tender_saving(rfq_number: str, route: str, carrier: str, prices: list[float]) -> dict | None:
    """Экономия по тендеру: худшая (стартовая) цена vs выигравшая (минимум всех раундов).

    ``prices`` — цены всех предложений/раундов тендера. ``saved = baseline − awarded``,
    где baseline = максимум, awarded = минимум. Пустой список → ``None``.
    """
    vals = [p for p in prices if p is not None]
    if not vals:
        return None
    baseline, awarded = max(vals), min(vals)
    saved = baseline - awarded
    return {
        "rfq_number": rfq_number,
        "route": route,
        "carrier": carrier,
        "baseline": _money(baseline),
        "awarded": _money(awarded),
        "saved": _money(saved),
        "saved_pct": _money((saved / baseline * 100) if baseline else 0.0),
    }


def build_recommendations(zones: list[dict], tenders: list[dict], audit_to_recover: float) -> list[str]:
    """Топ практических рекомендаций по снижению стоимости (человекочитаемый текст)."""
    recs: list[str] = []
    for z in sorted(zones, key=lambda z: z["spread_pct"], reverse=True)[:3]:
        if z["spread_pct"] >= 5:
            recs.append(
                f"Зона {z['zone_code']}: возить через «{z['cheapest_carrier_name']}» — "
                f"на {_money(z['avg_total'] - z['cheapest_total'])} BYN ниже среднего "
                f"(разброс {z['spread_pct']:.0f}%)."
            )
    if audit_to_recover > 0:
        recs.append(
            f"Аудит счетов: к возврату {_money(audit_to_recover)} BYN — выставить претензии перевозчикам."
        )
    total_saved = sum(t["saved"] for t in tenders)
    if total_saved > 0:
        recs.append(
            f"Торг по тендерам сэкономил {_money(total_saved)} BYN — практику снижения цены продолжать."
        )
    return recs


def summarize(reference_weight_kg: float, zones: list[dict], tenders: list[dict],
              audit_to_recover: float) -> dict:
    """Собрать полный отчёт cost-insights из готовых частей.

    ``potential_savings`` — сумма по зонам ``(avg − cheapest)`` на эталонный вес: сколько
    экономит выбор самого дешёвого перевозчика вместо среднего на одну отправку.
    ``best_savings_zone`` — зона с наибольшим абсолютным потенциалом.
    """
    potential = sum(z["avg_total"] - z["cheapest_total"] for z in zones)
    best_zone = max(zones, key=lambda z: z["avg_total"] - z["cheapest_total"])["zone_code"] if zones else ""
    return {
        "reference_weight_kg": reference_weight_kg,
        "zones": zones,
        "potential_savings": _money(potential),
        "best_savings_zone": best_zone,
        "tender_savings_total": _money(sum(t["saved"] for t in tenders)),
        "tenders": tenders,
        "audit_to_recover": _money(audit_to_recover),
        "recommendations": build_recommendations(zones, tenders, audit_to_recover),
    }
