"""Начальная загрузка каталога СИЗ."""

import csv
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from config.database import require_target
from django.db import transaction
from workwear.models import WorkwearItem


class Command(BaseCommand):
    help = "Проверить или загрузить утверждённый CSV каталога СИЗ"

    def add_arguments(self, parser):
        parser.add_argument("--environment", required=True, choices=["dev", "prod"])
        parser.add_argument("source")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--database-name", required=True,
                            help="Точное ожидаемое имя базы выбранного контура")
        parser.add_argument("--expected-count", type=int, required=True)

    def handle(self, *args, **options):
        """Перед загрузкой сверяем контур. Без --apply ничего не записываем."""
        require_target(options["environment"], options["database_name"])
        path = Path(options["source"])
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter=";")
                expected = {"code", "name", "category", "validity_days", "sort_order", "is_active"}
                if set(reader.fieldnames or []) != expected or len(reader.fieldnames or []) != len(expected):
                    raise CommandError("В каталоге должны быть только столбцы: " + ", ".join(sorted(expected)))
                rows = list(reader)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError(str(exc)) from exc
        if len(rows) != options["expected_count"] or options["expected_count"] < 1:
            raise CommandError("Число позиций каталога не совпадает с --expected-count")
        clean, codes = [], set()
        for idx, row in enumerate(rows, start=2):
            if None in row or any(v is None for v in row.values()):
                raise CommandError(f"Некорректная строка каталога: {idx}")
            if row["is_active"] not in ("1", "0"):
                raise CommandError(f"is_active должен быть 0 или 1, строка {idx}")
            item = WorkwearItem(code=row["code"].strip(), name=row["name"].strip(),
                category=row["category"].strip(), validity_days=row["validity_days"],
                sort_order=row["sort_order"], is_active=row["is_active"] == "1")
            try:
                # Admin и команда используют одинаковые проверки модели.
                # Повторная загрузка не создаёт позиции с тем же кодом.
                item.full_clean(validate_unique=False)
            except ValidationError as exc:
                raise CommandError(f"Каталог, строка {idx}: {'; '.join(exc.messages)}") from exc
            if item.code in codes:
                raise CommandError(f"Повтор кода {item.code}, строка {idx}")
            codes.add(item.code)
            clean.append({field: getattr(item, field) for field in expected})
        if not options["apply"]:
            self.stdout.write(f"DRY_RUN: проверено позиций: {len(clean)}; данные БД не изменены")
            return
        with transaction.atomic():
            existing = {item.code: item for item in WorkwearItem.objects.select_for_update()}
            to_create = []
            for data in clean:
                old = existing.get(data["code"])
                if old:
                    if any(getattr(old, key) != value for key, value in data.items()):
                        raise CommandError(f"Позиция с кодом {data['code']} уже существует с другими данными; изменения отклонены")
                else:
                    to_create.append(WorkwearItem(**data))
            WorkwearItem.objects.bulk_create(to_create)
            self.stdout.write(f"APPLIED: создано позиций: {len(to_create)}; без изменений: {len(clean)-len(to_create)}")
