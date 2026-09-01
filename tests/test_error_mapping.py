"""
Маппинг ошибок SDK/httpx в gRPC-статусы (корень NOT_FOUND-конфляции).

Требование: «панель временно недоступна» (UNAVAILABLE) должна быть
отличима от «подписки не существует» (NOT_FOUND). Достоверный 404 от
панели — и только он — остаётся NOT_FOUND.
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

import grpc  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from google.protobuf.timestamp_pb2 import Timestamp  # noqa: E402
from remnawave.exceptions import (  # noqa: E402
    ApiError,
    ApiErrorResponse,
    NetworkError,
    NotFoundError,
)

import rwmanager_pb2 as proto  # noqa: E402
from config import Config  # noqa: E402
from server import Server, map_exception_to_grpc_code  # noqa: E402


def make_api_error(status_code: int, cls=ApiError) -> ApiError:
    return cls(
        status_code,
        ApiErrorResponse(message=f"panel says {status_code}", code=f"HTTP_{status_code}"),
    )


def make_server() -> tuple[Server, MagicMock]:
    server = Server(Config())
    sdk = MagicMock()
    server._Server__remnawave = sdk
    return server, sdk


def make_add_user_request() -> proto.AddUserRequest:
    expire_at = Timestamp()
    expire_at.FromDatetime(datetime(2027, 1, 1))
    return proto.AddUserRequest(
        username="tg_100500",
        expire_at=expire_at,
        status=proto.UserStatus.ACTIVE,
        traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
    )


# --- Юнит-тесты самого маппера -------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (make_api_error(404), grpc.StatusCode.NOT_FOUND),
        (make_api_error(404, NotFoundError), grpc.StatusCode.NOT_FOUND),
        (make_api_error(500), grpc.StatusCode.INTERNAL),
        (make_api_error(502), grpc.StatusCode.INTERNAL),
        (make_api_error(429), grpc.StatusCode.INTERNAL),
        (make_api_error(401), grpc.StatusCode.INTERNAL),
        # SDK оборачивает httpx.RequestError в ApiError(0, NETWORK_ERROR)
        (make_api_error(0), grpc.StatusCode.UNAVAILABLE),
        (make_api_error(0, NetworkError), grpc.StatusCode.UNAVAILABLE),
        # Сырые транспортные ошибки httpx долетают из SDK как есть
        (httpx.ConnectError("connection refused"), grpc.StatusCode.UNAVAILABLE),
        (httpx.ConnectTimeout("connect timeout"), grpc.StatusCode.UNAVAILABLE),
        (httpx.ReadTimeout("read timeout"), grpc.StatusCode.UNAVAILABLE),
        (httpx.PoolTimeout("pool timeout"), grpc.StatusCode.UNAVAILABLE),
        (httpx.RemoteProtocolError("server disconnected"), grpc.StatusCode.UNAVAILABLE),
        # Всё неожиданное — INTERNAL
        (RuntimeError("boom"), grpc.StatusCode.INTERNAL),
        (ValueError("bad value"), grpc.StatusCode.INTERNAL),
    ],
)
def test_map_exception_to_grpc_code(exc, expected):
    assert map_exception_to_grpc_code(exc) == expected


def test_map_incomplete_api_error_is_internal():
    # ApiError.__new__ без атрибутов (паттерн старых тестов) не должен ронять маппер
    exc = ApiError.__new__(ApiError)
    assert map_exception_to_grpc_code(exc) == grpc.StatusCode.INTERNAL


# --- GetUserByUsername ----------------------------------------------------


def run_get_user_by_username(side_effect):
    server, sdk = make_server()
    sdk.users.get_user_by_username = AsyncMock(side_effect=side_effect)
    context = MagicMock()
    reply = asyncio.run(
        server.GetUserByUsername(
            proto.GetUserByUsernameRequest(username="tg_100500"), context
        )
    )
    return reply, context


def test_get_user_by_username_404_is_not_found():
    _, context = run_get_user_by_username(make_api_error(404, NotFoundError))
    context.set_code.assert_called_once_with(grpc.StatusCode.NOT_FOUND)


def test_get_user_by_username_network_error_is_unavailable():
    # Раньше любое ApiError/сетевая ошибка выглядела для клиентов как
    # «подписки нет» — бот показывал «ключ закончился» при живой подписке.
    _, context = run_get_user_by_username(httpx.ConnectError("connection refused"))
    context.set_code.assert_called_once_with(grpc.StatusCode.UNAVAILABLE)


def test_get_user_by_username_timeout_is_unavailable():
    _, context = run_get_user_by_username(httpx.ReadTimeout("read timeout"))
    context.set_code.assert_called_once_with(grpc.StatusCode.UNAVAILABLE)


def test_get_user_by_username_500_is_internal():
    _, context = run_get_user_by_username(make_api_error(500))
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)


# --- AddUser --------------------------------------------------------------


def run_add_user(side_effect):
    server, sdk = make_server()
    sdk.users.create_user = AsyncMock(side_effect=side_effect)
    context = MagicMock()
    reply = asyncio.run(server.AddUser(make_add_user_request(), context))
    return reply, context


def test_add_user_404_is_not_found():
    _, context = run_add_user(make_api_error(404, NotFoundError))
    context.set_code.assert_called_once_with(grpc.StatusCode.NOT_FOUND)


def test_add_user_network_error_is_unavailable():
    _, context = run_add_user(httpx.ConnectError("connection refused"))
    context.set_code.assert_called_once_with(grpc.StatusCode.UNAVAILABLE)


def test_add_user_500_is_internal():
    _, context = run_add_user(make_api_error(500))
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)


def test_add_user_error_details_present():
    _, context = run_add_user(make_api_error(500))
    context.set_details.assert_called_once()
    assert "add user operation failed" in context.set_details.call_args.args[0]
