#!/usr/bin/env python3
"""Monitor Turkish Ministry GKD lists for new Aydin records."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any


BASE_URL = "https://guvenilirgida.tarimorman.gov.tr"
DATA_URL = f"{BASE_URL}/GuvenilirGida/GKD/DataTablesList"
DEFAULT_CONFIG = "config.example.json"
DEFAULT_STATE = "gkd_state.json"
DEFAULT_CITY = "AYDIN"

COLUMNS = [
    "DuyuruTarihi",
    "FirmaAdi",
    "Marka",
    "UrunAdi",
    "Uygunsuzluk",
    "PartiSeriNo",
    "FirmaIlce",
    "FirmaIl",
    "UrunGrupAdi",
]


@dataclass(frozen=True)
class ListConfig:
    key: str
    name: str
    page_url: str
    extra_fields: dict[str, str]


LISTS = [
    ListConfig(
        key="sagligi_tehlikeye_dusurecek",
        name="Sagligi Tehlikeye Dusurecek Gidalar",
        page_url=f"{BASE_URL}/GuvenilirGida/gkd/SagligiTehlikeyeDusurecek?siteYayinDurumu=True",
        extra_fields={
            "KamuoyuDuyuruAra.ListeTurId": "",
            "KamuoyuDuyuruAra.IdariYaptirimYasalDayanakIdler": "",
            "KamuoyuDuyuruAra.IdariYaptirimYasalDayanakId": "",
        },
    ),
    ListConfig(
        key="ayni_degeri_tasimayan_madde_eklenmesi",
        name="Taklit veya Tagsis - Ayni Degeri Tasimayan Madde Eklenmesi",
        page_url=f"{BASE_URL}/GuvenilirGida/gkd/TaklitVeyaTagsisListe1?siteYayinDurumu=True",
        extra_fields={
            "KamuoyuDuyuruAra.HaricTut": "",
            "KamuoyuDuyuruAra.ListeTurId": "304",
            "KamuoyuDuyuruAra.IdariYaptirimYasalDayanakIdler": "",
            "KamuoyuDuyuruAra.IdariYaptirimYasalDayanakId": "0",
        },
    ),
    ListConfig(
        key="temel_ozelligi_etkileyen_icerik_eksikligi",
        name="Taklit veya Tagsis - Temel Ozelligi Etkileyen Icerik Eksikligi",
        page_url=f"{BASE_URL}/GuvenilirGida/gkd/TaklitVeyaTagsisListe2?siteYayinDurumu=True",
        extra_fields={
            "KamuoyuDuyuruAra.HaricTut": "304",
            "KamuoyuDuyuruAra.ListeTurId": "",
            "KamuoyuDuyuruAra.IdariYaptirimYasalDayanakIdler": "",
            "KamuoyuDuyuruAra.IdariYaptirimYasalDayanakId": "0",
        },
    ),
]


def normalize_text(value: Any) -> str:
    text = html.unescape(str(value or "")).strip().upper()
    return (
        text.replace("İ", "I")
        .replace("İ", "I")
        .replace("Ğ", "G")
        .replace("Ü", "U")
        .replace("Ş", "S")
        .replace("Ö", "O")
        .replace("Ç", "C")
    )


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_dotnet_date(value: Any) -> str:
    match = re.search(r"/Date\((\d+)\)/", str(value or ""))
    if not match:
        return clean_text(value)
    timestamp = int(match.group(1)) / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d.%m.%Y")


def record_id(list_key: str, row: dict[str, Any]) -> str:
    parts = [
        list_key,
        clean_text(row.get("DuyuruTarihi")),
        clean_text(row.get("FirmaAdi")),
        clean_text(row.get("Marka")),
        clean_text(row.get("UrunAdi")),
        clean_text(row.get("PartiSeriNo")),
        clean_text(row.get("FirmaIl")),
        clean_text(row.get("FirmaIlce")),
    ]
    raw = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def data_tables_payload(list_config: ListConfig, start: int, length: int) -> dict[str, str]:
    payload = {
        "draw": "1",
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "desc",
        "Order[0].column": "DuyuruTarihi",
        "Order[0].dir": "desc",
        "SiteYayinDurumu": "True",
        "KamuoyuDuyuruAra.DuyuruTarihi": "",
        "_KamuoyuDuyuruAra_UrunGrupId": "",
        "KamuoyuDuyuruAra.UrunGrupId": "",
    }
    payload.update(list_config.extra_fields)

    for index, column in enumerate(COLUMNS):
        payload[f"columns[{index}][data]"] = column
        payload[f"columns[{index}][name]"] = column
        payload[f"columns[{index}][searchable]"] = "true"
        payload[f"columns[{index}][orderable]"] = "true"
        payload[f"columns[{index}][search][value]"] = ""
        payload[f"columns[{index}][search][regex]"] = "false"
    return payload


def http_get(url: str, opener: urllib.request.OpenerDirector) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 GKD Aydin Monitor",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with opener.open(request, timeout=30) as response:
        return response.read()


def http_post_json(url: str, payload: dict[str, str], referer: str, opener: urllib.request.OpenerDirector) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "Mozilla/5.0 GKD Aydin Monitor",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        },
        method="POST",
    )
    with opener.open(request, timeout=30) as response:
        raw = response.read().decode("utf-8-sig")
    if raw.lstrip().startswith("<"):
        raise RuntimeError("GKD endpoint returned HTML instead of JSON.")
    return json.loads(raw)


def fetch_list(list_config: ListConfig, page_size: int = 500) -> list[dict[str, Any]]:
    cookie_processor = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(cookie_processor)
    http_get(list_config.page_url, opener)

    rows: list[dict[str, Any]] = []
    start = 0
    total: int | None = None
    while total is None or start < total:
        payload = data_tables_payload(list_config, start, page_size)
        data = http_post_json(DATA_URL, payload, list_config.page_url, opener)
        batch = data.get("data") or []
        rows.extend(batch)
        total = int(data.get("recordsFiltered") or len(rows))
        if not batch:
            break
        start += len(batch)
    return rows


def fetch_matches(city: str) -> list[dict[str, Any]]:
    wanted_city = normalize_text(city)
    matches: list[dict[str, Any]] = []
    for list_config in LISTS:
        for row in fetch_list(list_config):
            if normalize_text(row.get("FirmaIl")) != wanted_city:
                continue
            item = {key: clean_text(value) for key, value in row.items()}
            item["DuyuruTarihi"] = parse_dotnet_date(row.get("DuyuruTarihi"))
            item["_id"] = record_id(list_config.key, row)
            item["_list_key"] = list_config.key
            item["_list_name"] = list_config.name
            item["_source_url"] = list_config.page_url
            matches.append(item)
        time.sleep(0.5)
    return matches


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Environment variable {name} must be true or false.")


def apply_environment_config(config: dict[str, Any]) -> dict[str, Any]:
    """Overlay notification settings from environment variables."""
    merged = dict(config)
    email_config = dict(merged.get("email") or {})
    telegram_config = dict(merged.get("telegram") or {})

    email_config["enabled"] = env_bool(
        "GKD_EMAIL_ENABLED", bool(email_config.get("enabled", False))
    )
    email_config["starttls"] = env_bool(
        "GKD_SMTP_STARTTLS", bool(email_config.get("starttls", True))
    )
    email_config["send_no_new_report"] = env_bool(
        "GKD_SEND_NO_NEW_REPORT",
        bool(email_config.get("send_no_new_report", False)),
    )

    email_values = {
        "smtp_host": "GKD_SMTP_HOST",
        "username": "GKD_SMTP_USERNAME",
        "password": "GKD_SMTP_PASSWORD",
        "from": "GKD_EMAIL_FROM",
    }
    for config_key, environment_key in email_values.items():
        if environment_key in os.environ:
            email_config[config_key] = os.environ[environment_key]

    if "GKD_SMTP_PORT" in os.environ:
        try:
            email_config["smtp_port"] = int(os.environ["GKD_SMTP_PORT"])
        except ValueError as exc:
            raise RuntimeError("Environment variable GKD_SMTP_PORT must be an integer.") from exc

    if "GKD_EMAIL_TO" in os.environ:
        email_config["to"] = [
            value.strip()
            for value in os.environ["GKD_EMAIL_TO"].split(",")
            if value.strip()
        ]

    telegram_config["enabled"] = env_bool(
        "GKD_TELEGRAM_ENABLED", bool(telegram_config.get("enabled", False))
    )
    telegram_values = {
        "bot_token": "GKD_TELEGRAM_BOT_TOKEN",
        "chat_id": "GKD_TELEGRAM_CHAT_ID",
    }
    for config_key, environment_key in telegram_values.items():
        if environment_key in os.environ:
            telegram_config[config_key] = os.environ[environment_key]

    merged["email"] = email_config
    merged["telegram"] = telegram_config
    return merged


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def format_alert(city: str, records: list[dict[str, Any]]) -> tuple[str, str]:
    subject = f"GKD uyarisi: {city} icin {len(records)} yeni kayit"
    lines = [
        f"Guvenilir Gida GKD listelerinde {city} ili icin {len(records)} yeni kayit bulundu.",
        "",
    ]
    for index, record in enumerate(records, 1):
        lines.extend(
            [
                f"{index}. {record.get('_list_name')}",
                f"Tarih: {record.get('DuyuruTarihi')}",
                f"Firma: {record.get('FirmaAdi')}",
                f"Il/Ilce: {record.get('FirmaIl')} / {record.get('FirmaIlce')}",
                f"Marka: {record.get('Marka')}",
                f"Urun: {record.get('UrunAdi')}",
                f"Uygunsuzluk: {record.get('Uygunsuzluk')}",
                f"Parti/Seri No: {record.get('PartiSeriNo')}",
                f"Kaynak: {record.get('_source_url')}",
                "",
            ]
        )
    return subject, "\n".join(lines).strip()


def format_no_new_report(city: str, existing_count: int) -> tuple[str, str]:
    checked_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    subject = f"GKD gunluk kontrol: {city} icin yeni kayit yok"
    body = (
        f"Guvenilir Gida GKD listeleri {checked_at} tarihinde kontrol edildi.\n\n"
        f"{city} ili icin yeni kayit bulunmadi.\n"
        f"Sistemde daha once gorulen mevcut {city} kaydi: {existing_count}\n\n"
        f"Kaynak: {BASE_URL}/gkd"
    )
    return subject, body


def send_email(config: dict[str, Any], subject: str, body: str) -> None:
    email_config = config.get("email") or {}
    if not email_config.get("enabled"):
        return

    required = ["smtp_host", "smtp_port", "username", "password", "from", "to"]
    missing = [key for key in required if not email_config.get(key)]
    if missing:
        raise RuntimeError(f"Email config is missing: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_config["from"]
    message["To"] = ", ".join(email_config["to"]) if isinstance(email_config["to"], list) else email_config["to"]
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(email_config["smtp_host"], int(email_config["smtp_port"]), timeout=30) as server:
        if email_config.get("starttls", True):
            server.starttls(context=context)
        server.login(email_config["username"], email_config["password"])
        server.send_message(message)


def send_telegram(config: dict[str, Any], subject: str, body: str) -> None:
    telegram_config = config.get("telegram") or {}
    if not telegram_config.get("enabled"):
        return
    token = telegram_config.get("bot_token")
    chat_id = telegram_config.get("chat_id")
    if not token or not chat_id:
        raise RuntimeError("Telegram config needs bot_token and chat_id.")

    text = f"{subject}\n\n{body}"
    if len(text) > 3900:
        text = text[:3890] + "\n..."

    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {result}")


def send_notifications(config: dict[str, Any], subject: str, body: str) -> None:
    send_email(config, subject, body)
    send_telegram(config, subject, body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor GKD lists for new city records.")
    parser.add_argument("--config", default=os.environ.get("GKD_CONFIG", DEFAULT_CONFIG))
    parser.add_argument("--state", default=os.environ.get("GKD_STATE", DEFAULT_STATE))
    parser.add_argument("--city", default=os.environ.get("GKD_CITY", DEFAULT_CITY))
    parser.add_argument("--notify-existing", action="store_true", help="Alert for records already present on first run.")
    parser.add_argument("--dry-run", action="store_true", help="Print results without sending notifications or changing state.")
    args = parser.parse_args()

    config_path = Path(args.config)
    state_path = Path(args.state)
    config = apply_environment_config(load_json(config_path, {}))
    state = load_json(state_path, {"seen_ids": []})
    seen_ids = set(state.get("seen_ids", []))

    matches = fetch_matches(args.city)
    new_records = [record for record in matches if record["_id"] not in seen_ids]

    if not seen_ids and not args.notify_existing:
        print(f"Baseline created: {len(matches)} existing {args.city} records marked as seen.")
        if not args.dry_run:
            save_json(
                state_path,
                {
                    "city": args.city,
                    "seen_ids": sorted(record["_id"] for record in matches),
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        return 0

    if not new_records:
        print(f"No new {args.city} records. Existing matches: {len(matches)}.")
        if not args.dry_run and (config.get("email") or {}).get("send_no_new_report"):
            subject, body = format_no_new_report(args.city, len(matches))
            send_notifications(config, subject, body)
    else:
        subject, body = format_alert(args.city, new_records)
        print(body)
        if not args.dry_run:
            send_notifications(config, subject, body)

    if not args.dry_run:
        all_seen = seen_ids | {record["_id"] for record in matches}
        save_json(
            state_path,
            {
                "city": args.city,
                "seen_ids": sorted(all_seen),
                "last_checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
