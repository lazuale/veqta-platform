"""Проверка и публикация трёх справочников 1С."""
from dataclasses import asdict
from datetime import date
import json
import re
from pathlib import Path
import tarfile
from django.core.management.base import BaseCommand, CommandError
from config.database import require_target
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError
from workwear.importers.directories_1c import parse, ImportRejected
from workwear.importers.acceptance import accept, review


class Command(BaseCommand):
    help = "Проверить или атомарно принять три справочника 1С"

    def add_arguments(self, parser):
        parser.add_argument("--environment", required=True, choices=["dev", "prod"])
        parser.add_argument('source', help='Каталог с тремя CSV или архив .tar.gz')
        parser.add_argument('--source-export-date', default=None,
                            help='Подтверждённая дата выгрузки YYYY-MM-DD; обязательна для приёма')
        parser.add_argument('--apply', action='store_true', default=False)
        parser.add_argument('--database-name', required=True,
                            help='Точное ожидаемое имя базы выбранного контура')
        parser.add_argument('--expected-employee-count', type=int)
        parser.add_argument('--expected-previous-import-id', type=int)
        parser.add_argument('--confirm-missing-current-sha256')

    def handle(self, *args, **options):
        """Перед публикацией проверяем контур. Без --apply команда ничего не записывает."""
        require_target(options["environment"], options["database_name"])
        exported = None
        raw_date = options["source_export_date"]
        if raw_date is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
                raise CommandError("Дата выгрузки должна иметь формат YYYY-MM-DD")
            try:
                exported = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CommandError("Некорректная дата выгрузки YYYY-MM-DD") from exc
        if options['apply'] and Path(options['source']).is_dir():
            raise CommandError('APPLY requires a .tar.gz snapshot of the three CSV')
        try:
            batch = parse(options['source'])
        except (ImportRejected, OSError, ValueError, tarfile.TarError) as exc:
            raise CommandError(str(exc)) from exc
        result = {
            'mode': 'APPLY' if options['apply'] else 'DRY_RUN',
            'database': connection.settings_dict['NAME'],
            'contract': ['Employees.csv', 'Positions.csv', 'Units.csv'],
            'source_employees_rows': batch.source_row_count,
            'unique_current_employees': len(batch.people),
            'source_export_date': exported.isoformat() if exported else None,
            'sha256': batch.sha256, 'anomalies': batch.anomalies,
        }
        if options['apply']:
            try:
                result['acceptance'] = asdict(accept(batch, source_export_date=exported,
                    expected_employee_count=options['expected_employee_count'],
                    expected_previous_import_id=options['expected_previous_import_id'],
                    confirmed_missing_sha256=options['confirm_missing_current_sha256']))
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages)) from exc
            except IntegrityError as exc:
                constraint = getattr(getattr(exc.__cause__, 'diag', None), 'constraint_name', None)
                raise CommandError(f'Публикация полностью отменена ограничением БД: {constraint or "integrity"}') from exc
        else:
            result['publication_review'] = asdict(review(batch))
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
