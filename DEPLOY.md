# Развёртывание (канонический сценарий)

После копирования актуальных файлов на сервер выполните:

```bash
cd /root/telegram-support-bot
bash scripts/deploy_production_server.sh
```

Скрипт:
- валидирует `docker-compose.yml`
- публикует runtime виджета в `/root/nginx-proxy/html/widget`
- пересобирает и поднимает контейнеры
- проверяет доступность `widget-api`, `log-streamer` и наличие статики
- выводит статус и хвост логов

## Быстрый старт (альтернатива)

### 1. Скопируйте файлы на сервер

```bash
# С вашего локального компьютера
scp -r . user@your-server:/path/to/telegram-bot
```

Или через git:

```bash
# На сервере
git clone <your-repo-url> /path/to/telegram-bot
cd /path/to/telegram-bot
```

### 2. Настройте переменные окружения

Создайте файл `.env` на сервере:

```bash
cd /path/to/telegram-bot
nano .env
```

**Обязательные переменные:**
```env
BOT_TOKEN=6169525099:AAHAl62e8TYFBFA3BMbZsD5yHBRw9dcxcmE
GEMINI_API_KEY=AIzaSyCT4bF25hxPN_KTcDOGZVGSwFXIoicwf3I
SUPPORT_GROUP_ID=-1003762216349
ADMINS=1941348923
```

### 3. Запустите серверный деплой-скрипт

```bash
cd /root/telegram-bot
bash scripts/deploy_production_server.sh
```

## Управление

### Остановка бота
```bash
docker-compose down
```

### Перезапуск
```bash
docker-compose restart
```

### Обновление (после изменений в коде)
```bash
cd /root/telegram-support-bot
bash scripts/deploy_production_server.sh
```

### Просмотр логов
```bash
# В реальном времени
docker-compose logs -f

# Последние 100 строк
docker-compose logs --tail=100

# Только ошибки
docker-compose logs | grep -i error
```

### Доступ к базе данных
```bash
# Скопировать базу данных на хост
docker cp telegram-support-bot:/app/data/support.db ./support.db

# Или войти в контейнер
docker exec -it telegram-support-bot bash
sqlite3 /app/data/support.db
```

## Мониторинг

### Статус контейнера
```bash
docker stats telegram-support-bot
```

### Проверка что бот работает
```bash
docker exec telegram-support-bot ps aux | grep python
```

### Автоматический перезапуск при сбоях
В `docker-compose.yml` уже настроено:
```yaml
restart: unless-stopped
```

Контейнер автоматически перезапустится при:
- Ошибке в коде
- Перезагрузке сервера
- Crash процесса

## Безопасность

### 1. Ограничьте доступ к `.env`
```bash
chmod 600 /path/to/telegram-bot/.env
```

### 2. Используйте Docker secrets (опционально)
Для продакшена рассмотрите Docker Swarm secrets или external secrets.

### 3. Обновляйте зависимости
Регулярно проверяйте `requirements.txt` на уязвимости:
```bash
docker run --rm -v $(pwd):/app pip-audit pip-audit
```

## Troubleshooting

### Бот не запускается
```bash
# Проверьте логи
docker-compose logs telegram-bot

# Проверьте переменные окружения
docker exec telegram-support-bot env | grep -E "BOT_TOKEN|GEMINI"
```

### Ошибка "not enough rights to create a topic"
Добавьте бота как **администратора** группы в Telegram с правом создания тем.

### Ошибка Gemini API
Проверьте API ключ и квоты:
```bash
docker exec telegram-support-bot python -c "import os; print(os.getenv('GEMINI_API_KEY')[:10])"
```

### Место на диске
```bash
# Очистить старые образы
docker image prune -f

# Очистить всё (осторожно!)
docker system prune -a
```

## Production рекомендации

### 1. Логирование
Настройте ротацию логов в `/etc/docker/daemon.json`:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

### 2. Резервное копирование
```bash
# Бэкап базы данных
docker exec telegram-support-bot tar czf - /app/data > backup-$(date +%Y%m%d).tar.gz
```

### 3. Health check
Добавьте в `docker-compose.yml`:
```yaml
healthcheck:
  test: ["CMD", "pgrep", "-f", "main.py"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### 4. Обновление без простоя
Используйте docker-compose с zero-downtime:
```bash
docker-compose pull
docker-compose up -d --force-recreate
docker-compose down --remove-orphans
```

## Виджет как отдельный runtime

Боевой виджет разворачивается отдельно от демо-страницы:

```bash
cd /root/telegram-support-bot
bash scripts/publish_widget_runtime.sh
```

По умолчанию публикуется:
- `widget/dist` → `/root/nginx-proxy/html/widget`
- `widget-host.js` → `/root/nginx-proxy/html/widget-host.js`

Так виджет подключается на стороннем сайте и не блокирует загрузку основной страницы.
