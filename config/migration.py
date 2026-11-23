"""Настройки служебного процесса миграций."""
from django.db.backends.signals import connection_created

from .base import *
from .database import check_connection

VEQTA_MIGRATION = True
DATABASES = {"default": database_settings(migration=VEQTA_MIGRATION)}
connection_created.connect(check_connection)
