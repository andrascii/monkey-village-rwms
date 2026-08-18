import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RW_MS_GRPC_PORT", "50051")
os.environ.setdefault("RW_MS_BASE_URL", "http://localhost")
os.environ.setdefault("RW_MS_TOKEN", "test-token")

import grpc  # noqa: E402
import rwmanager_pb2 as proto  # noqa: E402

from config import Config  # noqa: E402
from server import Server  # noqa: E402

from remnawave.exceptions import ApiError  # noqa: E402

NODE_UUID = "091fae80-e27a-4e35-9ddb-8e737f4d2732"
PROFILE_UUID = "5f7c1f24-9adc-4f1b-8ef2-6a1a0f6cbb1d"
INBOUND_UUID_1 = "2a5f4a52-77e5-45f8-b3a4-df6a1c1c8a01"
INBOUND_UUID_2 = "9c0be3af-13f9-4a7a-8ac2-51b1d0a2be02"


def make_server_with_sdk_mock() -> tuple[Server, MagicMock]:
    server = Server(Config())
    sdk = MagicMock()
    server._Server__remnawave = sdk
    return server, sdk


def make_api_error() -> ApiError:
    return ApiError.__new__(ApiError)


def make_node_dto(
    uuid: str = NODE_UUID,
    name: str = "DE Node #1",
    address: str = "1.2.3.4",
) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid,
        name=name,
        address=address,
        is_connected=False,
        is_disabled=False,
        country_code="DE",
        config_profile=SimpleNamespace(
            active_config_profile_uuid=PROFILE_UUID,
            active_inbounds=[
                SimpleNamespace(uuid=INBOUND_UUID_1),
                SimpleNamespace(uuid=INBOUND_UUID_2),
            ],
        ),
    )


def make_create_request() -> proto.CreateNodeRequest:
    return proto.CreateNodeRequest(
        name="DE Node #1",
        address="1.2.3.4",
        port=2222,
        country_code="DE",
        config_profile_uuid=PROFILE_UUID,
        inbound_uuids=[INBOUND_UUID_1, INBOUND_UUID_2],
    )


def test_get_node_secret_returns_pub_key():
    server, sdk = make_server_with_sdk_mock()
    sdk.keygen.generate_key = AsyncMock(
        return_value=SimpleNamespace(pub_key="panel-pub-key")
    )

    response = asyncio.run(server.GetNodeSecret(proto.Empty(), MagicMock()))

    assert response.secret_key == "panel-pub-key"


def test_get_node_secret_api_error_sets_internal():
    server, sdk = make_server_with_sdk_mock()
    sdk.keygen.generate_key = AsyncMock(side_effect=make_api_error())
    context = MagicMock()

    response = asyncio.run(server.GetNodeSecret(proto.Empty(), context))

    assert response.secret_key == ""
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)


def test_get_nodes_exposes_config_profile():
    server, sdk = make_server_with_sdk_mock()
    sdk.nodes.get_all_nodes = AsyncMock(return_value=[make_node_dto()])

    response = asyncio.run(server.GetNodes(proto.Empty(), MagicMock()))

    node = response.nodes[0]
    assert node.config_profile_uuid == PROFILE_UUID
    assert list(node.active_inbound_uuids) == [INBOUND_UUID_1, INBOUND_UUID_2]


def test_create_node_creates_when_absent():
    server, sdk = make_server_with_sdk_mock()
    sdk.nodes.get_all_nodes = AsyncMock(return_value=[])
    sdk.nodes.create_node = AsyncMock(return_value=make_node_dto())

    response = asyncio.run(server.CreateNode(make_create_request(), MagicMock()))

    assert response.uuid == NODE_UUID
    assert response.config_profile_uuid == PROFILE_UUID
    body = sdk.nodes.create_node.call_args.args[0]
    assert body.name == "DE Node #1"
    assert body.address == "1.2.3.4"
    assert body.port == 2222
    assert body.country_code == "DE"
    assert str(body.config_profile.active_config_profile_uuid) == PROFILE_UUID
    assert [str(u) for u in body.config_profile.active_inbounds] == [
        INBOUND_UUID_1,
        INBOUND_UUID_2,
    ]


def test_create_node_defaults_country_code_when_missing():
    server, sdk = make_server_with_sdk_mock()
    sdk.nodes.get_all_nodes = AsyncMock(return_value=[])
    sdk.nodes.create_node = AsyncMock(return_value=make_node_dto())
    request = make_create_request()
    request.ClearField("country_code")

    asyncio.run(server.CreateNode(request, MagicMock()))

    body = sdk.nodes.create_node.call_args.args[0]
    assert body.country_code == "XX"


def test_create_node_idempotent_by_name():
    server, sdk = make_server_with_sdk_mock()
    existing = make_node_dto(address="5.6.7.8")
    sdk.nodes.get_all_nodes = AsyncMock(return_value=[existing])
    sdk.nodes.create_node = AsyncMock()

    response = asyncio.run(server.CreateNode(make_create_request(), MagicMock()))

    assert response.uuid == NODE_UUID
    assert response.address == "5.6.7.8"
    sdk.nodes.create_node.assert_not_called()


def test_create_node_idempotent_by_address():
    server, sdk = make_server_with_sdk_mock()
    existing = make_node_dto(name="Other name")
    sdk.nodes.get_all_nodes = AsyncMock(return_value=[existing])
    sdk.nodes.create_node = AsyncMock()

    response = asyncio.run(server.CreateNode(make_create_request(), MagicMock()))

    assert response.name == "Other name"
    sdk.nodes.create_node.assert_not_called()


def test_create_node_api_error_sets_internal():
    server, sdk = make_server_with_sdk_mock()
    sdk.nodes.get_all_nodes = AsyncMock(return_value=[])
    sdk.nodes.create_node = AsyncMock(side_effect=make_api_error())
    context = MagicMock()

    response = asyncio.run(server.CreateNode(make_create_request(), context))

    assert response.uuid == ""
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
