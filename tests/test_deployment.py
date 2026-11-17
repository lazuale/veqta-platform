"""Регрессия отказов установки: только временные файлы, loopback и внешние заглушки.

Запуск без БД и root: python -m unittest tests.test_deployment -v.
Никакие системные конфигурации и службы эти тесты не изменяют.
"""
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
            "veqta_target prod; veqta_check_https",
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
            "veqta_target prod; if veqta_check_https; then echo ACCEPTED; else exit 1; fi",
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
  conflict) echo '[warn] conflicting server name "platform.veqta.ru" on 0.0.0.0:443, ignored' >&2;;
  neighbor) echo '[warn] conflicting server name "other.example.invalid" on 0.0.0.0:443, ignored' >&2;;
  broken) echo 'synthetic syntax error' >&2; exit 1;;
esac
exit 0
''')
        return self.shell("veqta_target prod; veqta_check_nginx", VEQTA_TEST_MODE=mode)

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
        return subprocess.run(["sh", str(ROOT / "deploy/letsencrypt/renew-nginx.sh")],
                              env=self.env | {"RENEWED_DOMAINS": domains,
                                              "VEQTA_TEST_NGINX_STATUS": str(nginx_status)},
                              capture_output=True, text=True, timeout=5)

    def test_renewal_hook_ignores_other_certificates(self):
        self.assertEqual(self.hook("other.example.invalid", 0).returncode, 0)
        self.assertFalse((self.path / "hook.log").exists())

    def test_renewal_hook_checks_before_reload(self):
        self.assertEqual(self.hook("platform.veqta.ru", 0).returncode, 0)
        self.assertEqual((self.path / "hook.log").read_text().splitlines(), ["nginx", "systemctl"])

    def test_renewal_hook_does_not_reload_invalid_nginx(self):
        self.assertNotEqual(self.hook("test.platform.veqta.ru", 1).returncode, 0)
        self.assertEqual((self.path / "hook.log").read_text().splitlines(), ["nginx"])
