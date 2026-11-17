#!/usr/bin/env python3
"""Создать локальные настройки с независимыми случайными секретами."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    """Записать новый файл с правами 0600, не перезаписывая существующий."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=["dev", "prod"])
    parser.add_argument("--hostname", help="Имя HTTPS-хоста для PROD, без схемы и пути")
    args = parser.parse_args()
    if args.environment == "prod" and (
        not args.hostname or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in args.hostname)
    ):
        parser.error("Для PROD укажите --hostname с именем своего сервера")
    root = Path(__file__).resolve().parent.parent
    text = (root / "deploy" / f"{args.environment}.env.example").read_text(encoding="utf-8")
    destination = root / f".env.{args.environment}"
    text = text.replace("DJANGO_SECRET_KEY=\n", f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n")
    text = text.replace("DB_PASSWORD=\n", f"DB_PASSWORD={secrets.token_urlsafe(32)}\n")
    if args.environment == "prod":
        text = text.replace("portal.example.org", args.hostname)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError:
        parser.error("Файл настройки уже существует; перезапись запрещена")
    print(f"Создан {destination.name}. Секреты не выводятся в терминал")


if __name__ == "__main__":
    main()
