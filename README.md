# VEQTA Platform

VEQTA — внутренний портал на Django и PostgreSQL. Portal отвечает за вход и навигацию. Workwear позволяет оформлять заявки на спецодежду, подбирать сотрудников из 1С, назначать СИЗ, проверять повторы и просматривать историю заявок. Складской учёт и фактическая выдача в модуль не входят.

## Быстрый старт

Нужны Git, Python 3.14 с `venv`, серверные и клиентские утилиты PostgreSQL 18. Базовые версии закреплены в `.python-version` и CI. Клонируйте проект и установите зависимости.

```bash
git clone https://github.com/lazuale/veqta-platform.git
cd veqta-platform
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
```

Далее [создайте локальный кластер PostgreSQL и запустите Django](docs/getting-started.md). Инструкция включает пример первой заявки с вымышленным сотрудником. После входа откройте [/admin/](http://127.0.0.1:8000/admin/) для настройки организаций и СИЗ и [/workwear/](http://127.0.0.1:8000/workwear/) для заявок.

Workwear устанавливается на пустую БД одной начальной миграцией. Для баз прежних версий потребуется отдельная процедура переноса. [Серверная установка](docs/deployment.md) описывает независимые DEV и PROD с Gunicorn, Nginx и systemd.

## Структура

| Путь | Назначение |
|---|---|
| `manage.py`, `config/` | Команды Django, настройки, корневые маршруты и WSGI/ASGI |
| `portal/` | Авторизация, профиль, навигация и общая статика |
| `workwear/` | Модели, Admin, сервисы, импорт, JSON API и интерфейс заявок |
| `deploy/`, `scripts/` | Конфигурация и команды установки, проверки и обновления |
| `tests/`, `.github/workflows/` | Django, Cold Start, нагрузочные и браузерные сценарии |
| `docs/` | Документация проекта |

## Документация

- [Локальный запуск и первый вход](docs/getting-started.md)
- [Архитектура, HTTP API и PostgreSQL](docs/architecture.md)
- [Работа с заявками](docs/workwear.md)
- [Организации и каталог СИЗ](docs/directories.md)
- [Приём справочника 1С](docs/import-1c.md)
- [Установка, конфигурация и обновление сервера](docs/deployment.md)
- [Диагностика, резервное копирование и восстановление](docs/operations.md)
- [Команды тестирования и CI](docs/testing.md)
