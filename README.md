# rwms

gRPC-сервис управления пользователями Remnawave-панели (Remnawave Manager
Service). Проксирует операции над подписками (создание, обновление, получение,
удаление) через Remnawave SDK. API описан в `proto/rwmanager.proto`; потребители
(bot, website, payment, user-notify, rw-cleaner, ip-guard) держат собственные
копии сгенерированных стабов (`makepb.sh`).

## Переменные окружения

```env
RW_MS_LOG_LEVEL=info
RW_MS_GRPC_PORT=50051
RW_MS_BASE_URL=https://panel.example.com
RW_MS_TOKEN=...
```

## Статистика трафика нод (GetNodes / GetNodeUsersUsage)

Read-only RPC для поиска аномального потребления трафика (расшаренных
подписок), используются вкладкой «Трафик нод» в админке сайта:

- `GetNodes(Empty)` — список нод панели (uuid, имя, адрес, connected/disabled,
  код страны);
- `GetNodeUsersUsage(node_uuid, start, end)` — потрафиковая разбивка по
  каждому пользователю ноды за период (строки «пользователь × день»,
  проксирует `GET /api/bandwidth-stats/nodes/{uuid}/users/legacy`).

Оба метода ничего не изменяют ни в панели, ни в БД. На длинных периодах
(30+ дней) панель может отвечать HTTP 500 на тяжёлые ноды — потребитель
должен быть готов к INTERNAL-ошибке.

В `Node` также отдаются `config_profile_uuid` и `active_inbound_uuids` —
админка сайта использует их, чтобы создавать новую ноду «с конфигом как у
существующей».

## Установка нод через админку (GetNodeSecret / CreateNode)

RPC для автоматической установки нод через админку сайта
(monkey-village-website, раздел «Ноды»):

- `GetNodeSecret(Empty)` — панельный ключ для remnanode (`SECRET_KEY` в
  docker-compose ноды). Проксирует `GET /api/keygen`; ключ один для всех нод
  и к записи ноды в панели не привязан. Сам ключ в логи не пишется.
- `CreateNode(name, address, port, country_code, config_profile_uuid,
  inbound_uuids)` — создаёт ноду в панели. Идемпотентен: если нода с таким
  именем или адресом уже существует, она возвращается как есть и никогда не
  пересоздаётся. Удаления/пересоздания нод в этом API нет намеренно
  (Remnawave Safety Rules).

## Маппинг ошибок в gRPC-статусы

Все RPC-хендлеры используют единый маппинг исключений
(`map_exception_to_grpc_code` в `server.py`):

- `NOT_FOUND` — только достоверный 404 от панели (ресурс действительно
  не существует);
- `UNAVAILABLE` — панель недоступна: транспортные ошибки httpx
  (`httpx.TransportError` и подклассы — обрыв соединения, DNS, TLS,
  таймауты, обрыв протокола; SDK не оборачивает их и они долетают как
  есть), а также `NetworkError`/`ApiError(status_code=0)` из SDK;
- `INTERNAL` — остальные ответы панели (500/502/429/401/409 и т.д.) и
  неожиданные исключения.

Клиенты обязаны отличать `NOT_FOUND` («подписки нет») от
`UNAVAILABLE`/`INTERNAL` («панель временно недоступна») — иначе бот/сайт
показывают «ключ закончился» при живой подписке. Обратная совместимость:
клиенты, трактующие любой RpcError как «нет данных», продолжают работать,
достоверный 404 по-прежнему приходит как `NOT_FOUND`. В `common`
(`rwms_client.py` / `rwms_client_sync.py`) для этого есть
`get_user_by_username_strict`, который отдаёт `None` только на `NOT_FOUND`,
а на остальные статусы поднимает `RwmsUnavailableError`.

Маппинг покрыт тестами `tests/test_error_mapping.py`.

## Логирование и секреты

В логи никогда не пишутся: токен панели (`RW_MS_TOKEN`), полные дампы
пользователей панели (содержат `trojanPassword`, `ssPassword`,
`vlessUuid` — рабочие ключи подписки; для hysteria2 auth per-user — это
UUID), панельный ключ нод. Создание/обновление пользователя логируется
компактно: `username`, `uuid`, `expire_at`, `status`. Отсутствие секретов
в логах закреплено тестами `tests/test_add_update_user.py`.

## Семантика AddUser

`created_at` и `last_traffic_reset_at` передаются в панель только если
поле явно задано в запросе (проверка через `HasField`; message-поля
proto3 всегда truthy, из-за чего незаданное поле раньше превращалось в
1970-01-01 у пересозданных пользователей).

`hwid_device_limit` при создании реально доезжает до панели: DTO SDK
принимает поле только по snake_case-имени (`hwid_device_limit`),
camelCase-kwarg `hwidDeviceLimit` pydantic молча игнорировал (у поля
только `serialization_alias`), и лимит устройств терялся. Сейчас ни один
потребитель (bot, website, payment, user-notify) это поле при `AddUser` не
передаёт, поэтому новые подписки по-прежнему создаются без личного лимита
и подчиняются панельному `fallback_device_limit`.

