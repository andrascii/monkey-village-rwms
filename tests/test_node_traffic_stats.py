import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

import grpc  # noqa: E402
import rwmanager_pb2 as proto  # noqa: E402
from google.protobuf.timestamp_pb2 import Timestamp  # noqa: E402

from config import Config  # noqa: E402
from server import Server  # noqa: E402

from remnawave.exceptions import ApiError  # noqa: E402

NODE_UUID = "091fae80-e27a-4e35-9ddb-8e737f4d2732"
USER_UUID = "0f0e409f-31f3-4c91-a6b9-9e26d7bd4e4b"


def make_server_with_sdk_mock() -> tuple[Server, MagicMock]:
    server = Server(Config())
    sdk = MagicMock()
    server._Server__remnawave = sdk
    return server, sdk


def make_api_error() -> ApiError:
    return ApiError.__new__(ApiError)


def to_ts(dt: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def test_get_nodes_converts_dto_fields():
    server, sdk = make_server_with_sdk_mock()
    sdk.nodes.get_all_nodes = AsyncMock(
        return_value=[
            SimpleNamespace(
                uuid=NODE_UUID,
                name="WL IHC #1",
                address="1.2.3.4",
                is_connected=True,
                is_disabled=False,
                country_code="NL",
            )
        ]
    )

    response = asyncio.run(server.GetNodes(proto.Empty(), MagicMock()))

    assert len(response.nodes) == 1
    node = response.nodes[0]
    assert node.uuid == NODE_UUID
    assert node.name == "WL IHC #1"
    assert node.is_connected is True
    assert node.is_disabled is False
    assert node.country_code == "NL"


def test_get_nodes_api_error_sets_internal_status():
    server, sdk = make_server_with_sdk_mock()
    sdk.nodes.get_all_nodes = AsyncMock(side_effect=make_api_error())
    context = MagicMock()

    response = asyncio.run(server.GetNodes(proto.Empty(), context))

    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
    assert len(response.nodes) == 0


def test_get_node_users_usage_passes_range_and_converts_rows():
    server, sdk = make_server_with_sdk_mock()
    sdk.bandwidthstats.get_node_users_usage_legacy_stats = AsyncMock(
        return_value=[
            SimpleNamespace(
                user_uuid=USER_UUID,
                username="1165985802",
                total=1024,
                date="2026-07-10",
            )
        ]
    )

    start = datetime(2026, 7, 9, 23, 50, tzinfo=timezone.utc)
    end = datetime(2026, 7, 10, 0, 20, tzinfo=timezone.utc)
    request = proto.GetNodeUsersUsageRequest(
        node_uuid=NODE_UUID,
        start=to_ts(start),
        end=to_ts(end),
    )

    response = asyncio.run(server.GetNodeUsersUsage(request, MagicMock()))

    call = sdk.bandwidthstats.get_node_users_usage_legacy_stats.await_args
    assert call.kwargs["uuid"] == NODE_UUID
    assert call.kwargs["start"] == "2026-07-09T23:50:00.000Z"
    assert call.kwargs["end"] == "2026-07-10T00:20:00.000Z"

    assert len(response.items) == 1
    item = response.items[0]
    assert item.user_uuid == USER_UUID
    assert item.username == "1165985802"
    assert item.total_bytes == 1024
    assert item.date == "2026-07-10"


def test_get_node_users_usage_api_error_sets_internal_status():
    server, sdk = make_server_with_sdk_mock()
    sdk.bandwidthstats.get_node_users_usage_legacy_stats = AsyncMock(
        side_effect=make_api_error()
    )
    context = MagicMock()

    request = proto.GetNodeUsersUsageRequest(
        node_uuid=NODE_UUID,
        start=to_ts(datetime(2026, 7, 9, tzinfo=timezone.utc)),
        end=to_ts(datetime(2026, 7, 10, tzinfo=timezone.utc)),
    )
    response = asyncio.run(server.GetNodeUsersUsage(request, context))

    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
    assert len(response.items) == 0
