"""Общий временный сервер и безопасное чтение БД для синхронного Playwright."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class QueryCountMiddleware:
    """Число SQL-запросов для измерений временного сервера config.testing."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.db import connection
        queries = 0
        def count(execute, sql, params, many, context):
            nonlocal queries
            queries += 1
            return execute(sql, params, many, context)
        with connection.execute_wrapper(count):
            response = self.get_response(request)
        response["X-Veqta-Test-SQL"] = str(queries)
        if connection.connection is not None:
            response["X-Veqta-Test-DB-User"] = connection.connection.info.user
        return response


@dataclass(frozen=True)
class BrowserServer:
    url: str
    ca_file: Path | None = None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_process(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def sign_in(page, base, username, password):
    """Дождаться ответа входа и установки сессии перед следующей навигацией."""
    from playwright.sync_api import expect
    page.goto(base + "/login/")
    page.locator('[name="username"]').fill(username)
    page.locator('[name="password"]').fill(password)
    page.locator('button[type="submit"]').click()
    expect(page).to_have_url(base + "/")


@contextmanager
def live_server(*, stack="runserver", nginx_executable=None, artifacts=None):
    environment = os.environ.copy()
    environment.update(DB_USER="veqta_dev", DB_PASSWORD=environment.pop("DB_RUNTIME_PASSWORD"))
    environment.pop("DB_OWNER_PASSWORD", None)
    environment.pop("DB_MIGRATION_PASSWORD", None)
    if stack == "nginx":
        with https_server(environment, nginx_executable, artifacts) as server:
            yield server
        return
    if stack != "runserver":
        raise ValueError("Неизвестный сервер приёмки")
    environment["VEQTA_TEST_HTTPS"] = "0"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as log:
        server = subprocess.Popen([
            sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}",
            "--noreload", "--settings=config.testing",
        ], cwd=Path(__file__).resolve().parent.parent, stdout=log, stderr=log, env=environment)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/login/", timeout=1).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Тестовый сервер завершился до запуска")
                    time.sleep(0.1)
            else:
                raise RuntimeError("Тестовый сервер не ответил")
            yield BrowserServer(base)
        finally:
            stop_process(server)



def expose_fixture_static(folder):
    """Разрешить воркеру Nginx чтение только публичной статики HTTPS-стенда.

    TemporaryDirectory и umask 077 закрывают временный корень и CSS даже
    от непривилегированного www-data. Секретный private.key не затрагивается.
    """
    public = folder / "static"
    if folder.is_symlink() or not public.is_dir() or public.is_symlink():
        raise RuntimeError("Некорректный каталог статики HTTPS-стенда")
    paths = [public, *public.rglob("*")]
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in paths):
        raise RuntimeError("Неожиданная ссылка или тип объекта в тестовой статике")
    # Временный каталог остаётся недоступен для перечисления: только проход.
    # Полностью открыта для чтения лишь раздаваемая статика.
    for path in paths:
        path.chmod(0o755 if path.is_dir() else 0o644)
    folder.chmod(0o711)


