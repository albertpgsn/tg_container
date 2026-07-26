# Telegram CRM

Приватная мультиаккаунтная CRM для управления пользовательскими Telegram-сессиями. Backend построен на FastAPI и Telethon, production-контур — PostgreSQL, Nginx и Docker Compose. Панель по умолчанию доступна только на loopback-интерфейсе сервера через SSH-туннель.

> Это не Bot API. Приложение авторизует обычные Telegram-аккаунты через `api_id`/`api_hash` и хранит их пользовательские сессии. Используйте только принадлежащие вам аккаунты и соблюдайте правила Telegram и применимое законодательство.

## Возможности

- подключение нескольких Telegram-аккаунтов по номеру, коду и 2FA;
- зашифрованное хранение Telethon StringSession;
- список диалогов и обновление активной CRM раз в секунду;
- история сообщений, отправка текста и синхронизация статуса «прочитано»;
- фото, видео, GIF, голосовые, аудио, стикеры и документы;
- inline-, URL- и текстовые кнопки ботов;
- глобальный поиск по перепискам;
- Telegram-подобные шапки чатов, usernames, описания и аватары;
- создание личных чатов, супергрупп и каналов;
- закрытие остальных Telegram-сессий с двойным подтверждением;
- ежедневная синхронизация username и peer ID аккаунта;
- несколько администраторов, аудит действий, серверные admin-сессии и CSRF-защита.

## Архитектура

| Компонент | Назначение |
| --- | --- |
| FastAPI | API, авторизация администраторов, CRM и выдача фронтенда |
| Telethon | MTProto-клиенты подключённых Telegram-аккаунтов |
| PostgreSQL | аккаунты, зашифрованные сессии, администраторы, audit log |
| Nginx | приватный gateway, security headers и rate limit входа |
| HTML/CSS/JS | одностраничный интерфейс без отдельного Node.js runtime |

Production-схема:

```text
Рабочий компьютер ── SSH tunnel ──> 127.0.0.1:8080 на сервере
                                          │
                                        Nginx
                                          │
                                        FastAPI ──> Telegram MTProto
                                          │
                                      PostgreSQL
```

PostgreSQL и FastAPI не публикуют порты на хосте. Nginx привязан к `127.0.0.1:8080`, поэтому панель не доступна из интернета напрямую.

## Что понадобится

