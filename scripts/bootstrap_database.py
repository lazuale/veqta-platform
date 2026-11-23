"""Создаём роли и базу с помощью psql и SCRAM-верификаторов libpq."""
import argparse
import os
from pathlib import Path
import subprocess

import psycopg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=("dev", "prod"))
    parser.add_argument("--roles-only", action="store_true", help="Создать роли перед pg_restore")
    args = parser.parse_args()

    environment = os.environ.copy()
    runtime_password = environment.pop("DB_PASSWORD", "")
    migration_password = environment.pop("DB_MIGRATION_PASSWORD", "")
    if not runtime_password or not migration_password or runtime_password == migration_password:
        parser.error("Нужны разные пароли в DB_PASSWORD и DB_MIGRATION_PASSWORD")
    runtime = f"svc_platform_{args.environment}"
    with psycopg.connect(dbname="postgres", connect_timeout=5) as connection:
        for name, role, password in (
            ("VEQTA_RUNTIME_VERIFIER", runtime, runtime_password),
            ("VEQTA_MIGRATOR_VERIFIER", f"{runtime}_migrator", migration_password),
        ):
            environment[name] = connection.pgconn.encrypt_password(
                password.encode(), role.encode(), algorithm=b"scram-sha-256",
            ).decode("ascii")

    sql_file = Path(__file__).resolve().parent.parent / "deploy" / "postgresql" / f"{args.environment}.sql"
    command = ["psql", "-Xw", "-d", "postgres", "-f", str(sql_file)]
    if args.roles_only:
        command.extend(["-v", "roles_only=1"])
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
