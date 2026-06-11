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


def send_invite(channel: str, contact: str, message: str, *, settings=None) -> dict:
    """«Отправить» приглашение перевозчику. ``none`` пропускается.

    Если для канала задан реальный транспорт (SMTP для email / Bot API для telegram в
    ``settings``) — отправляет по-настоящему; иначе MVP-лог (как раньше). Возвращает
    ``{status, channel, detail}`` (``status`` ∈ sent/skipped/failed). Ошибку реальной
    отправки фиксируем явно (``failed``) — не молчим.
    """
    if channel == "none":
        return {"status": "skipped", "channel": "none", "detail": "нет контакта перевозчика"}
    try:
        if channel == "email" and settings and getattr(settings, "smtp_host", ""):
            _send_email(settings, contact, "Приглашение в тендер на перевозку", message)
            return {"status": "sent", "channel": "email", "detail": f"email → {contact}"}
        if channel == "telegram" and settings and getattr(settings, "telegram_bot_token", ""):
            _send_telegram(settings, _tg_chat(contact), message)
            return {"status": "sent", "channel": "telegram", "detail": f"telegram → {contact}"}
    except Exception as exc:  # noqa: BLE001 — рассылка best-effort; ошибку фиксируем явно
        logger.warning("tender invite send failed (%s → %s): %s", channel, contact, exc)
        return {"status": "failed", "channel": channel, "detail": f"ошибка отправки: {exc}"}
    # реальный транспорт не сконфигурирован → MVP-лог
    logger.info("tender invite via %s → %s: %s", channel, contact, message)
    return {"status": "sent", "channel": channel, "detail": f"отправлено ({channel}, MVP-лог)"}


def _tg_chat(contact: str) -> str:
    """chat_id для Telegram из контакта: ``tg:123`` → ``123``; иначе — как есть (@channel/id)."""
    c = (contact or "").strip()
    return c[3:] if c.lower().startswith("tg:") else c


def _send_email(settings, to_addr: str, subject: str, body: str) -> None:
    """Отправить email через SMTP (smtplib). Реальная рассылка — за конфигом ``AIOS_SMTP_*``."""
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = getattr(settings, "smtp_from", "no-reply@aios.local")
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, getattr(settings, "smtp_port", 587), timeout=10) as smtp:
        if getattr(settings, "smtp_tls", True):
            smtp.starttls()
        if getattr(settings, "smtp_user", ""):
            smtp.login(settings.smtp_user, getattr(settings, "smtp_password", ""))
        smtp.send_message(msg)


def _send_telegram(settings, chat_id: str, text: str) -> None:
    """Отправить сообщение через Telegram Bot API (httpx). Токен — ``AIOS_TELEGRAM_BOT_TOKEN``.

    Для личных чатов нужен числовой ``chat_id`` (перевозчик должен начать диалог с ботом);
    ``@username`` работает только для каналов. Полный онбординг перевозчиков — Итерация 2.
    """
    import httpx

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10).raise_for_status()
