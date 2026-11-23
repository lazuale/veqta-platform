"""Проверки владельца объектов работают только с одноразовой базой DEV."""
from config.base import *

if VEQTA_ENV != "dev":
    raise ImproperlyConfigured("Тесты разрешены только в изолированном DEV")
VEQTA_MIGRATION = True
DATABASES = {"default": database_settings(migration=True)}
DATABASES["default"].update(NAME="test_veqta_dev", TEST={"NAME": "test_veqta_dev"})
