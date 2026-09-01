"""
AddUser/UpdateUser: created_at через HasField (proto3 message-поля всегда
truthy — незаданное поле давало 1970-01-01), hwid_device_limit доезжает до
DTO (kwarg hwidDeviceLimit пайдантик молча игнорировал из-за
serialization_alias), и в логах нет секретов подписки/токена панели.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token-secret")

from google.protobuf.timestamp_pb2 import Timestamp  # noqa: E402
from remnawave.models import UserResponseDto  # noqa: E402

import rwmanager_pb2 as proto  # noqa: E402
from config import Config  # noqa: E402
from server import Server  # noqa: E402

USER_PAYLOAD = {
    "uuid": "0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b",
    "id": 1,
    "shortUuid": "abc123",
    "username": "tg_100500",
    "status": "ACTIVE",
    "trafficLimitBytes": 107374182400,
    "trafficLimitStrategy": "NO_RESET",
    "expireAt": "2027-01-01T00:00:00Z",
    "trojanPassword": "trojan-secret-pass",
    "vlessUuid": "6a5f9f0e-6c3b-4f0e-9d2a-1b2c3d4e5f60",
    "ssPassword": "ss-secret-pass-123",
    "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-01-02T00:00:00Z",
    "subscriptionUrl": "https://sub.example.com/abc123",
    "activeInternalSquads": [],
    "userTraffic": {
        "usedTrafficBytes": 1024.0,
        "lifetimeUsedTrafficBytes": 2048.0,
    },
}


def make_user_dto() -> UserResponseDto:
    return UserResponseDto.model_validate(USER_PAYLOAD)


def make_server() -> tuple[Server, MagicMock]:
    server = Server(Config())
    sdk = MagicMock()
    server._Server__remnawave = sdk
    return server, sdk


def to_ts(dt: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def make_add_user_request(**kwargs) -> proto.AddUserRequest:
    return proto.AddUserRequest(
        username="tg_100500",
        expire_at=to_ts(datetime(2027, 1, 1)),
        status=proto.UserStatus.ACTIVE,
        traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
        **kwargs,
    )


def run_add_user(request: proto.AddUserRequest):
    server, sdk = make_server()
    sdk.users.create_user = AsyncMock(return_value=make_user_dto())
    context = MagicMock()
    reply = asyncio.run(server.AddUser(request, context))
    context.set_code.assert_not_called()
    dto = sdk.users.create_user.call_args.args[0]
    return reply, dto


# --- created_at: HasField вместо truthy-проверки --------------------------


def test_add_user_without_created_at_passes_none():
    # Незаданное message-поле proto3 truthy, ToDatetime() дал бы 1970-01-01.
    _, dto = run_add_user(make_add_user_request())
    assert dto.created_at is None


def test_add_user_with_created_at_passes_value():
    created = datetime(2026, 5, 20, 12, 30)
    _, dto = run_add_user(make_add_user_request(created_at=to_ts(created)))
    assert dto.created_at == created


def test_add_user_without_last_traffic_reset_at_passes_none():
    _, dto = run_add_user(make_add_user_request())
    assert dto.last_traffic_reset_at is None


# --- hwid_device_limit доезжает до DTO ------------------------------------


def test_add_user_hwid_device_limit_reaches_dto():
    _, dto = run_add_user(make_add_user_request(hwid_device_limit=3))
    assert dto.hwid_device_limit == 3
    # и сериализуется в camelCase-поле, которое ждёт панель
    assert '"hwidDeviceLimit":3' in dto.model_dump_json(
        by_alias=True, exclude_none=True
    )


def test_add_user_without_hwid_device_limit_is_none():
    _, dto = run_add_user(make_add_user_request())
    assert dto.hwid_device_limit is None


# --- Логи без секретов ----------------------------------------------------

SECRETS = (
    "test-token-secret",  # токен панели
    "trojan-secret-pass",  # trojanPassword
    "ss-secret-pass-123",  # ssPassword
    "trojanPassword",
    "ssPassword",
    "6a5f9f0e-6c3b-4f0e-9d2a-1b2c3d4e5f60",  # vlessUuid (hysteria2 auth = UUID)
)


def assert_no_secrets(caplog):
    for secret in SECRETS:
        assert secret not in caplog.text, f"секрет '{secret}' попал в лог"


def test_server_startup_does_not_log_token(caplog, monkeypatch):
    # env выставляется явно: setdefault в других тест-модулях мог задать
    # другое значение раньше, и проверка стала бы тривиально-зелёной.
    monkeypatch.setenv("RW_MS_TOKEN", "test-token-secret")
    with caplog.at_level(logging.DEBUG):
        Server(Config())
    assert "test-token-secret" not in caplog.text


def test_add_user_logs_do_not_contain_secrets(caplog, monkeypatch):
    monkeypatch.setenv("RW_MS_TOKEN", "test-token-secret")
    with caplog.at_level(logging.DEBUG):
        run_add_user(make_add_user_request())
    assert_no_secrets(caplog)
    # компактный лог по-прежнему информативен
    assert "tg_100500" in caplog.text
    assert "0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b" in caplog.text


def test_update_user_logs_do_not_contain_secrets(caplog):
    server, sdk = make_server()
    sdk.users.update_user = AsyncMock(return_value=make_user_dto())
    context = MagicMock()
    request = proto.UpdateUserRequest(
        uuid="0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b",
        status=proto.UserStatus.ACTIVE,
        traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
        expire_at=to_ts(datetime(2027, 6, 1)),
    )

    with caplog.at_level(logging.DEBUG):
        reply = asyncio.run(server.UpdateUser(request, context))

    context.set_code.assert_not_called()
    assert reply.username == "tg_100500"
    assert_no_secrets(caplog)
    assert "user updated" in caplog.text
