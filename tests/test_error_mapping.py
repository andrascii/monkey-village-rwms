"""
Маппинг ошибок SDK/httpx в gRPC-статусы (корень NOT_FOUND-конфляции).

Требование: «панель временно недоступна» (UNAVAILABLE) должна быть
отличима от «подписки не существует» (NOT_FOUND). Достоверный 404 от
панели — и только он — остаётся NOT_FOUND.

Второе требование (инцидент 2026-09-10): «запрос невалиден» должно быть
отличимо от «панель чихнула». Битый email в users (домен без точки) не
проходил EmailStr в UpdateUserRequestDto, RWMS отдавал INTERNAL, payment
считал это транзиентным сбоем и крутил ОПЛАЧЕННОЕ продление в ретраях
больше трёх часов.

Теперь невалидный ЗАПРОС даёт INVALID_ARGUMENT, но ставится он ЯВНО в
хендлере вокруг сборки DTO — не в маппере. Разница принципиальна: SDK
валидирует тем же pydantic и ОТВЕТЫ панели, и ValidationError оттуда
означает уже применённую мутацию, которую нельзя объявлять «повтор не
поможет». Поэтому в маппере ValidationError остаётся INTERNAL.
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
from pydantic import BaseModel, ValidationError  # noqa: E402
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


class _StrictModel(BaseModel):
    n: int


def make_validation_error() -> ValidationError:
    """Настоящий pydantic ValidationError без зависимости от email_validator.

    В проде такую ошибку даёт EmailStr в Create/UpdateUserRequestDto, но
    email_validator тянется транзитивно через remnawave и есть не во всяком
    окружении. Мапперу важен сам тип исключения, а не поле, на котором оно
    возникло, поэтому берём тривиальную модель.
    """
    try:
        _StrictModel(n="not-an-int")
    except ValidationError as e:
        return e
    raise AssertionError("модель обязана была отвергнуть значение")


def make_update_user_request(email: str | None = None) -> proto.UpdateUserRequest:
    request = proto.UpdateUserRequest(
        uuid="175751a9-ec79-4b82-8ab9-e96e66711816",
        status=proto.UserStatus.ACTIVE,
    )
    if email is not None:
        request.email = email
    return request


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
        # ValidationError в МАППЕРЕ остаётся INTERNAL: сюда она долетает и от
        # разбора ОТВЕТА панели (SDK валидирует ответы тем же pydantic), а это
        # уже применённая мутация — хоронить её как «повтор не поможет»
        # нельзя. Невалидный ЗАПРОС отдаёт INVALID_ARGUMENT явно в хендлере,
        # см. тесты ниже.
        (make_validation_error(), grpc.StatusCode.INTERNAL),
        # Всё неожиданное — INTERNAL
        (RuntimeError("boom"), grpc.StatusCode.INTERNAL),
        (ValueError("bad value"), grpc.StatusCode.INTERNAL),
    ],
)
def test_map_exception_to_grpc_code(exc, expected):
    assert map_exception_to_grpc_code(exc) == expected


def test_handlers_catch_validation_error_not_its_value_error_parent():
    """Хендлеры ловят именно ValidationError, а не ValueError-родителя.

    ValidationError — подкласс ValueError, и если бы except в хендлере
    проверял ValueError, под INVALID_ARGUMENT уехали бы посторонние ошибки
    вычислений (например ValueError из ProtoTrafficLimitStrategyToRemnawave),
    и клиент перестал бы их ретраить.
    """
    assert isinstance(make_validation_error(), ValueError)
    source = Path(__file__).resolve().parent.parent / "server.py"
    body = source.read_text()
    assert "except ValidationError as e:" in body
    assert "except ValueError as e:" not in body
    assert map_exception_to_grpc_code(ValueError("bad value")) == grpc.StatusCode.INTERNAL


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


def test_add_user_with_malformed_email_is_invalid_argument():
    """Битый email в ЗАПРОСЕ: DTO не собирается, панель его не видела.

    Проверяется настоящая сборка CreateUserRequestDto, а не подставленное
    исключение: именно EmailStr отвергает 'milenapanowa@yandex' (домен без
    точки), и клиент обязан получить код, отличимый от «панель недоступна».
    """
    server, sdk = make_server()
    sdk.users.create_user = AsyncMock()
    context = MagicMock()

    request = make_add_user_request()
    request.email = "milenapanowa@yandex"
    asyncio.run(server.AddUser(request, context))

    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    # Панель НЕ трогали: запрос до неё не дошёл.
    sdk.users.create_user.assert_not_awaited()


def test_add_user_response_validation_error_stays_internal():
    """Ошибка разбора ОТВЕТА панели — не «невалидный запрос».

    Мутация могла уже примениться, поэтому такая ошибка обязана остаться
    ретраибельной, иначе клиент похоронит выполненную операцию.
    """
    _, context = run_add_user(make_validation_error())
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)


# --- UpdateUser -----------------------------------------------------------


def run_update_user(side_effect, request=None):
    server, sdk = make_server()
    sdk.users.update_user = AsyncMock(side_effect=side_effect)
    context = MagicMock()
    reply = asyncio.run(
        server.UpdateUser(request or make_update_user_request(), context)
    )
    return reply, context


def test_update_user_with_malformed_email_is_invalid_argument():
    """Инцидент 2026-09-10 в миниатюре.

    Раньше здесь был INTERNAL, payment трактовал его как «панель временно
    недоступна» и ретраил оплаченное продление бесконечно: в БД срок
    продлён, в панели нет, задача висела 3+ часа. Собирается настоящий
    UpdateUserRequestDto — отвергает адрес именно EmailStr.
    """
    server, sdk = make_server()
    sdk.users.update_user = AsyncMock()
    context = MagicMock()

    asyncio.run(
        server.UpdateUser(
            make_update_user_request(email="milenapanowa@yandex"), context
        )
    )

    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    # Панель НЕ трогали: запрос до неё не дошёл.
    sdk.users.update_user.assert_not_awaited()


def test_update_user_response_validation_error_stays_internal():
    _, context = run_update_user(make_validation_error())
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)


def test_update_user_network_error_is_unavailable():
    # Блип панели остаётся ретраибельным — задачу хоронить нельзя.
    _, context = run_update_user(httpx.ConnectError("connection refused"))
    context.set_code.assert_called_once_with(grpc.StatusCode.UNAVAILABLE)


def test_update_user_500_is_internal():
    _, context = run_update_user(make_api_error(500))
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)


def test_update_user_error_details_present():
    _, context = run_update_user(make_api_error(500))
    context.set_details.assert_called_once()
    assert "update user operation failed" in context.set_details.call_args.args[0]
