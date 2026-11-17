# Первая публикация исходников

Репозиторий предназначен для публикации исходников без секретов и рабочих данных. Условия стороннего использования кода автор определяет отдельно; эта инструкция не назначает проекту лицензию.

Публикация выполняется владельцем локально. Из корня распакованного проекта:

```bash
python3 scripts/check_source.py
git init -b main
git add .
python3 scripts/check_source.py --staged
git diff --cached --stat
git diff --cached --check
git commit -m "Initial VEQTA Platform source"
read -r -p 'URL созданного репозитория GitHub: ' REPOSITORY_URL
git remote add origin "$REPOSITORY_URL"
git push -u origin main
```

Перед commit просмотрите состав staged-файлов. `.env.dev`, `.env.prod`, venv, рабочие CSV, дампы, журналы и собранная статика исключены через `.gitignore`. Команда проверки файлов ищет запрещённые типы, приватные адреса и известные формы ключей; она помогает проверке состава, но не заменяет просмотр собственных добавленных данных.

Если Git ещё не настроен, задайте свои реальные `user.name` и `user.email` штатными командами git config локально. Готовых значений и учётных данных для GitHub в проекте нет.
