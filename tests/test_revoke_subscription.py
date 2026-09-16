"""RevokeUserSubscription: перевыпуск подписки по действию владельца в кабинете.

Панель вызывается с пустым телом (новый short_uuid + новые credentials),
ответ проксируется через dto_to_proto_user; ошибки панели — через _fail.
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

import grpc  # noqa: E402
from remnawave.exceptions import NotFoundError  # noqa: E402

import rwmanager_pb2 as proto  # noqa: E402
from config import Config  # noqa: E402
from server import Server  # noqa: E402
from tests.test_dto_to_proto_user import make_user  # noqa: E402


def make_server(revoke):
    server = Server(Config())
    sdk = MagicMock()
    sdk.users.revoke_user_subscription = revoke
    server._Server__remnawave = sdk
    return server, sdk


def test_revoke_returns_user_with_new_short_uuid():
    dto = make_user(shortUuid="newshort", subscriptionUrl="https://sub.example/newshort")
    revoke = AsyncMock(return_value=dto)
    server, _ = make_server(revoke)
    context = MagicMock()

    reply = asyncio.run(
        server.RevokeUserSubscription(
            proto.RevokeUserSubscriptionRequest(uuid=str(dto.uuid)), context
        )
    )

    # Тело запроса пустое: панель сама генерирует short_uuid и credentials.
    revoke.assert_awaited_once_with(str(dto.uuid))
    assert reply.uuid == str(dto.uuid)
    assert reply.short_uuid == "newshort"
    assert reply.subscription_url == "https://sub.example/newshort"
    context.set_code.assert_not_called()


def test_revoke_without_uuid_is_invalid_argument():
    revoke = AsyncMock()
    server, _ = make_server(revoke)
    context = MagicMock()

    asyncio.run(
        server.RevokeUserSubscription(proto.RevokeUserSubscriptionRequest(), context)
    )

    revoke.assert_not_awaited()
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)


def test_revoke_not_found_maps_to_not_found():
    error = NotFoundError.__new__(NotFoundError)
    error.status_code = 404
    revoke = AsyncMock(side_effect=error)
    server, _ = make_server(revoke)
    context = MagicMock()

    asyncio.run(
        server.RevokeUserSubscription(
            proto.RevokeUserSubscriptionRequest(uuid="missing"), context
        )
    )

    context.set_code.assert_called_once_with(grpc.StatusCode.NOT_FOUND)
