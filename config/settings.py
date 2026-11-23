"""Настройки прикладного процесса."""
from django.db.backends.signals import connection_created

from .base import *
from .database import check_connection

VEQTA_MIGRATION = False
DATABASES = {"default": database_settings(migration=VEQTA_MIGRATION)}
connection_created.connect(check_connection)
