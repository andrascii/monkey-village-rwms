import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

from remnawave.models import UserResponseDto  # noqa: E402

from server import dto_to_proto_user  # noqa: E402

USER_PAYLOAD = {
    "uuid": "0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b",
    "id": 1,
    "shortUuid": "abc123",
    "username": "tg_100500",
    "status": "ACTIVE",
    "trafficLimitBytes": 107374182400,
    "trafficLimitStrategy": "NO_RESET",
    "expireAt": "2026-08-01T00:00:00Z",
    "trojanPassword": "trojan-password",
    "vlessUuid": "6a5f9f0e-6c3b-4f0e-9d2a-1b2c3d4e5f60",
    "ssPassword": "ss-password-123",
    "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-01-02T00:00:00Z",
    "subscriptionUrl": "https://sub.example.com/abc123",
    "activeInternalSquads": [],
    "userTraffic": {
        "usedTrafficBytes": 1024.0,
        "lifetimeUsedTrafficBytes": 2048.0,
    },
}


def make_user(**overrides) -> UserResponseDto:
    payload = {**USER_PAYLOAD, **overrides}
    return UserResponseDto.model_validate(payload)


def test_traffic_limit_bytes_int():
    user = make_user()
    response = dto_to_proto_user(user)
    assert response.traffic_limit_bytes == 107374182400


def test_traffic_limit_bytes_float():
    # remnawave SDK >=2.8.0 объявляет traffic_limit_bytes как float.
    # На SDK 2.7.x pydantic приводит значение к int, поэтому float
    # подставляется в обход валидации — так тест воспроизводит 2.8.0
    # на любой версии SDK.
    user = make_user()
    object.__setattr__(user, "traffic_limit_bytes", 107374182400.0)
    assert isinstance(user.traffic_limit_bytes, float)

    response = dto_to_proto_user(user)

    assert response.traffic_limit_bytes == 107374182400
    assert isinstance(response.traffic_limit_bytes, int)


def test_traffic_limit_bytes_none():
    user = make_user()
    object.__setattr__(user, "traffic_limit_bytes", None)

    response = dto_to_proto_user(user)

    assert not response.HasField("traffic_limit_bytes")


def test_used_traffic_bytes_float():
    # used_traffic_bytes и lifetime_used_traffic_bytes в SDK float,
    # в proto — double; конвертация не должна падать.
    user = make_user()
    response = dto_to_proto_user(user)
    assert response.used_traffic_bytes == 1024.0
    assert response.lifetime_used_traffic_bytes == 2048.0
