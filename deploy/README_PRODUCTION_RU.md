# KABAN Production 24/7 — Linux + Docker Compose

Этот runbook предназначен для одного Docker-capable Linux host. KABAN остаётся provider-neutral: сервер может быть VPS, домашний Linux-хост или другой узел с Docker Engine и Compose plugin.

## 1. Установка Docker

Установите Docker Engine и Compose plugin по инструкции вашего Linux-дистрибутива, затем включите автозапуск Docker после reboot:

```bash
sudo systemctl enable --now docker
```

Проверьте:

```bash
docker version
docker compose version
```

## 2. Разместите release на сервере

Распакуйте KABAN в отдельную директорию и перейдите в неё. Не храните реальные секреты в ZIP или Git.

## 3. Создайте `.env`

```bash
cp .env.example .env
```

Заполните в `.env` реальные `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Для bind-mount permissions задайте UID/GID текущего Linux-пользователя:

```bash
id -u
id -g
```

Значения перенесите в `KABAN_UID` и `KABAN_GID` в `.env`.

## 4. Настройте доступ к Review Console

Для первого локального/private smoke оставьте:

```dotenv
KABAN_PUBLIC_URL=http://localhost
```

Создайте Caddy hash интерактивно, чтобы plaintext-пароль не попадал в историю shell:

```bash
docker run --rm -it caddy:2-alpine caddy hash-password
```

Сохраните username в `KABAN_ADMIN_USER`, полученный hash — в `KABAN_ADMIN_PASSWORD_HASH`. Bcrypt hash содержит символы `$`, поэтому в `.env` заключите значение hash в **одинарные кавычки**, чтобы Compose не интерпретировал его части как переменные, например `KABAN_ADMIN_PASSWORD_HASH='$2a$...'`.

Для публичного Internet-доступа укажите реальный домен в `KABAN_PUBLIC_URL`; Caddy сможет получить HTTPS автоматически при корректном DNS. Публичный unauthenticated HTTP не поддерживается. Без домена используйте private network/VPN и не открывайте Review Console в Интернет напрямую.

## 5. Подготовьте persistent data

KABAN хранит production data на host:

```text
data/generated
data/runtime
```

Для первого переноса shipped history выполните:

```bash
python deploy/bootstrap_data.py
mkdir -p data/runtime
```

Если `data/generated` уже содержит файлы, bootstrap остановится. Явный merge копирует только отсутствующие файлы и не перезаписывает host data:

```bash
python deploy/bootstrap_data.py --merge
```

## 6. Запустите production stack

```bash
docker compose up -d --build
docker compose ps
```

Проверьте scheduler:

```bash
docker compose exec scheduler python scheduler.py list
docker compose exec scheduler python scheduler.py status
```

Проверка логов:

```bash
docker compose logs -f scheduler
docker compose logs -f admin
```

Ручной запуск job при необходимости:

```bash
docker compose exec scheduler python scheduler.py run-now --project caelus --job generate_ru
```

## 7. Остановка и restart

Без удаления persistent data:

```bash
docker compose stop
docker compose restart
```

`restart: unless-stopped` вместе с `systemctl enable --now docker` обеспечивает автоматическое возвращение сервисов после reboot хоста.

## 8. Backup

Перед обновлением сделайте backup:

```bash
mkdir -p backups
tar -czf "backups/kaban-data-$(date +%Y%m%d-%H%M%S).tar.gz" data/generated data/runtime
```

Храните резервные копии отдельно от одного-единственного диска сервера.

## 9. Не разрушительное обновление

1. Сначала сделайте backup.
2. Замените application/release files новой версией.
3. Сохраните существующие `.env` и `data/`.
4. Выполните:

```bash
docker compose build --pull
docker compose up -d --remove-orphans
docker compose ps
```

При обновлении **не удаляйте Compose volumes** и не удаляйте `data/generated` / `data/runtime`. Production host data всегда важнее shipped history.

## 10. CAELUS production schedule

Stage 3 не меняет расписание:

- timezone: `Pacific/Auckland`;
- `generate_ru`: `06:00`;
- `publish_ru`: `08:00`;
- публикация требует ручной Approve;
- EN jobs не включены.
