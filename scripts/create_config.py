#!/usr/bin/env python3
"""Создать отдельные настройки приложения и миграций для нового контура."""
import argparse
import os
from pathlib import Path
import re
import secrets


def valid_hostname(value):
    """Явное DNS-имя HTTPS без схемы, порта, wildcard и Nginx-директив."""
    return bool(len(value) <= 253 and "." in value and re.search("[a-z]", value) and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=["dev", "prod"])
    parser.add_argument("--hostname", required=True, help="DNS-имя своего HTTPS-хоста, без схемы и пути")
    args = parser.parse_args()
    if not valid_hostname(args.hostname):
        parser.error("Нужен корректный hostname в нижнем регистре")
    root = Path(__file__).resolve().parent.parent
    template = (root / "deploy" / f"{args.environment}.env.example").read_text(encoding="utf-8")
    template = template.replace("__HOSTNAME__", args.hostname)
    template = template.replace("DJANGO_SECRET_KEY=\n", f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n")
    paths = [root / f".env.{args.environment}", root / f".env.{args.environment}-migrate"]
    if any(path.exists() or path.is_symlink() for path in paths):
        parser.error("Файл настройки уже существует; перезапись запрещена")
    created = []
    try:
        for path, role in zip(paths, (f"veqta_{args.environment}", f"veqta_{args.environment}_owner")):
            content = template.replace(f"DB_USER=veqta_{args.environment}\n", f"DB_USER={role}\n")
            content = content.replace("DB_PASSWORD=\n", f"DB_PASSWORD={secrets.token_urlsafe(32)}\n")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    except OSError:
        for path in created:
            path.unlink()
        parser.error("Не удалось создать пару настроек; созданные этим запуском файлы удалены")
    print("Созданы " + " и ".join(path.name for path in paths) + ". Секреты не выводятся в терминал")


if __name__ == "__main__":
    main()