- Debian или Ubuntu x86_64/arm64;
- пользователь с `sudo` и доступом по SSH-ключу;
- `api_id` и `api_hash`, полученные в [Telegram API development tools](https://my.telegram.org/apps);
- минимум 2 CPU, 2 ГБ RAM и 10 ГБ свободного диска;
- Docker Engine с Compose plugin либо разрешение установщику поставить их из официального APT-репозитория.

Официальные инструкции: [Docker для Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Docker для Debian](https://docs.docker.com/engine/install/debian/), [получение Telegram API ID](https://core.telegram.org/api/obtaining_api_id).

## Быстрая установка на сервер

После публикации репозитория:

```bash
git clone <REPOSITORY_URL> telegram-crm
cd telegram-crm
bash scripts/install_server.sh
```

Установщик:

1. проверит Debian/Ubuntu;
2. установит Docker Engine и Compose plugin из официального Docker APT-репозитория, если их нет;
3. запросит Telegram API ID, API hash и пароль первого администратора;
4. сгенерирует пароль PostgreSQL и Fernet-ключ;
5. создаст `.env.docker` и файлы в `secrets/` с правами `0600`;
6. соберёт и запустит контейнеры;
7. дождётся успешного `http://127.0.0.1:8080/health`.

Полезные режимы:

```bash
bash scripts/install_server.sh --skip-docker-install
bash scripts/install_server.sh --no-start
```

Скрипт идемпотентен в отношении конфигурации: существующие секреты не перезаписываются. При неполном наборе секретов установка останавливается.

## Подключение к панели

С рабочего компьютера:

```bash
ssh -L 8080:127.0.0.1:8080 crm-admin@SERVER_IP
```

Пока SSH-соединение открыто, перейдите на `http://127.0.0.1:8080` и войдите под администратором, указанным при установке.

Не меняйте публикацию gateway на `0.0.0.0`, если перед ним нет отдельно настроенного приватного VPN или проверенного HTTPS reverse proxy.

## Ручная установка

Если Docker уже установлен:

```bash
cp .env.docker.example .env.docker
nano .env.docker

python3 -m venv .setup-venv
.setup-venv/bin/pip install 'argon2-cffi>=23.1,<26.0' 'cryptography>=43,<46'
.setup-venv/bin/python scripts/init_secrets.py

docker compose --env-file .env.docker build --pull
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker ps
curl -fsS http://127.0.0.1:8080/health
```

В `.env.docker` находятся только Telegram API ID и bootstrap username. API hash, пароль БД, URL БД, Fernet-ключ и Argon2id-хеш пароля хранятся в `secrets/` и монтируются как Docker secrets.

## Первый запуск CRM

1. Войдите в админ-панель.
2. Откройте «Подключение».
3. Укажите номер в международном формате.
4. Введите код Telegram.
5. Если включена двухэтапная аутентификация, введите пароль 2FA.
6. Откройте подключённый аккаунт в CRM.

Telegram может показать уведомление о новом входе. Это ожидаемо: CRM создаёт отдельную пользовательскую сессию.

## Управление администраторами

```bash
docker compose --env-file .env.docker exec app python -m app.admin_cli add second-admin
docker compose --env-file .env.docker exec app python -m app.admin_cli disable second-admin
docker compose --env-file .env.docker exec app python -m app.admin_cli enable second-admin
docker compose --env-file .env.docker exec app python -m app.admin_cli list
```

Пароль нового администратора должен содержать минимум 16 символов.

## Эксплуатация

Статус и логи:

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail=200 app gateway db
docker compose --env-file .env.docker logs -f app
```

Обновление приложения:

```bash
git pull --ff-only
docker compose --env-file .env.docker build --pull
docker compose --env-file .env.docker up -d
curl -fsS http://127.0.0.1:8080/health
```

Перезапуск:

```bash
docker compose --env-file .env.docker restart app gateway
```

Остановка без удаления данных:

```bash
docker compose --env-file .env.docker down
```

Не используйте `down -v`, если не хотите удалить PostgreSQL volume.

## Резервное копирование

Создайте каталог с закрытыми правами:

```bash
install -m 700 -d backups
umask 077
docker compose --env-file .env.docker exec -T db \
  sh -c 'PGPASSWORD="$(cat /run/secrets/postgres_password)" pg_dump -U telegram_crm -d telegram_crm -Fc' \
  > "backups/telegram-crm-$(date +%F-%H%M).dump"
```

Отдельно сохраните `secrets/session_encryption_key` в зашифрованном хранилище. Без него Telegram-сессии из резервной копии БД не расшифровать. Не переносите `secrets/` или dump по незашифрованному каналу.

Перед восстановлением остановите `app`, проверьте выбранный dump и выполните процедуру сначала на тестовом сервере.

## Медиа-кэш

Полученные файлы проходят через авторизованный FastAPI endpoint. Ограничения по умолчанию:

- 100 МБ на один файл;
- 200 МБ на весь кэш;
- в Docker кэш расположен в `/tmp/media-cache` и очищается при пересоздании контейнера.

Это не резервное хранилище: отсутствующий файл повторно скачивается из Telegram.

## Локальная разработка

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
# Заполните .env и сгенерируйте Fernet-ключ/Argon2id-хеш.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Заполните .env.
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Локальная БД по умолчанию: `sqlite:///./data/mvp.db`. Production использует PostgreSQL.

## Проверки перед коммитом

```bash
python -m py_compile app/main.py app/models.py app/security.py app/config.py
node --check static/app.js
docker compose --env-file .env.docker config --quiet
bash -n scripts/install_server.sh
```

## Безопасность

Главные правила:

- оставляйте gateway на `127.0.0.1`;
- разрешайте на firewall только SSH или порт приватного VPN;
- отключите SSH password authentication и root login;
- используйте шифрование диска и зашифрованные backup-копии;
- не коммитьте `.env`, `.env.docker`, `secrets/`, `data/` и `backups/`;
- после компрометации сервера смените admin-пароли и отзовите Telegram-сессии;
- регулярно обновляйте ОС, Docker и container images.

Полная модель угроз и hardening checklist находятся в [SECURITY.md](SECURITY.md).

## Ограничения

- CRM работает с пользовательскими Telegram-сессиями, поэтому аккаунты подчиняются flood limits и ограничениям Telegram.
- Некоторые пользователи запрещают добавлять себя в группы — Telegram вернёт privacy error.
- Публичный username для нового канала через CRM пока не назначается; канал создаётся приватным.
- Отправка новых сообщений сейчас текстовая; полученные медиа поддерживаются для просмотра и скачивания.
- Автоматического удаления Telegram-аккаунта из CRM пока нет.

## Лицензия

Лицензия пока не выбрана. До добавления файла `LICENSE` считайте проект приватным и не распространяйте его публично без решения владельца.