@contextmanager
def https_server(environment, nginx_executable, artifacts):
    """Приватные Gunicorn/Nginx, свой сертификат и стандартный шаблон сайта."""
    executable = nginx_executable or shutil.which("nginx")
    if not executable or not shutil.which("openssl"):
        raise RuntimeError("HTTPS-приёмке нужны Nginx и OpenSSL в изолированной среде")
    root = Path(__file__).resolve().parent.parent
    destination = Path(artifacts).resolve() if artifacts else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="veqta-https-") as temporary:
        folder = Path(temporary)
        app_port, http_port, tls_port = free_port(), free_port(), free_port()
        while len({app_port, http_port, tls_port}) != 3:
            app_port, http_port, tls_port = free_port(), free_port(), free_port()
        base = f"https://127.0.0.1:{tls_port}"
        certificate, key = folder / "certificate.pem", folder / "private.key"
        # CA доверяется только тестовым клиентом; системное хранилище не меняется.
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-keyout", str(key), "-out", str(certificate), "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        runtime = environment | {"DJANGO_SETTINGS_MODULE": "config.testing", "VEQTA_TEST_HTTPS": "1",
            "DJANGO_DEBUG": "0", "DJANGO_TRUST_PROXY": "1", "DJANGO_SECURE_COOKIES": "1",
            "DJANGO_CSRF_TRUSTED_ORIGINS": base, "DJANGO_STATIC_ROOT": str(folder / "static")}
        with (folder / "collectstatic.log").open("wb") as log:
            subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"],
                cwd=root, env=runtime, stdout=log, stderr=log, check=True)
        key.chmod(0o600)
        expose_fixture_static(folder)
        site = (root / "deploy/nginx/veqta.conf").read_text()
        site = site.replace("__HOSTNAME__", "127.0.0.1").replace("__PORT__", str(app_port))
        site = site.replace("__STATIC_ROOT__", str(folder / "static"))
        site = site.replace("listen 80;", f"listen 127.0.0.1:{http_port};")
        site = site.replace("listen 443 ssl;", f"listen 127.0.0.1:{tls_port} ssl;")
        site = site.replace("https://127.0.0.1$request_uri", base + "$request_uri")
        site = site.replace("/etc/letsencrypt/live/127.0.0.1/fullchain.pem", str(certificate))
        site = site.replace("/etc/letsencrypt/live/127.0.0.1/privkey.pem", str(key))
        (folder / "site.conf").write_text(site)
        temporary_paths = " ".join(f"{kind}_temp_path {folder}/{kind};" for kind in
                                   ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
        config = folder / "nginx.conf"
        mime_types = "types { text/css css; application/javascript js; image/svg+xml svg; image/png png; image/x-icon ico; }"
        config.write_text(f"worker_processes 1;\nerror_log {folder}/nginx-error.log;\npid {folder}/nginx.pid;\n"
            f"events {{ worker_connections 256; }}\nhttp {{ {mime_types} {temporary_paths} access_log off; include {folder}/site.conf; }}\n")
        processes = []
        with (folder / "gunicorn.log").open("wb") as gunicorn_log, (folder / "nginx.log").open("wb") as nginx_log:
            try:
                subprocess.run([str(executable), "-t", "-p", str(folder) + "/", "-c", str(config)],
                    stdout=nginx_log, stderr=nginx_log, check=True)
                processes.append(subprocess.Popen([sys.executable, "-m", "gunicorn", "config.wsgi:application",
                    "--bind", f"127.0.0.1:{app_port}", "--workers", "2", "--timeout", "120",
                    "--error-logfile", "-", "--no-control-socket"],
                    cwd=root, env=runtime, stdout=gunicorn_log, stderr=gunicorn_log))
                processes.append(subprocess.Popen([str(executable), "-p", str(folder) + "/", "-c", str(config),
                    "-g", "daemon off;"], stdout=nginx_log, stderr=nginx_log))
                context = ssl.create_default_context(cafile=str(certificate))
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
                for _ in range(100):
                    if any(process.poll() is not None for process in processes):
                        raise RuntimeError("Процесс HTTPS-стенда завершился до готовности; см. журналы")
                    try:
                        with opener.open(base + "/login/", timeout=1) as response:
                            assert response.status == 200
                            assert "Secure" in response.headers.get("Set-Cookie", "")
                            assert response.headers["X-Frame-Options"] == "DENY"
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("HTTPS-стенд не ответил")
                untrusted = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=ssl.create_default_context()))
                try:
                    untrusted.open(base + "/login/", timeout=5).close()
                except urllib.error.URLError as error:
                    assert isinstance(error.reason, ssl.SSLCertVerificationError)
                else:
                    raise AssertionError("Клиент принял недоверенный сертификат")
                try:
                    with socket.create_connection(("127.0.0.1", tls_port), timeout=5) as connection:
                        context.wrap_socket(connection, server_hostname="unexpected.invalid").close()
                except ssl.SSLCertVerificationError:
                    pass
                else:
                    raise AssertionError("Клиент принял сертификат чужого hostname")
                import http.client
                connection = http.client.HTTPConnection("127.0.0.1", http_port, timeout=5)
                try:
                    connection.request("GET", "/login/")
                    response = connection.getresponse()
                    assert response.status == 301 and response.getheader("Location") == base + "/login/"
                finally:
                    connection.close()
                with opener.open(base + "/static/admin/css/base.css", timeout=5) as response:
                    assert response.status == 200 and response.headers.get("Cache-Control") == "no-cache"
                    assert response.headers.get_content_type() == "text/css"
                if destination:
                    (destination / "server-stack.json").write_text(json.dumps({
                        "stack": "nginx", "application_server": "gunicorn", "workers": 2,
                        "debug": False, "https_verified_with_fixture_ca": True,
                        "untrusted_certificate_rejected": True, "wrong_hostname_rejected": True,
                        "secure_csrf_cookie": True, "http_redirect": 301, "static": 200,
                        "database_role": runtime["DB_USER"], "migration_secret_in_server": any(name in runtime for name in
                            ("DB_OWNER_PASSWORD", "DB_MIGRATION_PASSWORD", "DB_RUNTIME_PASSWORD")),
                        "browser_ignore_https_errors": True,
                        "tls_validation": "explicit fixture CA and hostname checks in the HTTP client",
                    }, ensure_ascii=False, indent=2))
                yield BrowserServer(base, certificate)
            finally:
                for process in reversed(processes):
                    stop_process(process)
                if destination:
                    for name in ("collectstatic.log", "gunicorn.log", "nginx.log", "nginx-error.log"):
                        path = folder / name
                        if path.exists():
                            shutil.copyfile(path, destination / name)


def database_call(callback):
    # Sync Playwright держит event loop. ORM выполняется в отдельном потоке,
    # без отключения штатной защиты Django от синхронных обращений из asyncio.
    def run():
        from django.db import connections
        try:
            return callback()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


@contextmanager
def delayed_requests(page, pattern):
    """Удержать ответ до выхода из блока; одинаково работает во всех движках."""
    pending = []
    def hold(route):
        pending.append(route)
    page.route(pattern, hold)
    try:
        yield pending
    finally:
        for route in pending:
            route.continue_()
        page.unroute(pattern, hold)
