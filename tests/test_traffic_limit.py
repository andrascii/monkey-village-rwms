"""
Лимит трафика при создании подписки (AddUser.traffic_limit_bytes — только
при явно заданном поле), стратегия MONTH_ROLLING в обе стороны (раньше любой
Get* по такому пользователю падал с ValueError -> INTERNAL) и UpdateUser,
который передаёт traffic_limit_strategy в панель ТОЛЬКО при HasField
(раньше незаданное поле = NO_RESET перезаписывало стратегию в панели).
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

import grpc  # noqa: E402
from google.protobuf.timestamp_pb2 import Timestamp  # noqa: E402
from remnawave.enums import TrafficLimitStrategy  # noqa: E402
from remnawave.models import UserResponseDto  # noqa: E402

import rwmanager_pb2 as proto  # noqa: E402
from config import Config  # noqa: E402
from server import (  # noqa: E402
    ProtoTrafficLimitStrategyToRemnawave,
    RemnawaveTrafficLimitStrategyToProto,
    Server,
    dto_to_proto_user,
)

USER_UUID = "0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b"
SQUAD_UUID = "be4e12f0-098a-4d44-88d4-37cff58bf2d7"
FIVE_GIB = 5 * 1024**3

USER_PAYLOAD = {
    "uuid": USER_UUID,
    "id": 1,
    "shortUuid": "abc123",
    "username": "tg_100500",
    "status": "ACTIVE",
    "trafficLimitBytes": FIVE_GIB,
    "trafficLimitStrategy": "DAY",
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

STRATEGY_PAIRS = [
    (TrafficLimitStrategy.NO_RESET, proto.TrafficLimitStrategy.NO_RESET),
    (TrafficLimitStrategy.DAY, proto.TrafficLimitStrategy.DAY),
    (TrafficLimitStrategy.WEEK, proto.TrafficLimitStrategy.WEEK),
    (TrafficLimitStrategy.MONTH, proto.TrafficLimitStrategy.MONTH),
    (TrafficLimitStrategy.MONTH_ROLLING, proto.TrafficLimitStrategy.MONTH_ROLLING),
]


def make_user_dto(**overrides) -> UserResponseDto:
    return UserResponseDto.model_validate({**USER_PAYLOAD, **overrides})


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
        **kwargs,
    )


def run_add_user(request: proto.AddUserRequest):
    server, sdk = make_server()
    sdk.users.create_user = AsyncMock(return_value=make_user_dto())
    context = MagicMock()
    reply = asyncio.run(server.AddUser(request, context))
    return reply, sdk, context


def run_update_user(request: proto.UpdateUserRequest):
    server, sdk = make_server()
    sdk.users.update_user = AsyncMock(return_value=make_user_dto())
    context = MagicMock()
    reply = asyncio.run(server.UpdateUser(request, context))
    return reply, sdk, context


def dump_for_panel(dto) -> dict:
    # Именно так SDK сериализует DTO в тело запроса к панели
    # (exclude_none + by_alias): поле None в запрос не попадает вовсе.
    return dto.model_dump(exclude_none=True, by_alias=True, mode="json")


# --- Маппинг стратегий proto <-> SDK ---------------------------------------


@pytest.mark.parametrize("sdk_value,proto_value", STRATEGY_PAIRS)
def test_strategy_sdk_to_proto(sdk_value, proto_value):
    assert RemnawaveTrafficLimitStrategyToProto(sdk_value) == proto_value


@pytest.mark.parametrize("sdk_value,proto_value", STRATEGY_PAIRS)
def test_strategy_proto_to_sdk(sdk_value, proto_value):
    assert ProtoTrafficLimitStrategyToRemnawave(proto_value) == sdk_value


@pytest.mark.parametrize("sdk_value,_", STRATEGY_PAIRS)
def test_strategy_round_trip(sdk_value, _):
    assert (
        ProtoTrafficLimitStrategyToRemnawave(
            RemnawaveTrafficLimitStrategyToProto(sdk_value)
        )
        == sdk_value
    )


def test_strategy_mapping_covers_whole_proto_enum():
    # Новое значение в proto без ветки в маппинге — ValueError на проде.
    for value in proto.TrafficLimitStrategy.values():
        ProtoTrafficLimitStrategyToRemnawave(value)


def test_strategy_mapping_covers_whole_sdk_enum():
    for value in TrafficLimitStrategy:
        RemnawaveTrafficLimitStrategyToProto(value)


def test_unknown_proto_strategy_raises():
    with pytest.raises(ValueError):
        ProtoTrafficLimitStrategyToRemnawave(99)


def test_unknown_sdk_strategy_raises():
    with pytest.raises(ValueError):
        RemnawaveTrafficLimitStrategyToProto("SOMETHING_NEW")


# --- MONTH_ROLLING в ответах панели больше не роняет Get* -----------------


def test_dto_to_proto_user_month_rolling():
    response = dto_to_proto_user(make_user_dto(trafficLimitStrategy="MONTH_ROLLING"))
    assert response.HasField("traffic_limit_strategy")
    assert response.traffic_limit_strategy == proto.TrafficLimitStrategy.MONTH_ROLLING


def test_get_user_by_uuid_with_month_rolling_is_not_internal():
    server, sdk = make_server()
    sdk.users.get_user_by_uuid = AsyncMock(
        return_value=make_user_dto(trafficLimitStrategy="MONTH_ROLLING")
    )
    context = MagicMock()

    reply = asyncio.run(
        server.GetUserByUuid(proto.GetUserByUuidRequest(uuid=USER_UUID), context)
    )

    context.set_code.assert_not_called()
    assert reply.traffic_limit_strategy == proto.TrafficLimitStrategy.MONTH_ROLLING
    assert reply.traffic_limit_bytes == FIVE_GIB


# --- AddUser: traffic_limit_bytes только при HasField ---------------------


def test_add_user_without_traffic_limit_bytes_passes_none():
    _, sdk, context = run_add_user(make_add_user_request())
    context.set_code.assert_not_called()
    dto = sdk.users.create_user.call_args.args[0]
    assert dto.traffic_limit_bytes is None
    assert "trafficLimitBytes" not in dump_for_panel(dto)


def test_add_user_with_traffic_limit_bytes_reaches_dto():
    _, sdk, context = run_add_user(
        make_add_user_request(
            traffic_limit_bytes=FIVE_GIB,
            traffic_limit_strategy=proto.TrafficLimitStrategy.DAY,
        )
    )
    context.set_code.assert_not_called()
    dto = sdk.users.create_user.call_args.args[0]
    assert dto.traffic_limit_bytes == FIVE_GIB
    assert dto.traffic_limit_strategy == TrafficLimitStrategy.DAY
    dumped = dump_for_panel(dto)
    assert dumped["trafficLimitBytes"] == FIVE_GIB
    assert dumped["trafficLimitStrategy"] == "DAY"


def test_add_user_with_explicit_zero_traffic_limit_bytes_passes_zero():
    # Явный 0 (optional-поле задано) — «без лимита» в терминах панели,
    # и он должен доехать как 0, а не исчезнуть как None.
    _, sdk, context = run_add_user(make_add_user_request(traffic_limit_bytes=0))
    context.set_code.assert_not_called()
    dto = sdk.users.create_user.call_args.args[0]
    assert dto.traffic_limit_bytes == 0
    assert dump_for_panel(dto)["trafficLimitBytes"] == 0


def test_add_user_negative_traffic_limit_bytes_is_invalid_argument():
    _, sdk, context = run_add_user(make_add_user_request(traffic_limit_bytes=-1))
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    sdk.users.create_user.assert_not_awaited()


def test_add_user_month_rolling_strategy_reaches_dto():
    _, sdk, context = run_add_user(
        make_add_user_request(
            traffic_limit_strategy=proto.TrafficLimitStrategy.MONTH_ROLLING
        )
    )
    context.set_code.assert_not_called()
    dto = sdk.users.create_user.call_args.args[0]
    assert dto.traffic_limit_strategy == TrafficLimitStrategy.MONTH_ROLLING
    assert dump_for_panel(dto)["trafficLimitStrategy"] == "MONTH_ROLLING"


def test_add_user_unset_strategy_is_no_reset():
    # Поведение «как раньше»: незаданная стратегия при создании = NO_RESET.
    request = make_add_user_request()
    assert not request.HasField("traffic_limit_strategy")
    _, sdk, context = run_add_user(request)
    context.set_code.assert_not_called()
    dto = sdk.users.create_user.call_args.args[0]
    assert dto.traffic_limit_strategy == TrafficLimitStrategy.NO_RESET


def test_add_user_unknown_strategy_is_invalid_argument():
    _, sdk, context = run_add_user(make_add_user_request(traffic_limit_strategy=99))
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    sdk.users.create_user.assert_not_awaited()


# --- UpdateUser: стратегия только при HasField (багфикс) ------------------


def test_update_user_without_strategy_does_not_send_it():
    request = proto.UpdateUserRequest(
        uuid=USER_UUID, expire_at=to_ts(datetime(2027, 6, 1))
    )
    assert not request.HasField("traffic_limit_strategy")

    _, sdk, context = run_update_user(request)

    context.set_code.assert_not_called()
    dto = sdk.users.update_user.call_args.args[0]
    assert dto.traffic_limit_strategy is None
    dumped = dump_for_panel(dto)
    assert "trafficLimitStrategy" not in dumped
    assert "expireAt" in dumped


def test_update_user_with_explicit_no_reset_sends_it():
    request = proto.UpdateUserRequest(
        uuid=USER_UUID,
        traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
    )
    assert request.HasField("traffic_limit_strategy")

    _, sdk, context = run_update_user(request)

    context.set_code.assert_not_called()
    dto = sdk.users.update_user.call_args.args[0]
    assert dto.traffic_limit_strategy == TrafficLimitStrategy.NO_RESET
    assert dump_for_panel(dto)["trafficLimitStrategy"] == "NO_RESET"


def test_update_user_with_month_rolling_sends_it():
    _, sdk, context = run_update_user(
        proto.UpdateUserRequest(
            uuid=USER_UUID,
            traffic_limit_strategy=proto.TrafficLimitStrategy.MONTH_ROLLING,
        )
    )
    context.set_code.assert_not_called()
    dto = sdk.users.update_user.call_args.args[0]
    assert dto.traffic_limit_strategy == TrafficLimitStrategy.MONTH_ROLLING
    assert dump_for_panel(dto)["trafficLimitStrategy"] == "MONTH_ROLLING"


def test_update_user_unknown_strategy_is_invalid_argument():
    _, sdk, context = run_update_user(
        proto.UpdateUserRequest(uuid=USER_UUID, traffic_limit_strategy=99)
    )
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    sdk.users.update_user.assert_not_awaited()


def test_update_user_negative_traffic_limit_bytes_is_invalid_argument():
    _, sdk, context = run_update_user(
        proto.UpdateUserRequest(uuid=USER_UUID, traffic_limit_bytes=-1)
    )
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)
    sdk.users.update_user.assert_not_awaited()


def test_update_user_lift_trial_limit_request_shape():
    # Снятие лимита пробной подписки после оплаты: явные bytes=0 +
    # NO_RESET + ACTIVE, БЕЗ сквадов — в PATCH не должно быть
    # activeInternalSquads (иначе снялся бы ban-сквад).
    _, sdk, context = run_update_user(
        proto.UpdateUserRequest(
            uuid=USER_UUID,
            traffic_limit_bytes=0,
            traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
            status=proto.UserStatus.ACTIVE,
        )
    )
    context.set_code.assert_not_called()
    dto = sdk.users.update_user.call_args.args[0]
    dumped = dump_for_panel(dto)
    assert dumped == {
        "uuid": USER_UUID,
        "status": "ACTIVE",
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
    }


def test_update_user_with_squads_still_passes_them_alongside_strategy():
    _, sdk, context = run_update_user(
        proto.UpdateUserRequest(
            uuid=USER_UUID,
            traffic_limit_strategy=proto.TrafficLimitStrategy.DAY,
            active_internal_squads=[SQUAD_UUID],
        )
    )
    context.set_code.assert_not_called()
    dumped = dump_for_panel(sdk.users.update_user.call_args.args[0])
    assert dumped["trafficLimitStrategy"] == "DAY"
    assert dumped["activeInternalSquads"] == [SQUAD_UUID]


# --- Логи: лимит и стратегия видны, секретов нет --------------------------


def test_add_user_log_mentions_limit_without_secrets(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        run_add_user(make_add_user_request(traffic_limit_bytes=FIVE_GIB))
    assert f"traffic_limit_bytes={FIVE_GIB}" in caplog.text
    assert "traffic_limit_strategy=DAY" in caplog.text
    for secret in ("trojan-secret-pass", "ss-secret-pass-123", "test-token"):
        assert secret not in caplog.text
