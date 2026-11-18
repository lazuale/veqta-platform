"""Проверяем отказы установки на временных файлах и заглушках, без рабочих служб."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "scripts/server_checks.sh"


class DeploymentChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="veqta-deploy-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.bin = self.path / "bin"
        self.bin.mkdir()
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env["PATH"],
                        VEQTA_TEST_DIR=str(self.path))
        self.script("sleep", "#!/bin/sh\nexit 0\n")

    def script(self, name, text):
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o700)

    def shell(self, body, **env):
        return subprocess.run(
            ["bash", "-c", 'set -Eeuo pipefail; source "$1"\n' + body,
             "bash", str(CHECKS)],
            env=self.env | env, cwd=ROOT, capture_output=True, text=True, timeout=15,
        )

    def fake_curl(self):
        self.script("curl", f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
root=pathlib.Path(os.environ["VEQTA_TEST_DIR"])
log=root/"curl.log"
with log.open("a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")
mode=os.environ.get("VEQTA_TEST_MODE", "ok")
count=len(log.read_text().splitlines())
code="503" if mode=="transient" and count==1 else "200"
if mode=="redirect": code="302"
cookie="veqta_dev_csrf" if mode=="wrong_contour" else "veqta_prod_csrf"
attribute="HttpOnly" if mode=="insecure" else "Secure"
print(f"HTTP/1.1 {code} Synthetic\\r\\nSet-Cookie: {cookie}=synthetic; Path=/; {attribute}\\r\\n\\r\\n{code}", end="")
sys.exit(7 if mode=="transport_failure" else 0)
''')

    def http(self, mode):
        self.fake_curl()
        return self.shell(
            "veqta_target prod; DOMAIN=release.example.invalid; veqta_check_https",
            VEQTA_TEST_MODE=mode,
        )

    def test_transient_https_error_retries_before_acceptance(self):
        result = self.http("transient")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.path / "curl.log").read_text().splitlines()]
        self.assertEqual(len(calls), 3)  # две попытки login и одна static
        self.assertIn("/login/", calls[0][-1])
        self.assertIn("/static/", calls[-1][-1])

    def test_other_contour_is_rejected_even_when_static_would_work(self):
        self.fake_curl()
        result = self.shell(
            "veqta_target prod; DOMAIN=release.example.invalid; if veqta_check_https; then echo ACCEPTED; else exit 1; fi",
            VEQTA_TEST_MODE="wrong_contour",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ACCEPTED", result.stdout)
        self.assertNotIn("/static/", (self.path / "curl.log").read_text())

    def test_redirect_is_not_a_successful_login(self):
        result = self.http("redirect")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTP=302", result.stderr)

    def test_cookie_without_secure_is_rejected(self):
        self.assertNotEqual(self.http("insecure").returncode, 0)

    def test_transport_error_is_not_hidden_by_http_200(self):
        result = self.http("transport_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("curl=7", result.stderr)

    @unittest.skipUnless(shutil.which("curl"), "Нужен штатный curl")
    def test_real_loopback_ignores_a_broken_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Set-Cookie", "veqta_prod_csrf=synthetic; Secure; Path=/")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.shell(
                'veqta_wait_http test "http://127.0.0.1:$VEQTA_TEST_PORT/login/" veqta_prod_csrf',
                VEQTA_TEST_PORT=str(server.server_port),
                http_proxy="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
                ALL_PROXY="http://127.0.0.1:9", all_proxy="http://127.0.0.1:9",
                NO_PROXY="", no_proxy="",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def git_repository(self):
        repo = self.path / "repo"
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
        (repo / "source.txt").write_text("synthetic\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Synthetic Test",
                        "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "test"],
                       check=True, capture_output=True)
        self.script("runuser", '#!/bin/sh\nshift 3\nexec "$@"\n')
        return repo

    def git_check(self):
        return self.shell('ACTOR=synthetic; APP="$VEQTA_TEST_DIR/repo"; veqta_clean_checkout')

    def test_clean_git_checkout_is_accepted(self):
        self.git_repository()
        result = self.git_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dirty_git_checkout_is_rejected(self):
        repo = self.git_repository()
        (repo / "source.txt").write_text("changed\n")
        self.assertNotEqual(self.git_check().returncode, 0)

    def test_git_error_is_rejected_even_when_head_is_readable(self):
        repo = self.git_repository()
        (repo / ".git/index").write_bytes(b"invalid index")
        result = self.git_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Git не смог", result.stderr)
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                       capture_output=True).returncode, 0)

    def nginx(self, mode):
        self.script("nginx", '''#!/bin/sh
case "$VEQTA_TEST_MODE" in
  conflict) echo '[warn] conflicting server name "release.example.invalid" on 0.0.0.0:443, ignored' >&2;;
  neighbor) echo '[warn] conflicting server name "other.example.invalid" on 0.0.0.0:443, ignored' >&2;;
  broken) echo 'synthetic syntax error' >&2; exit 1;;
esac
exit 0
''')
        return self.shell("veqta_target prod; DOMAIN=release.example.invalid; veqta_check_nginx", VEQTA_TEST_MODE=mode)

    def test_nginx_target_conflict_with_exit_zero_is_rejected(self):
        self.assertNotEqual(self.nginx("conflict").returncode, 0)

    def test_nginx_other_hostname_warning_is_not_misattributed(self):
        self.assertEqual(self.nginx("neighbor").returncode, 0)

    def test_nginx_syntax_error_is_rejected(self):
        self.assertNotEqual(self.nginx("broken").returncode, 0)

    def test_database_failure_stops_verification(self):
        self.script("runuser", "#!/bin/sh\nexit 1\n")
        result = self.shell("veqta_target prod; veqta_check_database; echo ACCEPTED")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ACCEPTED", result.stdout)

    def hook(self, domains, nginx_status):
        self.script("nginx", '#!/bin/sh\necho nginx >> "$VEQTA_TEST_DIR/hook.log"\nexit "$VEQTA_TEST_NGINX_STATUS"\n')
        self.script("systemctl", '#!/bin/sh\necho systemctl >> "$VEQTA_TEST_DIR/hook.log"\n')
        config = self.path / "config"
        config.mkdir(exist_ok=True)
        (config / "prod.env").write_text("VEQTA_HOSTNAME=release.example.invalid\n")
        (config / "dev.env").write_text("VEQTA_HOSTNAME=dev.example.invalid\n")
        hook = self.path / "renew.sh"
        hook.write_text((ROOT / "deploy/letsencrypt/renew-nginx.sh").read_text().replace("/etc/veqta", str(config)))
        return subprocess.run(["sh", str(hook)],
                              env=self.env | {"RENEWED_DOMAINS": domains,
                                              "VEQTA_TEST_NGINX_STATUS": str(nginx_status)},
                              capture_output=True, text=True, timeout=5)

    def test_renewal_hook_ignores_other_certificates(self):
        self.assertEqual(self.hook("other.example.invalid", 0).returncode, 0)
        self.assertFalse((self.path / "hook.log").exists())

    def test_renewal_hook_checks_before_reload(self):
        self.assertEqual(self.hook("release.example.invalid", 0).returncode, 0)
        self.assertEqual((self.path / "hook.log").read_text().splitlines(), ["nginx", "systemctl"])

    def test_renewal_hook_does_not_reload_invalid_nginx(self):
        self.assertNotEqual(self.hook("dev.example.invalid", 1).returncode, 0)
        self.assertEqual((self.path / "hook.log").read_text().splitlines(), ["nginx"])


class HttpsStaticPermissionsTests(unittest.TestCase):
    """Приватный ключ остаётся закрытым, а CSS доступен воркеру Nginx."""

    def test_public_static_traversable_with_private_key_protected(self):
        from .browser_support import expose_fixture_static
        with tempfile.TemporaryDirectory(prefix="veqta-https-test-") as temporary:
            folder = Path(temporary)
            static = folder / "static" / "admin" / "css"
            static.mkdir(parents=True)
            css = static / "base.css"
            css.write_text("body {}")
            key = folder / "private.key"
            key.write_text("synthetic secret")
            key.chmod(0o600)
            for path in [folder, folder / "static", folder / "static" / "admin", static]:
                path.chmod(0o700)
            css.chmod(0o600)
            expose_fixture_static(folder)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o711)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(css.stat().st_mode & 0o777, 0o644)
            for directory in [folder / "static", folder / "static" / "admin", static]:
                self.assertEqual(directory.stat().st_mode & 0o777, 0o755)

    def test_public_static_symlink_is_rejected_without_exposing_private_key(self):
        from .browser_support import expose_fixture_static
        with tempfile.TemporaryDirectory(prefix="veqta-https-test-") as temporary:
            folder = Path(temporary)
            (folder / "static").mkdir()
            secret = folder / "private.key"
            secret.write_text("synthetic secret")
            secret.chmod(0o600)
            (folder / "static" / "bad.css").symlink_to(secret)
            with self.assertRaisesRegex(RuntimeError, "ссылка"):
                expose_fixture_static(folder)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)


class InstallationConfigurationTests(unittest.TestCase):
    script = DeploymentChecks.script
    shell = DeploymentChecks.shell

    def setUp(self):
        DeploymentChecks.setUp(self)
        self.project = self.path / "project"
        (self.project / "scripts").mkdir(parents=True)
        (self.project / "deploy").mkdir()
        shutil.copy(ROOT / "scripts/create_config.py", self.project / "scripts")
        for contour in ("dev", "prod"):
            shutil.copy(ROOT / f"deploy/{contour}.env.example", self.project / "deploy")
        self.env["VEQTA_TEST_SOURCE"] = str(ROOT)

    def generate(self, contour="prod", hostname="release.example.invalid"):
        args = [sys.executable, str(self.project / "scripts/create_config.py"), "--environment", contour]
        if hostname is not None:
            args += ["--hostname", hostname]
        return subprocess.run(args, capture_output=True, text=True, timeout=10)

    def test_independent_credentials_and_domains_for_both_contours(self):
        secrets_seen = []
        for contour, hostname in (("dev", "dev.example.invalid"), ("prod", "release.example.invalid")):
            result = self.generate(contour, hostname)
            self.assertEqual(result.returncode, 0, result.stderr)
            contents = []
            for suffix in ("", "-migrate"):
                path = self.project / f".env.{contour}{suffix}"
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                text = path.read_text()
                contents.append(text)
                self.assertIn(f"VEQTA_HOSTNAME={hostname}\n", text)
                self.assertIn(f"DB_USER=veqta_{contour}" + ("_owner" if suffix else "") + "\n", text)
                password = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("DB_PASSWORD="))
                self.assertNotIn(password, result.stdout + result.stderr)
                secrets_seen.append(password)
            self.assertNotEqual(contents[0], contents[1])
        self.assertEqual(len(set(secrets_seen)), 4)

    def test_generator_rejects_invalid_or_missing_hostname(self):
        for hostname in (None, "", "https://host.example", "*.example.org", ".example.org", "example.org.",
                         "a..example.org", "-a.example.org", "a-.example.org", "a_b.example.org",
                         "127.0.0.1", "UPPER.example.org", "a" * 64 + ".example.org", "a.example.org;exit"):
            with self.subTest(hostname=hostname):
                self.assertNotEqual(self.generate(hostname=hostname).returncode, 0)
                self.assertFalse((self.project / ".env.prod").exists())
                self.assertFalse((self.project / ".env.prod-migrate").exists())

    def test_generator_refuses_repetition_without_changing_either_file(self):
        self.assertEqual(self.generate().returncode, 0)
        paths = [self.project / f".env.prod{suffix}" for suffix in ("", "-migrate")]
        before = [path.read_bytes() for path in paths]
        self.assertNotEqual(self.generate().returncode, 0)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def load(self, changes=None, contour="prod"):
        hostname = "release.example.invalid" if contour == "prod" else "dev.example.invalid"
        self.assertEqual(self.generate(contour, hostname).returncode, 0)
        config = self.project / f".env.{contour}"
        content = config.read_text()
        for name, value in (changes or {}).items():
            content = "\n".join(f"{name}={value}" if line.startswith(name + "=") else line for line in content.splitlines()) + "\n"
        config.write_text(content)
        self.script("stat", f"#!/bin/sh\necho root:veqta-{contour}:640\n")
        return self.shell(f'veqta_target {contour}; CONFIG="$VEQTA_TEST_DIR/project/.env.{contour}"; '
                          'veqta_load_config; APP="$VEQTA_TEST_SOURCE"; veqta_render_nginx')

    def test_custom_prod_hostname_drives_all_nginx_directives(self):
        result = self.load()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("server_name release.example.invalid;", result.stdout)
        self.assertIn("live/release.example.invalid/fullchain.pem", result.stdout)
        self.assertIn("127.0.0.1:8765", result.stdout)
        self.assertNotIn("__", result.stdout)

    def test_custom_dev_hostname_keeps_separate_port_and_static(self):
        result = self.load(contour="dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("server_name dev.example.invalid;", result.stdout)
        self.assertIn("127.0.0.1:8767", result.stdout)
        self.assertIn("/var/www/veqta/dev-static/", result.stdout)

    def test_configuration_rejects_missing_hostname(self):
        self.assertNotEqual(self.load({"VEQTA_HOSTNAME": ""}).returncode, 0)

    def test_configuration_rejects_invalid_hostname(self):
        self.assertNotEqual(self.load({"VEQTA_HOSTNAME": "invalid..example.org"}).returncode, 0)

    def test_configuration_rejects_wrong_environment(self):
        self.assertNotEqual(self.load({"VEQTA_ENV": "dev"}).returncode, 0)

    def test_configuration_rejects_wrong_database(self):
        self.assertNotEqual(self.load({"DB_NAME": "veqta_dev"}).returncode, 0)

    def test_configuration_does_not_inherit_missing_postgresql_parameter(self):
        self.env['DB_HOST'] = '127.0.0.1'
        self.assertNotEqual(self.load({"DB_HOST": ""}).returncode, 0)

    def test_service_configuration_rejects_owner_role(self):
        self.assertNotEqual(self.load({"DB_USER": "veqta_prod_owner"}).returncode, 0)

    def test_migration_configuration_rejects_other_postgresql_address(self):
        self.assertEqual(self.generate().returncode, 0)
        config = self.project / ".env.prod-migrate"
        config.write_text(config.read_text().replace("DB_PORT=5432", "DB_PORT=6543"))
        self.script("stat", "#!/bin/sh\necho root:root:600\n")
        self.script("runuser", '#!/bin/sh\necho unexpected > "$VEQTA_TEST_DIR/migration-ran"\n')
        result = self.shell('veqta_target prod; DB_HOST=127.0.0.1; DB_PORT=5432; DB_SSLMODE=prefer; '
                            'MIGRATION_CONFIG="$VEQTA_TEST_DIR/project/.env.prod-migrate"; veqta_load_migration_config')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "migration-ran").exists())


class ServerApplyTests(unittest.TestCase):
    script = DeploymentChecks.script
    shell = DeploymentChecks.shell
    fake_curl = DeploymentChecks.fake_curl

    def setUp(self):
        DeploymentChecks.setUp(self)
        self.app = self.path / "opt/veqta-prod"
        (self.app / ".git").mkdir(parents=True)
        (self.app / ".venv/bin").mkdir(parents=True)
        shutil.copytree(ROOT / "deploy", self.app / "deploy")
        (self.app / "requirements.lock").write_text("")
        (self.app / "manage.py").write_text("# synthetic\n")
        python = self.app / ".venv/bin/python"
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o755)
        self.scripts = self.path / "scripts"
        self.scripts.mkdir()
        for name in ("server_checks.sh", "server_apply.sh"):
            text = (ROOT / "scripts" / name).read_text()
            for prefix in ("/opt/", "/etc/", "/var/www/", "/run/"):
                text = text.replace(prefix, str(self.path) + prefix)
            text = text.replace('[[ $EUID -eq 0 ]]', 'true')
            (self.scripts / name).write_text(text)
        for directory in ("etc/veqta", "etc/nginx/sites-available", "etc/nginx/sites-enabled",
                          "etc/systemd/system", "run/lock", "etc/letsencrypt/live/release.example.invalid"):
            (self.path / directory).mkdir(parents=True, exist_ok=True)
        for name in ("fullchain.pem", "privkey.pem"):
            (self.path / "etc/letsencrypt/live/release.example.invalid" / name).write_text("synthetic")
        config = (ROOT / "deploy/prod.env.example").read_text().replace("__HOSTNAME__", "release.example.invalid")
        config = config.replace("DB_PASSWORD=\n", "DB_PASSWORD=synthetic\n")
        config = config.replace("/var/www/", str(self.path) + "/var/www/")
        (self.path / "etc/veqta/prod.env").write_text(config)
        (self.path / "etc/veqta/prod-migrate.env").write_text(config.replace("DB_USER=veqta_prod\n", "DB_USER=veqta_prod_owner\n"))
        self.site = self.path / "etc/nginx/sites-available/veqta-prod"
        self.link = self.path / "etc/nginx/sites-enabled/veqta-prod"
        self.script("runuser", '#!/bin/sh\nshift 3\nexec "$@"\n')
        self.script("id", "#!/bin/sh\nexit 0\n")
        self.script("stat", '''#!/bin/sh
case "$2" in
  %U:%G) echo root:root;;
  %U) echo root;;
  *) case "$3" in *-migrate.env) echo root:root:600;; *) echo root:veqta-prod:640;; esac;;
esac
''')
        self.script("find", "#!/bin/sh\nexit 0\n")
        self.script("git", '''#!/bin/sh
case "$*" in *rev-parse*) echo 1111111111111111111111111111111111111111;; esac
''')
        for name in ("openssl", "chgrp", "chmod", "chown"):
            self.script(name, "#!/bin/sh\nexit 0\n")
        self.script("install", f"#!{sys.executable}\n" + '''
import subprocess, sys
args=[]; source=iter(sys.argv[1:])
for arg in source:
    if arg in ('-o','-g'): next(source)
    else: args.append(arg)
raise SystemExit(subprocess.call(['/usr/bin/install',*args]))
''')
        self.script("nginx", f"#!{sys.executable}\n" + '''
import os, pathlib
path=pathlib.Path(os.environ['VEQTA_TEST_DIR'])/'nginx-count'
count=int(path.read_text())+1 if path.exists() else 1
path.write_text(str(count))
raise SystemExit(1 if os.environ.get('VEQTA_TEST_MODE')=='broken-new-site' and count==2 else 0)
''')
        self.script("systemctl", '#!/bin/sh\necho "$*" >> "$VEQTA_TEST_DIR/systemctl.log"\n')
        self.fake_curl()

    def apply(self, mode="ok"):
        return subprocess.run(["bash", str(self.scripts / "server_apply.sh"), "prod", "--migrate"],
            env=self.env | {"VEQTA_TEST_MODE": mode}, capture_output=True, text=True, timeout=20)

    def test_repeated_apply_accepts_custom_hostname_and_preserves_neighbor(self):
        neighbor = self.path / "etc/nginx/sites-available/neighbor"
        neighbor.write_text("unchanged neighbor")
        for _ in range(2):
            result = self.apply()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("SERVER APPLY: PASS", result.stdout)
            self.assertIn("server_name release.example.invalid;", self.site.read_text())
            self.assertEqual(self.link.resolve(), self.site)
            self.assertEqual(neighbor.read_text(), "unchanged neighbor")
        self.assertNotIn("veqta-dev", (self.path / "systemctl.log").read_text())

    def test_apply_recreates_missing_virtualenv_before_readiness_check(self):
        shutil.rmtree(self.app / ".venv")
        self.script("python3.14", """#!/bin/sh
set -eu
[ "$1" = -m ] && [ "$2" = venv ]
mkdir -p "$3/bin"
printf '#!/bin/sh\nexit 0\n' > "$3/bin/python"
/usr/bin/chmod +x "$3/bin/python"
""")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("SERVER APPLY: PASS", result.stdout)
        self.assertTrue((self.app / ".venv/bin/python").is_file())

    def test_nginx_failure_restores_existing_file_without_reload(self):
        previous = "previous exact site\n"
        self.site.write_text(previous)
        self.link.symlink_to(self.site)
        result = self.apply("broken-new-site")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.site.read_text(), previous)
        self.assertEqual(self.link.resolve(), self.site)
        self.assertFalse((self.path / "systemctl.log").exists())

    def test_insecure_checkout_is_rejected_before_mutation(self):
        self.script("find", "#!/bin/sh\necho /opt/veqta-prod/config/settings.py\n")
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("недоверенный каталог пути checkout", result.stderr)
        self.assertFalse((self.path / "systemctl.log").exists())

    def test_nginx_failure_removes_only_new_site_and_link(self):
        result = self.apply("broken-new-site")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.site.exists())
        self.assertFalse(self.link.is_symlink())
        self.assertFalse((self.path / "systemctl.log").exists())
