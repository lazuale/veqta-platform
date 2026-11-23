# Проверка VEQTA

Запускайте проверки обычным пользователем из корня проекта с активным Python 3.14. В `PATH` должны быть `initdb`, `pg_ctl`, `psql`, `pg_dump` и `pg_restore` одной основной версии PostgreSQL 19.

```bash
python -m pip install --require-hashes -r requirements-browser.lock
python -m pip check
bash tests/postgresql_release.sh
```

Сценарий запускает PostgreSQL на случайных локальных портах. Он генерирует независимые пароли, создаёт DEV и PROD через серверные SQL-файлы и выполняет Cold Start обоих контуров. В каждом контуре проверяются миграции, Admin, импорт 1С и завершение первой заявки через Runtime. Проверяются отказы при неверной роли и БД, повторный запуск bootstrap и migrate, запреты DDL и `SET ROLE`, межконтурный CONNECT, права новых объектов и защита журнала событий.

Контроль восстановления использует отдельный кластер и новые пароли ролей. Сценарий сравнивает данные, владельцев, индексы, ограничения, триггеры, ACL и права по умолчанию. После восстановления он имитирует прерывание `0002_database_acl` и проверяет повторное применение без изменения прикладных данных.

Для Django ORM создаётся отдельная `test_veqta_dev`. Записи под Migrator в этой базе позволяют проверить сами ограничения и триггеры. Браузерный процесс использует Runtime с теми же ACL, что у нового контура. В конце прогона оба тестовых кластера удаляются.

## Браузеры и нагрузка

Установите Nginx и OpenSSL. Затем установите браузеры и запустите приёмку.

```bash
python -m playwright install --with-deps chromium firefox webkit
bash tests/postgresql_release.sh --browser
```

Сценарий проверяет Chromium, Firefox и WebKit через Gunicorn и Nginx с временным HTTPS-сертификатом. Приёмка покрывает матрицу назначений, массовый подбор, редактирование, завершение, возврат, повторы, историю, фильтры и права Django Admin. Каждый движок проверяет профиль до 1000 сотрудников × 30 позиций. Chromium дополнительно проверяет восемь параллельных HTTP-сеансов и конфликт версий.

Для расширенной проверки доступности укажите путь к `axe.min.js` в переменной `VEQTA_TEST_AXE`. GitHub Actions устанавливает axe-core и сохраняет результаты браузерной проверки в артефакте `rc-browser-evidence`.

## Покрытие Python

```bash
mkdir -p test-results/coverage
export COVERAGE_FILE=test-results/coverage/.coverage
python -m coverage erase
bash tests/postgresql_release.sh
python -m coverage report -m
python -m coverage json -o test-results/coverage/coverage.json
python -m coverage html -d test-results/coverage/html
```

Отчёт показывает покрытие операторов и ветвлений Python для `config/`, `portal/` и `workwear/` вне миграций. Отдельные процессы Gunicorn, браузеры и PostgreSQL проверяются своими сценариями. В CI отчёт публикуется как `python-branch-coverage`.

Тесты серверных скриптов можно запустить командой `python -m unittest tests.test_deployment -v`. Проверка Unix-полномочий `sudo bash tests/linux_permissions.sh` предназначена для одноразового Linux runner.
