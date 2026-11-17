"""Первичная загрузка отдельно утверждённого каталога спецодежды.

Это не выгрузка 1С. Dry-run не пишет; apply создаёт недостающие записи
в atomic-транзакции и отказывается перезаписывать отличающийся каталог.
Параметр database-name служит защитой от неверного контура."""

import csv
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from config.database import require_target
from django.db import connection, transaction
from workwear.models import WorkwearItem


class Command(BaseCommand):
    """Штатная management-команда: проверка целевой БД и явный dry-run/apply.

    Полный порядок и параметры: docs/import_1c.md; ошибки — CommandError."""
    help = "Проверить или загрузить утверждённый CSV каталога СИЗ"

    def add_arguments(self, parser):
        """Зарегистрировать явные параметры CLI без чтения секретов и записи в БД."""
        parser.add_argument("--environment", required=True, choices=["dev", "prod"])
        parser.add_argument("source")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--database-name", required=True,
                            help="Точное ожидаемое имя базы выбранного контура")
        parser.add_argument("--expected-count", type=int, default=11)

    def handle(self, *args, **options):
        """Проверить параметры и целевую БД, разобрать источник и выполнить выбранный режим.

        По умолчанию только проверка. Ошибка отклоняет запуск; публикация выполняется
        транзакционным сервисом. Не включать кадровый stdout в публичный отчёт."""
        require_target(options["environment"], options["database_name"])
        path = Path(options["source"])
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter=";")
                expected = {"code", "name", "category", "validity_days", "sort_order", "is_active"}
                if set(reader.fieldnames or []) != expected:
                    raise CommandError("Catalog source must have exactly: " + ", ".join(sorted(expected)))
                rows = list(reader)
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        if len(rows) != options["expected_count"] or options["expected_count"] < 1:
            raise CommandError("Catalog count differs from explicitly expected count")
        clean, codes = [], set()
        for idx, row in enumerate(rows, start=2):
            if None in row or any(v is None for v in row.values()):
                raise CommandError(f"Malformed catalog row {idx}")
            code = row["code"].strip()
            name = row["name"].strip()
            category = row["category"].strip()
            try:
                validity = int(row["validity_days"])
                sort_order = int(row["sort_order"])
            except ValueError as exc:
                raise CommandError(f"Invalid numbers on row {idx}") from exc
            if row["is_active"] not in ("1", "0"):
                raise CommandError(f"is_active must be 0 or 1 on row {idx}")
            if (not code or len(code) > 32 or not name or len(name) > 120 or
                    not category or len(category) > 64 or validity not in (365, 730) or
                    sort_order < 0 or code in codes):
                raise CommandError(f"Invalid or duplicated catalog row {idx}")
            codes.add(code)
            clean.append({"code": code, "name": name, "category": category,
                          "validity_days": validity, "sort_order": sort_order,
                          "is_active": row["is_active"] == "1"})
        if not options["apply"]:
            self.stdout.write(f"DRY_RUN catalog validated: {len(clean)} positions; database untouched")
            return
        with transaction.atomic():
            existing = {item.code: item for item in WorkwearItem.objects.select_for_update()}
            to_create = []
            for data in clean:
                old = existing.get(data["code"])
                if old:
                    if any(getattr(old, key) != value for key, value in data.items()):
                        raise CommandError(f"Catalog conflict for code {data['code']}; refused overwrite")
                else:
                    to_create.append(WorkwearItem(**data))
            WorkwearItem.objects.bulk_create(to_create)
            self.stdout.write(f"APPLIED: created {len(to_create)}; retained {len(clean)-len(to_create)} matching entries")
