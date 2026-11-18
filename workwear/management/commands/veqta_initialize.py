"""Начальное создание схемы без рабочих данных и без загрузки каталога."""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from config.database import require_target


class Command(BaseCommand):
    """Применить миграции только к пустому контуру с явным подтверждением имени."""
    help = "Создать схему VEQTA в новой пустой PostgreSQL"

    def add_arguments(self, parser):
        """Потребовать точный контур и имя новой базы."""
        parser.add_argument("--environment", required=True, choices=["dev", "prod"])
        parser.add_argument("--database-name", required=True)

    def handle(self, *args, **options):
        """Отказаться от существующей схемы, затем выполнить штатные миграции."""
        require_target(options["environment"], options["database_name"], owner=True)
        if connection.introspection.table_names():
            raise CommandError("База уже содержит таблицы. Для дальнейших миграций используйте migrate после проверки контура")
        call_command("migrate", interactive=False, stdout=self.stdout)
        call_command("check", stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f"Схема {options['database_name']} создана. Рабочих данных нет; каталог СИЗ загружается отдельно"
        ))
