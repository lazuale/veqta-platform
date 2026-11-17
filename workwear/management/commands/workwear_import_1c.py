"""Штатная команда проверки и публикации трёх справочников 1С.

По умолчанию dry-run; apply требует tar.gz, точной БД, даты с часовым поясом,
подтверждённого количества и previous id. Запуск имеет доступ к кадровым
данным; stdout dry-run нельзя публиковать как обезличенный диагностический
отчёт: review содержит GUID выпавших сотрудников. См. docs/import_1c.md."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import tarfile
from django.core.management.base import BaseCommand, CommandError
from config.database import require_target
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError
from workwear.importers.directories_1c import parse, ImportRejected
from workwear.importers.acceptance import accept, review


class Command(BaseCommand):
    """Штатная management-команда: проверка целевой БД и явный dry-run/apply.

    Полный порядок и параметры: docs/import_1c.md; ошибки — CommandError."""
    help = "Проверить или атомарно принять три справочника 1С"

    def add_arguments(self, parser):
        """Зарегистрировать явные параметры CLI без чтения секретов и записи в БД."""
        parser.add_argument("--environment", required=True, choices=["dev", "prod"])
        parser.add_argument('source', help='Каталог с тремя CSV или архив .tar.gz')
        parser.add_argument('--source-exported-at', default=None,
                            help='Подтверждённая дата выгрузки ISO8601 с часовым поясом; обязательна для приёма')
        parser.add_argument('--apply', action='store_true', default=False)
        parser.add_argument('--database-name', required=True,
                            help='Точное ожидаемое имя базы выбранного контура')
        parser.add_argument('--expected-employee-count', type=int)
        parser.add_argument('--expected-previous-import-id', type=int)
        parser.add_argument('--confirm-missing-current-sha256')
        parser.add_argument('--acknowledge-undated-previous', action='store_true')

    def handle(self, *args, **options):
        """Проверить параметры и целевую БД, разобрать источник и выполнить выбранный режим.

        По умолчанию только проверка. Ошибка отклоняет запуск; публикация выполняется
        транзакционным сервисом. Не включать кадровый stdout в публичный отчёт."""
        require_target(options["environment"], options["database_name"])
        exported = None
        if options['source_exported_at']:
            try:
                exported = datetime.fromisoformat(options['source_exported_at'])
                if exported.utcoffset() is None:
                    raise ValueError('timezone required')
            except ValueError as exc:
                raise CommandError(f'Invalid source-exported-at: {exc}') from exc
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
            'unique_current_employees': batch.current_count,
            'source_exported_at': exported.isoformat() if exported else None,
            'coverage_period': None,
            'sha256': batch.sha256, 'anomalies': batch.anomalies,
        }
        if options['apply']:
            try:
                result['acceptance'] = asdict(accept(batch, source_exported_at=exported,
                    expected_employee_count=options['expected_employee_count'],
                    expected_previous_import_id=options['expected_previous_import_id'],
                    confirmed_missing_sha256=options['confirm_missing_current_sha256'],
                    acknowledge_undated_previous=options['acknowledge_undated_previous']))
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages)) from exc
            except IntegrityError as exc:
                constraint = getattr(getattr(exc.__cause__, 'diag', None), 'constraint_name', None)
                raise CommandError(f'Публикация полностью отменена ограничением БД: {constraint or "integrity"}') from exc
        else:
            result['publication_review'] = asdict(review(batch))
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
