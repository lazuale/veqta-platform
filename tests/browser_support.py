"""Общий временный сервер и безопасное чтение БД для синхронного Playwright."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


@contextmanager
def live_server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as log:
        server = subprocess.Popen([
            sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}",
            "--noreload", "--settings=config.testing",
        ], cwd=Path(__file__).resolve().parent.parent, stdout=log, stderr=log)
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
            yield base
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


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
