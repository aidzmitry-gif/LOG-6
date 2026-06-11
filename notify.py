"""Уведомление перевозчиков о тендере (рассылка) — pluggable-«отправитель».

MVP: выбирает канал по контакту перевозчика, формирует текст приглашения и
«отправляет» его. Реальная доставка (SMTP / Telegram Bot API) — Итерация 1 за
конфигом; по умолчанию канал логируется, а приглашение помечается отправленным.
Выбор канала и текст — чистые функции (без I/O), удобно юнит-тестировать;
``send_invite`` только пишет в лог и возвращает результат.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("logistics.notify")


def pick_channel(contact: str) -> str:
    """Канал доставки по контакту перевозчика: email / telegram / phone / none."""
    c = (contact or "").strip()
    if c.startswith("@") or c.lower().startswith("tg:") or "t.me/" in c:
        return "telegram"             # telegram-хэндл начинается с @ — проверяем до email
    if "@" in c:
        return "email"
    if any(ch.isdigit() for ch in c):
        return "phone"
    return "none"


def invite_message(number: str, cargo: str, weight_kg: float, route_from: str,
                   route_to: str, carrier_name: str, bid_url: str) -> str:
    """Текст приглашения в тендер: параметры груза + ссылка на подачу ставки."""
    return (
        f"{carrier_name or 'Перевозчик'}, приглашаем в тендер {number} на перевозку: "
        f"{cargo or 'груз'}, {weight_kg:.0f} кг, {route_from or '—'} → {route_to or '—'}. "
        f"Подайте предложение по ссылке: {bid_url}"
    )


def send_invite(channel: str, contact: str, message: str) -> dict:
    """«Отправить» приглашение. ``none`` пропускается, остальные каналы логируются.

    Возвращает ``{status, channel, detail}`` (``status`` ∈ sent/skipped). Реальная
    доставка по каналу — Итерация 1 (подменяется здесь без изменения вызова).
    """
    if channel == "none":
        return {"status": "skipped", "channel": "none", "detail": "нет контакта перевозчика"}
    logger.info("tender invite via %s → %s: %s", channel, contact, message)
    return {"status": "sent", "channel": channel, "detail": f"отправлено ({channel}, MVP-лог)"}