В UpdateUser защита hwid-лимита намеренно отсутствует: платёжный update
вправе сбрасывать вручную установленный лимит (лимиты ставятся вручную
против абьюзеров).

`traffic_limit_bytes` (поле 13, `optional int64`) передаётся в панель только
если явно задан в запросе (`HasField`); не задан — подписка создаётся без
лимита трафика, как раньше. Явный `0` тоже доезжает как `0` («без лимита»
в терминах панели), отрицательное значение — `INVALID_ARGUMENT`. Поле
используется антиабьюз-лимитом новых пробных подписок (настройки сайта
`trial_traffic_limit_enabled` / `trial_traffic_limit_gb` /
`trial_traffic_limit_strategy`): байты = `round(ГиБ * 1024**3)`.

`traffic_limit_strategy` при создании: незаданное поле читается как
`NO_RESET` (значение 0 в proto3, совпадает с дефолтом DTO SDK) — это
прежнее поведение, оно сохранено намеренно.

## Семантика UpdateUser

Опциональные скалярные поля (`status`, `email`, `expire_at`, `telegram_id`,
`traffic_limit_bytes`, `traffic_limit_strategy` и т.д.), не заданные в
запросе, в PATCH к панели не попадают и значений не сбрасывают.

`traffic_limit_strategy` — багфикс (антиабьюз, 2026-09): раньше стратегия
передавалась в PATCH всегда, и незаданное поле (0 = `NO_RESET` в proto3)
перезаписывало стратегию сброса в панели при каждом продлении/обновлении.
Теперь она передаётся только при `HasField("traffic_limit_strategy")`.
Вызывающим, которым нужно именно сбросить стратегию (снятие лимита пробной
подписки после оплаты), надо передавать `NO_RESET` явно — все текущие
платёжные пути (bot, website, payment, user-notify) так и делают.
Отрицательный `traffic_limit_bytes` — `INVALID_ARGUMENT`.

Снятие лимита трафика «как было»: `UpdateUser(uuid, traffic_limit_bytes=0,
traffic_limit_strategy=NO_RESET, status=ACTIVE)` без `active_internal_squads`
(см. ниже: пустой список сквады не трогает, поэтому ban-сквад не снимается).

`active_internal_squads` — repeated-поле без признака «не задано»: пустой список
трактуется как «сквады не менять» (поле исключается из PATCH). Стереть все
сквады подписки через UpdateUser нельзя. Это защищает от случайного сброса
сквадов вызовами, которые продлевают подписку и сквады не передают.

## Тесты

```bash
.venv/bin/python -m pytest tests/ -q
```

## Сборка и деплой

```bash
cd docker
./build-image-amd64.sh        # собирает образ rwms:v0.1 в docker/rwms-amd64.tar
./deploy.sh                   # заливает tar на сервер (старый tar сохраняется как .bak)
SSH_HOST=other-alias ./deploy.sh   # деплой на другой SSH-алиас
```

`deploy.sh` по умолчанию использует SSH-алиас `mv.fornex.app` и каталог
`/srv/monkey-village/rwms`; на сервере образ загружается `docker load` и
поднимается `up.sh` (`docker-compose.yml`, `.env` рядом). `mv-deploy.sh` —
исторический дубликат с теми же значениями.


## Зависимости

Все прямые зависимости в `requirements.txt` запинены на точные версии (инцидент 2026-07-07: незапиненный `remnawave` в rwms при пересборке Docker-образа притянул 2.8.0 с breaking change, и бот показывал всем пользователям, что срок ключа истёк). Пересборка образа не должна молча подтягивать новые версии. Обновление любой версии — осознанное изменение: поднять пин в `requirements.txt`, прогнать тесты и проверить согласованность со смежными сервисами.

## Обновление панели Remnawave до 2.8.x

Код rwms готов к SDK 2.8.0: в 2.8.0 `traffic_limit_bytes` в user-DTO стал
`float`, и `dto_to_proto_user` делает явный `int()`-каст (поле в proto —
`int64`); поведение покрыто тестами `tests/test_dto_to_proto_user.py`,
которые проходят и на SDK 2.7.1, и на 2.8.0. Остальные используемые методы
SDK (`get_user_by_uuid`, `get_user_by_username`, `create_user`,
`delete_user`, `update_user`, `get_all_users`, `get_inbounds`) между
версиями не менялись, gRPC-контракт rwms не затронут — потребители (bot,
website, payment, user-notify, rw-cleaner) обновлений не требуют.

Миграция БД панели в 2.8.0 необратима, поэтому бэкап перед обновлением
обязателен — это единственный путь отката. Таблицы пользователей и подписок
миграция не трогает; меняются только `hwid_user_devices` и
`user_subscription_request_history` (`userUuid` -> `userId`):

