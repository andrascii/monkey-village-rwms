import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

import rwmanager_pb2 as proto  # noqa: E402
import server as server_module  # noqa: E402
from config import Config  # noqa: E402
from server import Server  # noqa: E402

USER_UUID = "0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b"
SQUAD_UUID = "be4e12f0-098a-4d44-88d4-37cff58bf2d7"


def make_server_with_sdk_mock() -> tuple[Server, MagicMock]:
    server = Server(Config())

    sdk = MagicMock()
    sdk.users.update_user = AsyncMock(return_value=MagicMock())
    server._Server__remnawave = sdk

    return server, sdk


def call_update_user(request: proto.UpdateUserRequest, monkeypatch):
    # Конвертация ответа в protobuf не относится к проверяемому поведению,
    # поэтому подменяем её заглушкой.
    monkeypatch.setattr(
        server_module, "dto_to_proto_user", lambda user: proto.UserResponse()
    )

    server, sdk = make_server_with_sdk_mock()
    context = MagicMock()

    asyncio.run(server.UpdateUser(request, context))

    sdk.users.update_user.assert_awaited_once()
    return sdk.users.update_user.await_args.args[0]


def test_update_user_without_squads_does_not_touch_squads(monkeypatch):
    """Пустой repeated в proto3 неотличим от "не задано": UpdateUser без сквадов
    не должен стирать сквады подписки в панели."""
    request = proto.UpdateUserRequest(uuid=USER_UUID)

    dto = call_update_user(request, monkeypatch)

    assert dto.active_internal_squads is None
    # Именно так DTO сериализуется в PATCH-запрос к панели (см. remnawave SDK,
    # rapid/client.py): поле должно отсутствовать в теле запроса целиком.
    dumped = dto.model_dump(exclude_none=True, by_alias=True, mode="json")
    assert "activeInternalSquads" not in dumped


def test_update_user_with_squads_passes_them_through(monkeypatch):
    request = proto.UpdateUserRequest(
        uuid=USER_UUID,
        active_internal_squads=[SQUAD_UUID],
    )

    dto = call_update_user(request, monkeypatch)

    assert dto.active_internal_squads == [UUID(SQUAD_UUID)]
    dumped = dto.model_dump(exclude_none=True, by_alias=True, mode="json")
    assert dumped["activeInternalSquads"] == [SQUAD_UUID]
