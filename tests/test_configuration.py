"""Неверные настройки должны останавливать запуск до обращения к базе."""
import os
import secrets
import subprocess
import sys
from importlib import import_module
from unittest.mock import patch
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from config.database import require_target
from workwear.ingest_lock import lock_for_acceptance, lock_for_membership


class ConfigurationTests(SimpleTestCase):
    """Проверяем настройки в отдельном процессе, не используя кэш Django."""
    def settings_process(self, *, module="config.settings", assertions="", **changes):
        env = os.environ.copy()
        # Не наследуем настройки HTTPS при проверке значений по умолчанию.
        env.pop("DJANGO_SECURE_COOKIES", None)
        env.update(VEQTA_ENV="dev", DB_NAME="veqta_dev", DB_USER="veqta_dev",
                   DB_PASSWORD=secrets.token_urlsafe(32), DB_HOST="127.0.0.1", DB_PORT="5432",
                   DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_ALLOWED_HOSTS="localhost",
                   DJANGO_DEBUG="1", DJANGO_TRUST_PROXY="0", DJANGO_CSRF_TRUSTED_ORIGINS="")
        env.update(changes)
        code = f"from importlib import import_module; settings = import_module({module!r}); " + assertions
        return subprocess.run([sys.executable, "-c", code], env=env,
                              capture_output=True, timeout=10).returncode

    def test_valid_dev_configuration(self):
        self.assertEqual(self.settings_process(), 0)

    def test_schema_owner_is_bound_to_its_environment(self):
        self.assertEqual(self.settings_process(DB_USER="veqta_dev_owner"), 0)
        self.assertNotEqual(self.settings_process(DB_USER="veqta_prod_owner"), 0)
        self.assertNotEqual(self.settings_process(DB_USER="postgres"), 0)

    def test_database_and_role_must_match_environment(self):
        for changes in ({"DB_NAME": "veqta_prod"}, {"DB_USER": "veqta_prod"}, {"VEQTA_ENV": "unknown"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.settings_process(**changes), 0)

    def test_secret_and_hosts_required(self):
        for changes in ({"DJANGO_SECRET_KEY": ""}, {"DJANGO_SECRET_KEY": "short"},
                        {"DJANGO_ALLOWED_HOSTS": "*"}, {"DJANGO_ALLOWED_HOSTS": ""}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.settings_process(**changes), 0)

    def test_prod_forbids_debug_and_requires_trusted_proxy(self):
        prod = {"VEQTA_ENV": "prod", "DB_NAME": "veqta_prod", "DB_USER": "veqta_prod",
                "DJANGO_DEBUG": "0", "DJANGO_TRUST_PROXY": "1"}
        self.assertEqual(self.settings_process(**prod), 0)
        for changes in ({"DJANGO_DEBUG": "1"}, {"DJANGO_TRUST_PROXY": "0"},
                        {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://localhost"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.settings_process(**(prod|changes)), 0)

    def test_boolean_typo_rejected(self):
        self.assertNotEqual(self.settings_process(DJANGO_DEBUG="yes"), 0)

    def test_secure_cookies_reject_missing_https_proxy_and_insecure_prod(self):
        prod = {"VEQTA_ENV": "prod", "DB_NAME": "veqta_prod",
                "DB_USER": "veqta_prod", "DJANGO_DEBUG": "0",
                "DJANGO_TRUST_PROXY": "1", "DJANGO_SECURE_COOKIES": "0"}
        self.assertNotEqual(self.settings_process(**prod), 0)
        self.assertNotEqual(self.settings_process(
            DJANGO_SECURE_COOKIES="1", DJANGO_TRUST_PROXY="0",
        ), 0)

    def test_default_browser_server_uses_isolated_http_settings(self):
        self.assertEqual(self.settings_process(module="config.testing", VEQTA_TEST_HTTPS="0",
            assertions="assert settings.DEBUG; assert not settings.SESSION_COOKIE_SECURE; "
                       "assert settings.SECURE_PROXY_SSL_HEADER is None; "
                       "assert settings.DATABASES['default']['NAME'] == 'test_veqta_dev'"), 0)

    def test_https_browser_server_disables_debug_and_requires_secure_cookies(self):
        self.assertEqual(self.settings_process(module="config.testing", VEQTA_TEST_HTTPS="1",
            assertions="assert not settings.DEBUG; assert settings.SESSION_COOKIE_SECURE; "
                       "assert settings.CSRF_COOKIE_SECURE; assert settings.SECURE_SSL_REDIRECT; "
                       "assert settings.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https'); "
                       "assert settings.DATABASES['default']['NAME'] == 'test_veqta_dev'"), 0)

    def test_invalid_https_test_flag_is_rejected(self):
        self.assertNotEqual(self.settings_process(module="config.testing", VEQTA_TEST_HTTPS="yes"), 0)

    def test_browser_server_cannot_use_production_environment(self):
        self.assertNotEqual(self.settings_process(module="config.testing", VEQTA_TEST_HTTPS="1",
            VEQTA_ENV="prod", DB_NAME="veqta_prod", DB_USER="veqta_prod",
            DJANGO_DEBUG="0", DJANGO_TRUST_PROXY="1"), 0)


class RuntimeGuardTests(SimpleTestCase):
    """Моделируем отказы до обращения к настоящей базе."""

    def test_database_target_checks_vendor_role_and_actual_connection(self):
        with patch("config.database.settings") as settings, patch("config.database.connection") as db:
            settings.VEQTA_ENV = "dev"
            db.vendor = "postgresql"
            db.settings_dict = {"NAME": "veqta_dev", "USER": "veqta_dev"}
            db.cursor.return_value.__enter__.return_value.fetchone.return_value = ("veqta_dev", "veqta_dev")
            require_target("dev", "veqta_dev")
            with self.assertRaises(CommandError):
                require_target("dev", "veqta_prod")
            db.vendor = "sqlite"
            with self.assertRaisesRegex(CommandError, "Неверная целевая база"):
                require_target("dev", "veqta_dev")
            db.vendor = "postgresql"
            db.settings_dict["USER"] = "postgres"
            with self.assertRaisesRegex(CommandError, "Имя базы или роль"):
                require_target("dev", "veqta_dev")
            db.settings_dict["USER"] = "veqta_dev"
            db.cursor.return_value.__enter__.return_value.fetchone.return_value = ("veqta_prod", "veqta_dev")
            with self.assertRaisesRegex(CommandError, "PostgreSQL подключила команду"):
                require_target("dev", "veqta_dev")

    def test_ingest_lock_requires_transaction_and_postgresql(self):
        with patch("workwear.ingest_lock.transaction.get_connection") as transaction_connection, \
             patch("workwear.ingest_lock.connection") as database:
            transaction_connection.return_value.in_atomic_block = False
            with self.assertRaisesRegex(RuntimeError, "transaction.atomic"):
                lock_for_membership()
            transaction_connection.return_value.in_atomic_block = True
            database.vendor = "sqlite"
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                lock_for_acceptance()

    def test_initialize_rejects_existing_schema(self):
        from workwear.management.commands.veqta_initialize import Command

        with patch("workwear.management.commands.veqta_initialize.require_target"), \
             patch("workwear.management.commands.veqta_initialize.connection") as database:
            database.introspection.table_names.return_value = ["django_migrations"]
            with self.assertRaisesRegex(CommandError, "База уже содержит таблицы"):
                Command().handle(environment="dev", database_name="veqta_dev")

    def test_asgi_and_wsgi_entrypoints_exist(self):
        self.assertIsNotNone(import_module("config.asgi").application)
        self.assertIsNotNone(import_module("config.wsgi").application)
