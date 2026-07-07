# rwms

gRPC-сервис управления пользователями Remnawave-панели (Remnawave Manager
Service). Проксирует операции над подписками (создание, обновление, получение,
удаление) через Remnawave SDK. API описан в `proto/rwmanager.proto`; потребители
(bot, website, payment, user-notify, rw-cleaner) держат собственные копии
сгенерированных стабов (`makepb.sh`).

## Переменные окружения

```env
RW_MS_LOG_LEVEL=info
RW_MS_GRPC_PORT=50051
RW_MS_BASE_URL=https://panel.example.com
RW_MS_TOKEN=...
```

## Семантика UpdateUser

Опциональные скалярные поля (`status`, `email`, `expire_at`, `telegram_id` и
т.д.), не заданные в запросе, в PATCH к панели не попадают и значений не
сбрасывают.

`active_internal_squads` — repeated-поле без признака «не задано»: пустой список
трактуется как «сквады не менять» (поле исключается из PATCH). Стереть все
сквады подписки через UpdateUser нельзя. Это защищает от случайного сброса
сквадов вызовами, которые продлевают подписку и сквады не передают.

## Тесты

```bash
.venv/bin/python -m pytest tests/ -q
```