- `user_subscription_request_history` очищается (история запросов, не критично);
- `hwid_user_devices` переносится автоматически при <=500 тыс. записей, иначе
  очищается. Очистка для проекта допустима: список устройств никто из сервисов
  не читает (в коде используется только `hwid_device_limit` — число на
  пользователе, миграция его не трогает), конфиги на устройствах продолжают
  работать, устройства молча перерегистрируются при следующем обновлении
  подписки клиентом, лимит продолжает действовать. Руками ужимать таблицу под
  порог ради автопереноса не нужно. Перед апгрейдом узнать счётчик, чтобы
  поведение миграции было предсказуемым:
  `SELECT count(*) FROM hwid_user_devices;`
- вебхуки истечения объединены в `user.expiration` и выключены по умолчанию —
  проект панельные вебхуки не использует.

Порядок обновления:

1. Бэкап БД панели Remnawave и её `.env`
   (`docker exec <db> pg_dump -U postgres -Fc remnawave > dump`),
   плюс бэкап основной БД проекта.
2. Обновить панель на образ с явным тегом `2.8.0` (не `latest`).
3. Поднять пин `remnawave==2.8.0` в `requirements.txt`, прогнать тесты,
   пересобрать и задеплоить rwms.
4. Смоук-тест: профиль в боте (срок/трафик), продление оплатой (UpdateUser),
   кабинет сайта, `/sb` в custom-config, валидность существующих
   subscription URL, работоспособность API-токена, хосты в UI панели
   (в 2.8.0 `tag` -> `tags`, `allowInsecure` -> `pinnedPeerCertSha256`).

Откат: вернуть rwms-образ с SDK 2.7.1, тег панели 2.7.4 и восстановить
бэкап БД панели (`pg_restore`) — миграция 2.8.0 необратима, без бэкапа
отката нет.


## HWID-устройства (2026-08-19)

Аддитивные RPC для личного кабинета сайта:

- `GetUserHwidDevices(user_uuid)` — список HWID-устройств подписки
  (проксирует `GET /api/hwid/devices/{userUuid}` панели).
- `DeleteUserHwidDevice(user_uuid, hwid)` — удаление одного устройства
  (проксирует `POST /api/hwid/devices/delete`); возвращает обновлённый список.
- `GetHwidSettings(Empty)` — панельные настройки HWID (проксирует
  `GET /api/subscription-settings`, блок `hwidSettings`): `enabled` и
  `fallback_device_limit` — глобальный лимит устройств для подписок без
  личного `hwid_device_limit`. Кабинет сайта показывает по нему реальный
  лимит устройств.

Подписки и лимиты этими методами не изменяются. Все вызовы логируются.
После обновления proto пересобрать стабы: `./makepb.sh` (и в website-репо тоже).

## Лимит трафика и стратегии сброса (2026-09, антиабьюз)

Аддитивные изменения proto (номера и имена существующих полей не менялись):

- `enum TrafficLimitStrategy` += `MONTH_ROLLING = 4` — «ежемесячно по дате
  создания подписки», как в панели Remnawave. Маппинг proto <-> SDK знает
  это значение в обе стороны; раньше любой `Get*`/`GetAllUsers` по
  пользователю с такой стратегией в панели падал с `ValueError` ->
  `INTERNAL`.
- `AddUserRequest` += `optional int64 traffic_limit_bytes = 13` — лимит
  трафика при создании, см. «Семантика AddUser».
- `UpdateUser` передаёт `traffic_limit_strategy` только при `HasField`,
  см. «Семантика UpdateUser».

Лимит и стратегия попадают в компактный лог создания/обновления
(`traffic_limit_bytes=... traffic_limit_strategy=...`) — секретов там
по-прежнему нет. Покрытие: `tests/test_traffic_limit.py`.

Перегенерация стабов у потребителей (каждый — своим пиновым `.venv`,
чтобы gencode-версия `rwmanager_pb2.py` совпадала; тулчейн у всех
запинен на `grpcio-tools==1.81.1` / `protobuf==6.33.6`):

```bash
PATH="$PWD/.venv/bin:$PATH" ./makepb.sh                    # rwms
for d in ../monkey-village-website ../monkey-village-vpn-bot \
         ../monkey-village-notifier ../monkey-village-wata-webhook \
         ../monkey-village-rw-cleaner ../monkey-village-ip-guard; do
  cp proto/rwmanager.proto "$d/proto/rwmanager.proto"
  (cd "$d" && PATH="$PWD/.venv/bin:$PATH" ./makepb.sh)
done
md5 ../monkey-village-*/proto/rwmanager_pb2.py             # у потребителей одинаковы
```

`rwmanager_pb2.py` в rwms отличается от потребительского только путём
источника в дескрипторе (`-Iproto` против `-I.`), поэтому md5 сравнивать
между потребителями, а не с rwms. ip-guard — полноценный потребитель
(`GetUserById`, `GetUserHwidDevices` для алертов по подсетям) со своими
`proto/rwmanager_pb2*.py` и `makepb.sh`; если пропустить его в цикле, его
стабы разойдутся с остальными.
