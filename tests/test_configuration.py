"""Ошибочная конфигурация должна останавливать процесс до обращения к данным."""
import os
import secrets
import subprocess
import sys
from django.test import SimpleTestCase


class ConfigurationTests(SimpleTestCase):
    """Отдельный процесс исключает влияние уже загруженных настроек Django."""
    def settings_process(self, **changes):
        """Импортировать настройки с синтетическим ключом и явным набором переменных."""
        env = os.environ.copy()
        env.update(VEQTA_ENV="dev", DB_NAME="veqta_dev", DB_USER="veqta_dev",
                   DB_PASSWORD=secrets.token_urlsafe(32), DB_HOST="127.0.0.1", DB_PORT="5432",
                   DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_ALLOWED_HOSTS="localhost",
                   DJANGO_DEBUG="1", DJANGO_TRUST_PROXY="0", DJANGO_CSRF_TRUSTED_ORIGINS="")
        env.update(changes)
        return subprocess.run([sys.executable, "-c", "import config.settings"], env=env,
                              capture_output=True, timeout=10).returncode

    def test_valid_dev_configuration(self):
        self.assertEqual(self.settings_process(), 0)

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
