"""Неверные настройки должны останавливать запуск до обращения к базе."""
import os
import secrets
import subprocess
import sys
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
        env.update(DJANGO_SETTINGS_MODULE=module, VEQTA_ENV="dev", DB_NAME="veqta_dev", DB_USER="svc_platform_dev",
                   DB_PASSWORD=secrets.token_urlsafe(32), DB_HOST="127.0.0.1", DB_PORT="5432", DB_SSLMODE="prefer",
                   DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_ALLOWED_HOSTS="localhost",
                   DJANGO_DEBUG="1", DJANGO_TRUST_PROXY="0", DJANGO_CSRF_TRUSTED_ORIGINS="")
        env.update(changes)
        code = f"from importlib import import_module; settings = import_module({module!r}); " + assertions
        return subprocess.run([sys.executable, "-c", code], env=env,
                              capture_output=True, timeout=10).returncode

    def test_valid_dev_configuration(self):
        self.assertEqual(self.settings_process(), 0)

    def test_settings_accept_only_their_contour_and_role(self):
        for contour, other in (("dev", "prod"), ("prod", "dev")):
            changes = {"VEQTA_ENV": contour, "DB_NAME": f"veqta_{contour}",
                       "DJANGO_DEBUG": "0", "DJANGO_TRUST_PROXY": "1"}
            for module, suffix in (("config.settings", ""), ("config.migration", "_migrator")):
                other_suffix = "" if suffix else "_migrator"
                with self.subTest(contour=contour, module=module):
                    self.assertEqual(self.settings_process(module=module, DB_USER=f"svc_platform_{contour}{suffix}", **changes), 0)
                    for user in (f"svc_platform_{other}{suffix}", f"svc_platform_{contour}{other_suffix}", "postgres"):
                        self.assertNotEqual(self.settings_process(module=module, DB_USER=user, **changes), 0)

    def test_http_entrypoints_use_runtime_settings(self):
        self.assertEqual(self.settings_process(assertions=
            "assert import_module('config.asgi').application; assert import_module('config.wsgi').application"), 0)

    def test_http_entrypoints_reject_migration_settings(self):
        for interface in ("wsgi", "asgi"):
            with self.subTest(interface=interface):
                self.assertNotEqual(self.settings_process(module="config.migration", DB_USER="svc_platform_dev_migrator",
                    assertions=f"import_module('config.{interface}')"), 0)

    def test_database_and_role_must_match_environment(self):
        for changes in ({"DB_NAME": "veqta_prod"}, {"DB_USER": "svc_platform_prod"}, {"VEQTA_ENV": "unknown"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.settings_process(**changes), 0)

    def test_remote_database_requires_full_tls_verification(self):
        for mode in ("disable", "allow", "prefer", "require", "verify-ca"):
            with self.subTest(mode=mode):
                self.assertNotEqual(
                    self.settings_process(DB_HOST="db.example.invalid", DB_SSLMODE=mode),
                    0,
                )
        self.assertEqual(
            self.settings_process(DB_HOST="db.example.invalid", DB_SSLMODE="verify-full"),
            0,
        )
        self.assertNotEqual(self.settings_process(DB_SSLMODE="unknown"), 0)

    def test_secret_and_hosts_required(self):
        for changes in ({"DJANGO_SECRET_KEY": ""}, {"DJANGO_SECRET_KEY": "short"},
                        {"DJANGO_ALLOWED_HOSTS": "*"}, {"DJANGO_ALLOWED_HOSTS": ""}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.settings_process(**changes), 0)

    def test_prod_forbids_debug_and_requires_trusted_proxy(self):
        prod = {"VEQTA_ENV": "prod", "DB_NAME": "veqta_prod", "DB_USER": "svc_platform_prod",
                "DJANGO_DEBUG": "0", "DJANGO_TRUST_PROXY": "1"}
        self.assertEqual(self.settings_process(**prod), 0)
        self.assertEqual(self.settings_process(
            **prod,
            assertions="assert settings.SECURE_HSTS_SECONDS == 31536000; assert not settings.SECURE_HSTS_PRELOAD",
        ), 0)
        for changes in ({"DJANGO_DEBUG": "1"}, {"DJANGO_TRUST_PROXY": "0"},
                        {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://localhost"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.settings_process(**(prod|changes)), 0)

    def test_boolean_typo_rejected(self):
        self.assertNotEqual(self.settings_process(DJANGO_DEBUG="yes"), 0)

    def test_secure_cookies_reject_missing_https_proxy_and_insecure_prod(self):
        prod = {"VEQTA_ENV": "prod", "DB_NAME": "veqta_prod",
                "DB_USER": "svc_platform_prod", "DJANGO_DEBUG": "0",
                "DJANGO_TRUST_PROXY": "1", "DJANGO_SECURE_COOKIES": "0"}
        self.assertNotEqual(self.settings_process(**prod), 0)
        self.assertNotEqual(self.settings_process(
            DJANGO_SECURE_COOKIES="1", DJANGO_TRUST_PROXY="0",
        ), 0)

    def test_https_browser_server_disables_debug_and_requires_secure_cookies(self):
        self.assertEqual(self.settings_process(module="config.testing",
            assertions="assert not settings.DEBUG; assert settings.SESSION_COOKIE_SECURE; "
                       "assert settings.CSRF_COOKIE_SECURE; assert settings.SECURE_SSL_REDIRECT; "
                       "assert settings.SECURE_HSTS_SECONDS == 31536000; assert not settings.SECURE_HSTS_PRELOAD; "
                       "assert settings.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https'); "
                       "assert settings.DATABASES['default']['NAME'] == 'test_veqta_dev'"), 0)

    def test_browser_server_cannot_use_production_environment(self):
        self.assertNotEqual(self.settings_process(module="config.testing",
            VEQTA_ENV="prod", DB_NAME="veqta_prod", DB_USER="svc_platform_prod",
            DJANGO_DEBUG="0", DJANGO_TRUST_PROXY="1"), 0)


class RuntimeGuardTests(SimpleTestCase):
    """Моделируем отказы до обращения к настоящей базе."""

    def test_database_target_checks_vendor_role_and_actual_connection(self):
        with patch("config.database.settings") as settings, patch("config.database.connection") as db:
            settings.VEQTA_ENV = "dev"
            db.vendor = "postgresql"
            db.settings_dict = {"NAME": "veqta_dev", "USER": "svc_platform_dev"}
            db.cursor.return_value.__enter__.return_value.fetchone.return_value = ("veqta_dev", "svc_platform_dev", "svc_platform_dev", True)
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
            db.settings_dict["USER"] = "svc_platform_dev"
            db.cursor.return_value.__enter__.return_value.fetchone.return_value = ("veqta_prod", "svc_platform_dev", "svc_platform_dev", True)
            with self.assertRaisesRegex(CommandError, "Подключение PostgreSQL"):
                require_target("dev", "veqta_dev")

    def test_database_target_checks_configured_server_role_and_operation(self):
        with patch("config.database.settings") as settings, patch("config.database.connection") as db:
            settings.VEQTA_ENV = "dev"
            db.vendor = "postgresql"
            cursor = db.cursor.return_value.__enter__.return_value
            for role, migration in (("svc_platform_dev", False), ("svc_platform_dev_migrator", True)):
                with self.subTest(role=role):
                    db.settings_dict = {"NAME": "veqta_dev", "USER": role}
                    cursor.fetchone.return_value = ("veqta_dev", role, role, True)
                    require_target("dev", "veqta_dev", migration=migration)
                    with self.assertRaisesRegex(CommandError, "Имя базы или роль"):
                        require_target("dev", "veqta_dev", migration=not migration)
                    cursor.fetchone.return_value = ("veqta_dev", role, "postgres", False)
                    with self.assertRaisesRegex(CommandError, "Подключение PostgreSQL"):
                        require_target("dev", "veqta_dev", migration=migration)

    def test_remote_connection_requires_an_encrypted_session(self):
        with patch("config.database.settings", VEQTA_ENV="dev"), \
             patch("config.database.connection") as db:
            db.vendor = "postgresql"
            db.settings_dict = {
                "NAME": "veqta_dev", "USER": "svc_platform_dev",
                "HOST": "db.example.invalid",
            }
            cursor = db.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("veqta_dev", "svc_platform_dev", "svc_platform_dev", True, True)
            require_target("dev", "veqta_dev")
            cursor.fetchone.return_value = ("veqta_dev", "svc_platform_dev", "svc_platform_dev", True, False)
            with self.assertRaisesRegex(CommandError, "TLS"):
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

    def test_connection_closes_on_unexpected_identity(self):
        from config.database import check_connection

        with patch("config.database.settings", VEQTA_ENV="dev", VEQTA_MIGRATION=True), \
             patch("config.database.verify_connection", side_effect=CommandError("Неверная роль")), \
             patch("config.database.connection") as database:
            database.settings_dict = {"NAME": "veqta_dev"}
            with self.assertRaisesRegex(CommandError, "Неверная роль"):
                check_connection(None, database)
            database.close.assert_called_once_with()
