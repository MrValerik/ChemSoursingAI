# Автовыключение ВМ после 30 минут бездействия

Механизм считает активностью HTTP-запросы и реальные действия пользователя:
движение мыши, нажатия, прокрутку и касания. Браузер отправляет лёгкую отметку
активности не чаще раза в минуту. Запросы проходят через nginx и записываются
в `data/nginx/chemsource_access.log`.
Внутренние healthcheck-запросы Docker идут напрямую в backend и не продлевают
работу ВМ.

Дополнительные защиты:

- ВМ не выключается первые 30 минут после запуска.
- ВМ не выключается, пока открыто HTTP-соединение — например, Qwen генерирует
  длинный ответ.
- Проверка выполняется раз в минуту, поэтому фактическое выключение произойдёт
  примерно через 30–31 минуту.
- При следующем ручном запуске ВМ автоматически стартуют Qwen, Docker и сайт.

## Установка на ВМ

После `git pull` выполните из корня проекта:

```bash
docker compose up -d --build frontend
sudo bash deploy/install-vm-services.sh
```

Проверьте:

```bash
systemctl is-enabled qwen.service chemsource.service
systemctl is-active chemsource-idle-shutdown.timer
systemctl list-timers chemsource-idle-shutdown.timer
```

## Безопасная проверка без выключения

Откройте настройки:

```bash
sudo nano /etc/chemsource-idle-shutdown.conf
```

Временно установите:

```ini
IDLE_TIMEOUT_SECONDS=120
MIN_UPTIME_SECONDS=0
DRY_RUN=1
```

Перезапустите проверку и посмотрите журнал:

```bash
sudo systemctl start chemsource-idle-shutdown.service
journalctl -u chemsource-idle-shutdown.service -n 20 --no-pager
```

После проверки верните безопасные значения:

```ini
IDLE_TIMEOUT_SECONDS=1800
MIN_UPTIME_SECONDS=1800
DRY_RUN=0
```

Перезапуск таймера после изменения файла не требуется: конфигурация читается
при каждом запуске проверки.

## Отмена автовыключения

```bash
sudo systemctl disable --now chemsource-idle-shutdown.timer
```

Отключение таймера не затрагивает автозапуск сайта и Qwen.
