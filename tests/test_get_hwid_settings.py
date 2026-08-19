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

import rwmanager_pb2 as proto  # noqa: E402
from config import Config  # noqa: E402
from server import Server  # noqa: E402


def make_server_with_settings(hwid_settings) -> tuple[Server, MagicMock]:
    server = Server(Config())

    settings = MagicMock()
    settings.hwid_settings = hwid_settings

    sdk = MagicMock()
    sdk.subscriptions_settings.get_settings = AsyncMock(return_value=settings)
    server._Server__remnawave = sdk

    return server, sdk


def test_get_hwid_settings_returns_panel_fallback_limit():
    hwid = MagicMock()
    hwid.enabled = True
    hwid.fallback_device_limit = 25

    server, _ = make_server_with_settings(hwid)
    context = MagicMock()

    reply = asyncio.run(server.GetHwidSettings(proto.Empty(), context))

    assert reply.enabled is True
    assert reply.HasField("fallback_device_limit")
    assert reply.fallback_device_limit == 25
    context.set_code.assert_not_called()


def test_get_hwid_settings_without_panel_settings():
    """Панель без блока hwidSettings — отвечаем enabled=False без ошибки."""
    server, _ = make_server_with_settings(None)
    context = MagicMock()

    reply = asyncio.run(server.GetHwidSettings(proto.Empty(), context))

    assert reply.enabled is False
    assert not reply.HasField("fallback_device_limit")
    context.set_code.assert_not_called()


def test_get_hwid_settings_api_error_sets_internal():
    server = Server(Config())
    sdk = MagicMock()
    sdk.subscriptions_settings.get_settings = AsyncMock(
        side_effect=RuntimeError("panel down")
    )
    server._Server__remnawave = sdk
    context = MagicMock()

    reply = asyncio.run(server.GetHwidSettings(proto.Empty(), context))

    assert not reply.HasField("fallback_device_limit")
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
