# VEQTA Platform

Внутренний портал на Django и PostgreSQL. Portal содержит вход, профиль и навигацию. Workwear ведёт заявки на спецодежду: подбор сотрудников из 1С, массовые назначения, контроль повторов и историю действий.

Один код обслуживает два независимых контура: `veqta_dev` и `veqta_prod`. Данные, роли, ключи и cookies у них разные. Производственный сервер использует Gunicorn и Nginx с HTTPS.

В проекте нет кадровых выгрузок, сотрудников, заявок, учётных записей или готовых секретов. Утверждённый каталог СИЗ загружается отдельно из CSV на 11 позиций. В исходном приложении такого CSV нет; тестовые позиции его не заменяют.

## Первый запуск DEV

Нужны Linux или WSL, Python с модулем venv и запущенная PostgreSQL 18. Команды выполняются из корня проекта; роль и база ещё не должны существовать.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python scripts/create_config.py --environment dev
set -a
. ./.env.dev
set +a
sudo --preserve-env=DB_PASSWORD -u postgres psql -X -v ON_ERROR_STOP=1 -f deploy/postgresql/dev.sql
python manage.py veqta_initialize --environment dev --database-name veqta_dev
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Открыть http://127.0.0.1:8000/. Заявки находятся по адресу `/workwear/`, пользователи и группы — `/admin/`. После первого запуска справочник сотрудников и каталог пусты.

## Состав

| Каталог | Назначение |
|---|---|
| `config/` | Настройки, WSGI/ASGI, маршруты и проверка контура |
| `portal/` | Штатная авторизация Django, страницы и общие стили |
| `workwear/` | Модели, транзакционные сервисы, API, импорт и интерфейс |
| `deploy/` | Примеры окружения, создание БД, systemd и Nginx |
| `tests/` | Синтетические проверки, конкуренция и браузерный сценарий |
| `scripts/` | Создание локальных настроек и проверка публикуемых файлов |
| `docs/` | Установка, бизнес-правила, разработка и эксплуатация |

Начать с [установки](docs/installation.md). Далее: [настройки](docs/configuration.md), [архитектура](docs/architecture.md), [Workwear](docs/workwear.md), [импорт](docs/import_1c.md), [проверки](docs/testing.md), [эксплуатация](docs/operations.md), [публикация исходников](docs/publication.md).
