import os
import grpc
import orjson
import logging

import rwmanager_pb2 as proto
import rwmanager_pb2_grpc

from typing import Optional
from datetime import datetime, timezone
from google.protobuf.timestamp_pb2 import Timestamp

from remnawave import RemnawaveSDK
from remnawave.models import UserResponseDto

from remnawave.enums import UserStatus, TrafficLimitStrategy
from remnawave.exceptions import ApiError
from remnawave.models import (
    CreateUserRequestDto,
    UpdateUserRequestDto,
    UserResponseDto,
    ActiveInternalSquadDto,
    HappCrypto,
    CreateNodeRequestDto,
    NodeConfigProfileRequestDto,
)

from config import Config

# Прод-панель не включает userUuid в элементы ответа
# /api/hwid/devices/{userUuid} (запрос и так сделан по uuid пользователя),
# а модель SDK 2.7.1 требует это поле строго — pydantic падал с
# ValidationError на каждом списке устройств. Поле нам не нужно, делаем
# его опциональным прямо в модели SDK (версия запинена в requirements).
from uuid import UUID as _UUID
from remnawave.models.hwid import (
    HwidDeviceDto as _HwidDeviceDto,
    GetUserHwidDevicesResponseDto as _GetHwidResp,
    DeleteUserHwidDeviceResponseDto as _DelHwidResp,
    CreateUserHwidDeviceResponseDto as _CreateHwidResp,
)

_HwidDeviceDto.model_fields["user_uuid"].default = None
_HwidDeviceDto.model_fields["user_uuid"].annotation = Optional[_UUID]
_HwidDeviceDto.model_rebuild(force=True)
# Родительские модели держат встроенную схему ребёнка — пересобираем и их
_GetHwidResp.model_rebuild(force=True)
_DelHwidResp.model_rebuild(force=True)
_CreateHwidResp.model_rebuild(force=True)

# Формат дат, который принимают эндпоинты /api/bandwidth-stats/* панели
STATS_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


def from_proto_timestamp(ts: Timestamp) -> datetime:
    return ts.ToDatetime()


def to_ts(dt: Optional[datetime]) -> Optional[Timestamp]:
    if dt is None:
        return None

    dt = dt.replace(tzinfo=timezone.utc)
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def RemnawaveUserStatusToProto(status: UserStatus) -> proto.UserStatus:
    """
    Converts a Remnawave UserStatus to a UserStatus protobuf enum.
    """
    if status == UserStatus.ACTIVE:
        return proto.UserStatus.ACTIVE
    elif status == UserStatus.DISABLED:
        return proto.UserStatus.DISABLED
    elif status == UserStatus.LIMITED:
        return proto.UserStatus.LIMITED
    elif status == UserStatus.EXPIRED:
        return proto.UserStatus.EXPIRED
    else:
        raise ValueError(f"Invalid user status: {status}")


def RemnawaveTrafficLimitStrategyToProto(
    strategy: TrafficLimitStrategy,
) -> proto.TrafficLimitStrategy:
    """
    Converts a Remnawave TrafficLimitStrategy to a TrafficLimitStrategy protobuf enum.
    """
    if strategy == TrafficLimitStrategy.NO_RESET:
        return proto.TrafficLimitStrategy.NO_RESET
    elif strategy == TrafficLimitStrategy.DAY:
        return proto.TrafficLimitStrategy.DAY
    elif strategy == TrafficLimitStrategy.WEEK:
        return proto.TrafficLimitStrategy.WEEK
    elif strategy == TrafficLimitStrategy.MONTH:
        return proto.TrafficLimitStrategy.MONTH
    else:
        raise ValueError(f"Invalid traffic limit strategy: {strategy}")


def dto_to_proto_active_squad(
    squad: ActiveInternalSquadDto,
) -> proto.ActiveInternalSquad:
    return proto.ActiveInternalSquad(
        uuid=str(squad.uuid),
        name=squad.name,
    )


def dto_to_proto_happ(h: HappCrypto) -> proto.HappCrypto:
    return proto.HappCrypto(crypto_link=h.crypto_link)


def dto_to_proto_user(user: UserResponseDto) -> proto.UserResponse:
    """
    Converts a UserResponseDto to a UserResponse protobuf message.
    """

    response = proto.UserResponse(
        uuid=str(user.uuid),
        short_uuid=user.short_uuid,
        username=user.username,
        used_traffic_bytes=user.used_traffic_bytes,
        lifetime_used_traffic_bytes=user.lifetime_used_traffic_bytes,
        # В remnawave SDK >=2.8.0 traffic_limit_bytes стал float, а в proto это
        # int64 — без явного int() присвоение падает с TypeError (инцидент
        # 2026-07-07). Каст корректен и для int из SDK <=2.7.x.
        traffic_limit_bytes=(
            int(user.traffic_limit_bytes)
            if user.traffic_limit_bytes is not None
            else None
        ),
        sub_last_user_agent=(
            user.sub_last_user_agent if user.sub_last_user_agent else None
        ),
        sub_last_opened_at=to_ts(user.sub_last_opened_at),
        expire_at=to_ts(user.expire_at),
        online_at=to_ts(user.online_at),
        sub_revoked_at=to_ts(user.sub_revoked_at),
        last_traffic_reset_at=to_ts(user.last_traffic_reset_at),
        trojan_password=user.trojan_password,
        vless_uuid=str(user.vless_uuid),
        ss_password=user.ss_password,
        description=user.description if user.description else None,
        telegram_id=user.telegram_id if user.telegram_id else None,
        email=user.email if user.email else None,
        hwid_device_limit=(
            user.hwid_device_limit if user.hwid_device_limit is not None else None
        ),
        subscription_url=user.subscription_url,
        first_connected=to_ts(user.first_connected),
        last_trigger_threshold=(
            user.last_trigger_threshold if user.last_trigger_threshold else None
        ),
        active_internal_squads=[
            dto_to_proto_active_squad(s) for s in user.active_internal_squads
        ],
        happ=dto_to_proto_happ(user.happ),
        created_at=to_ts(user.created_at),
        updated_at=to_ts(user.updated_at),
    )

    if user.status is not None:
        response.status = RemnawaveUserStatusToProto(user.status)

    if user.traffic_limit_strategy is not None:
        response.traffic_limit_strategy = RemnawaveTrafficLimitStrategyToProto(
            user.traffic_limit_strategy
        )

    return response



def dto_to_proto_hwid_device(d) -> proto.HwidDevice:
    dev = proto.HwidDevice(hwid=d.hwid)
    if d.platform is not None:
        dev.platform = d.platform
    if d.os_version is not None:
        dev.os_version = d.os_version
    if d.device_model is not None:
        dev.device_model = d.device_model
    if d.user_agent is not None:
        dev.user_agent = d.user_agent
    if d.created_at is not None:
        dev.created_at.CopyFrom(to_ts(d.created_at.replace(tzinfo=None)))
    if d.updated_at is not None:
        dev.updated_at.CopyFrom(to_ts(d.updated_at.replace(tzinfo=None)))
    return dev


class Server(rwmanager_pb2_grpc.RwManager):
    def __init__(self, config: Config):
        super().__init__()
        self.__config = config
        self.__logger = logging.getLogger(self.__class__.__name__)

        self.__logger.info("remnawave base url: %s", self.__config.base_url)
        self.__logger.info("remnawave token: %s", self.__config.token)

        self.__remnawave = RemnawaveSDK(
            base_url=self.__config.base_url, token=self.__config.token
        )

    async def GetUserByUuid(
        self, request: proto.GetUserByUuidRequest, context: grpc.aio.ServicerContext
    ) -> proto.UserResponse:
        try:
            user = await self.__remnawave.users.get_user_by_uuid(request.uuid)
            return dto_to_proto_user(user)
        except ApiError as e:
            self.__logger.error(f"failed to get user by uuid: {e}")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"failed to get user by uuid: {e}")
            return proto.UserResponse()

    async def GetUserByUsername(
        self, request: proto.GetUserByUsernameRequest, context: grpc.aio.ServicerContext
    ) -> proto.UserResponse:
        try:
            user = await self.__remnawave.users.get_user_by_username(request.username)
            return dto_to_proto_user(user)
        except ApiError as e:
            self.__logger.error(f"failed to get user by username: {e}")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"failed to get user by username: {e}")
            return proto.UserResponse()

    async def GetUserById(
        self, request: proto.GetUserByIdRequest, context: grpc.aio.ServicerContext
    ) -> proto.UserResponse:
        try:
            user = await self.__remnawave.users.get_user_by_id(str(request.id))
            return dto_to_proto_user(user)
        except ApiError as e:
            self.__logger.error(f"failed to get user by id: {e}")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"failed to get user by id: {e}")
            return proto.UserResponse()

    async def AddUser(
        self, request: proto.AddUserRequest, context: grpc.aio.ServicerContext
    ) -> proto.UserResponse:
        try:
            self.__logger.info(f"adding user {request.username}")

            if request.status == proto.UserStatus.ACTIVE:
                status = UserStatus.ACTIVE
            elif request.status == proto.UserStatus.DISABLED:
                status = UserStatus.DISABLED
            elif request.status == proto.UserStatus.LIMITED:
                status = UserStatus.LIMITED
            elif request.status == proto.UserStatus.EXPIRED:
                status = UserStatus.EXPIRED
            else:
                self.__logger.error(f"invalid user status: {request.status}")
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(f"invalid user status: {request.status}")
                return proto.UserResponse()

            if request.traffic_limit_strategy == proto.TrafficLimitStrategy.NO_RESET:
                traffic_limit_strategy = TrafficLimitStrategy.NO_RESET
            elif request.traffic_limit_strategy == proto.TrafficLimitStrategy.DAY:
                traffic_limit_strategy = TrafficLimitStrategy.DAY
            elif request.traffic_limit_strategy == proto.TrafficLimitStrategy.WEEK:
                traffic_limit_strategy = TrafficLimitStrategy.WEEK
            elif request.traffic_limit_strategy == proto.TrafficLimitStrategy.MONTH:
                traffic_limit_strategy = TrafficLimitStrategy.MONTH
            else:
                self.__logger.error(
                    f"invalid traffic limit strategy: {request.traffic_limit_strategy}"
                )
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    f"invalid traffic limit strategy: {request.traffic_limit_strategy}"
                )
                return proto.UserResponse()

            self.__logger.info(f"received request {request}")

            created_user = await self.__remnawave.users.create_user(
                CreateUserRequestDto(
                    username=request.username,
                    email=request.email if request.HasField("email") else None,
                    telegram_id=(
                        request.telegram_id if request.HasField("telegram_id") else None
                    ),
                    expire_at=from_proto_timestamp(request.expire_at),
                    created_at=(
                        from_proto_timestamp(request.created_at)
                        if request.created_at
                        else None
                    ),
                    status=status,
                    traffic_limit_strategy=traffic_limit_strategy,
                    description=(
                        request.description if request.HasField("description") else None
                    ),
                    tag=request.tag if request.HasField("tag") else None,
                    hwidDeviceLimit=(
                        request.hwid_device_limit
                        if request.HasField("hwid_device_limit")
                        else None
                    ),
                    last_traffic_reset_at=(
                        from_proto_timestamp(request.last_traffic_reset_at)
                        if request.last_traffic_reset_at
                        else None
                    ),
                    active_internal_squads=list(request.active_internal_squads),
                )
            )

            self.__logger.info(
                f"user created: {created_user.model_dump_json(by_alias=True)}"
            )
            return dto_to_proto_user(created_user)
        except ApiError as e:
            self.__logger.error(f"add user operation failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"add user operation failed: {e}")
            return proto.UserResponse()

    async def DeleteUser(
        self,
        request: proto.DeleteUserRequest,
        context: grpc.aio.ServicerContext,
    ) -> proto.DeleteUserResponse:
        try:
            self.__logger.info(f"delete user {request.uuid}")
            response = await self.__remnawave.users.delete_user(request.uuid)
            return proto.DeleteUserResponse(is_deleted=response.is_deleted)
        except ApiError as e:
            self.__logger.error(f"delete user operation failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"delete user operation failed: {e}")
            return proto.DeleteUserResponse(is_deleted=False)

    async def UpdateUser(
        self,
        request: proto.UpdateUserRequest,
        context: grpc.aio.ServicerContext,
    ) -> proto.UserResponse:
        try:
            self.__logger.info(f"update user {request.uuid}")

            if request.status == proto.UserStatus.ACTIVE:
                status = UserStatus.ACTIVE
            elif request.status == proto.UserStatus.DISABLED:
                status = UserStatus.DISABLED
            elif request.status == proto.UserStatus.LIMITED:
                status = UserStatus.LIMITED
            elif request.status == proto.UserStatus.EXPIRED:
                status = UserStatus.EXPIRED
            else:
                self.__logger.error(f"invalid user status: {request.status}")
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(f"invalid user status: {request.status}")
                return proto.UserResponse()

            if request.traffic_limit_strategy == proto.TrafficLimitStrategy.NO_RESET:
                traffic_limit_strategy = TrafficLimitStrategy.NO_RESET
            elif request.traffic_limit_strategy == proto.TrafficLimitStrategy.DAY:
                traffic_limit_strategy = TrafficLimitStrategy.DAY
            elif request.traffic_limit_strategy == proto.TrafficLimitStrategy.WEEK:
                traffic_limit_strategy = TrafficLimitStrategy.WEEK
            elif request.traffic_limit_strategy == proto.TrafficLimitStrategy.MONTH:
                traffic_limit_strategy = TrafficLimitStrategy.MONTH
            else:
                self.__logger.error(
                    f"invalid traffic limit strategy: {request.traffic_limit_strategy}"
                )
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    f"invalid traffic limit strategy: {request.traffic_limit_strategy}"
                )
                return proto.UserResponse()

            updated_user = await self.__remnawave.users.update_user(
                UpdateUserRequestDto(
                    uuid=request.uuid,
                    status=status if request.HasField("status") else None,
                    traffic_limit_bytes=(
                        request.traffic_limit_bytes
                        if request.HasField("traffic_limit_bytes")
                        else None
                    ),
                    traffic_limit_strategy=traffic_limit_strategy,
                    expire_at=(
                        from_proto_timestamp(request.expire_at)
                        if request.HasField("expire_at")
                        else None
                    ),
                    last_traffic_reset_at=(
                        from_proto_timestamp(request.last_traffic_reset_at)
                        if request.HasField("last_traffic_reset_at")
                        else None
                    ),
                    description=(
                        request.description if request.HasField("description") else None
                    ),
                    tag=request.tag if request.HasField("tag") else None,
                    telegram_id=(
                        request.telegram_id if request.HasField("telegram_id") else None
                    ),
                    email=request.email if request.HasField("email") else None,
                    hwid_device_limit=(
                        request.hwid_device_limit
                        if request.HasField("hwid_device_limit")
                        else None
                    ),
                    # У repeated-полей proto3 нет признака "не задано": пустой список
                    # означает, что вызывающий сквады не передал. Передаём None, чтобы
                    # exclude_none убрал поле из PATCH и панель не стёрла сквады.
                    # Стереть все сквады через UpdateUser нельзя (и не требуется).
                    active_internal_squads=(
                        list(request.active_internal_squads)
                        if request.active_internal_squads
                        else None
                    ),
                )
            )

            self.__logger.info(
                f"user updated: {updated_user.model_dump_json(by_alias=True)}"
            )
            return dto_to_proto_user(updated_user)
        except ApiError as e:
            self.__logger.error(f"update user operation failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"update user operation failed: {e}")
            return proto.UserResponse()

    async def GetAllUsers(
        self, request: proto.GetAllUsersRequest, context: grpc.aio.ServicerContext
    ) -> proto.GetAllUsersReply:
        try:
            all_users = await self.__remnawave.users.get_all_users(
                request.offset, request.count
            )
            response = proto.GetAllUsersReply(total=all_users.total)

            proto_user_list: list[proto.UserResponse] = [
                dto_to_proto_user(user) for user in all_users.users
            ]

            response.users.extend(proto_user_list)
            return response
        except ApiError as e:
            self.__logger.error(f"failed to get all users: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get all users: {e}")
            return proto.GetAllUsersReply()

    async def GetInbounds(
        self, request: proto.Empty, context: grpc.aio.ServicerContext
    ) -> proto.GetInboundsResponse:
        try:
            inbounds = await self.__remnawave.inbounds.get_inbounds()
            return proto.GetInboundsResponse(inbounds=inbounds)
        except ApiError as e:
            self.__logger.error(f"failed to get inbounds: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get inbounds: {e}")
            return proto.GetInboundsResponse()

    @staticmethod
    def _dto_to_proto_node(node) -> proto.Node:
        # config_profile обязателен в DTO панели, но getattr оставлен для
        # обратной совместимости тестов и старых версий SDK.
        config_profile = getattr(node, "config_profile", None)
        profile_uuid = (
            str(config_profile.active_config_profile_uuid)
            if config_profile is not None
            and config_profile.active_config_profile_uuid is not None
            else None
        )
        inbound_uuids = (
            [str(inbound.uuid) for inbound in config_profile.active_inbounds]
            if config_profile is not None
            else []
        )
        return proto.Node(
            uuid=str(node.uuid),
            name=node.name,
            address=node.address,
            is_connected=node.is_connected,
            is_disabled=node.is_disabled,
            country_code=node.country_code,
            config_profile_uuid=profile_uuid,
            active_inbound_uuids=inbound_uuids,
        )

    async def GetNodes(
        self, request: proto.Empty, context: grpc.aio.ServicerContext
    ) -> proto.GetNodesResponse:
        try:
            nodes = await self.__remnawave.nodes.get_all_nodes()
            return proto.GetNodesResponse(
                nodes=[self._dto_to_proto_node(node) for node in nodes]
            )
        except ApiError as e:
            self.__logger.error(f"failed to get nodes: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get nodes: {e}")
            return proto.GetNodesResponse()


    async def GetUserHwidDevices(
        self,
        request: proto.GetUserHwidDevicesRequest,
        context: grpc.aio.ServicerContext,
    ) -> proto.GetUserHwidDevicesResponse:
        try:
            resp = await self.__remnawave.hwid.get_hwid_user(request.user_uuid)
            self.__logger.info(
                "hwid devices listed: user=%s total=%s",
                request.user_uuid,
                int(resp.total),
            )
            return proto.GetUserHwidDevicesResponse(
                total=int(resp.total),
                devices=[dto_to_proto_hwid_device(d) for d in resp.devices],
            )
        except (ApiError, Exception) as e:
            self.__logger.error(
                f"failed to get hwid devices for {request.user_uuid}: {e!r}"
            )
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get hwid devices: {e!r}")
            return proto.GetUserHwidDevicesResponse()

    async def DeleteUserHwidDevice(
        self,
        request: proto.DeleteUserHwidDeviceRequest,
        context: grpc.aio.ServicerContext,
    ) -> proto.DeleteUserHwidDeviceResponse:
        try:
            from remnawave.models import HWIDDeleteRequest

            resp = await self.__remnawave.hwid.delete_hwid_to_user(
                HWIDDeleteRequest(user_uuid=request.user_uuid, hwid=request.hwid)
            )
            self.__logger.info(
                "hwid device deleted: user=%s hwid=%s left=%s",
                request.user_uuid,
                request.hwid,
                int(resp.total),
            )
            return proto.DeleteUserHwidDeviceResponse(
                total=int(resp.total),
                devices=[dto_to_proto_hwid_device(d) for d in resp.devices],
            )
        except (ApiError, Exception) as e:
            self.__logger.error(
                f"failed to delete hwid device {request.hwid} "
                f"for {request.user_uuid}: {e!r}"
            )
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to delete hwid device: {e!r}")
            return proto.DeleteUserHwidDeviceResponse()

    async def GetHwidSettings(
        self, request: proto.Empty, context: grpc.aio.ServicerContext
    ) -> proto.GetHwidSettingsResponse:
        try:
            settings = await self.__remnawave.subscriptions_settings.get_settings()
            hwid = settings.hwid_settings
            if hwid is None:
                self.__logger.info("hwid settings requested: panel has none")
                return proto.GetHwidSettingsResponse(enabled=False)
            self.__logger.info(
                "hwid settings requested: enabled=%s fallback_limit=%s",
                hwid.enabled,
                hwid.fallback_device_limit,
            )
            return proto.GetHwidSettingsResponse(
                enabled=hwid.enabled,
                fallback_device_limit=hwid.fallback_device_limit,
            )
        except (ApiError, Exception) as e:
            self.__logger.error(f"failed to get hwid settings: {e!r}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get hwid settings: {e!r}")
            return proto.GetHwidSettingsResponse()

    async def GetNodeSecret(
        self, request: proto.Empty, context: grpc.aio.ServicerContext
    ) -> proto.GetNodeSecretResponse:
        try:
            key = await self.__remnawave.keygen.generate_key()
            # Сам ключ в лог не пишем: это секрет, которым нода
            # аутентифицируется в панели.
            self.__logger.info("node secret key issued")
            return proto.GetNodeSecretResponse(secret_key=key.pub_key)
        except ApiError as e:
            self.__logger.error(f"failed to get node secret: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get node secret: {e}")
            return proto.GetNodeSecretResponse()

    async def CreateNode(
        self, request: proto.CreateNodeRequest, context: grpc.aio.ServicerContext
    ) -> proto.Node:
        try:
            self.__logger.info(
                "create node: name=%s address=%s port=%s profile=%s",
                request.name,
                request.address,
                request.port,
                request.config_profile_uuid,
            )
            # Идемпотентность: повтор запроса (ретрай сайта после сбоя) не
            # должен плодить дубликаты — существующая нода возвращается как
            # есть и никогда не пересоздаётся (Remnawave Safety Rules).
            existing_nodes = await self.__remnawave.nodes.get_all_nodes()
            for node in existing_nodes:
                if node.name == request.name or node.address == request.address:
                    self.__logger.info(
                        "create node: already exists uuid=%s name=%s address=%s",
                        node.uuid,
                        node.name,
                        node.address,
                    )
                    return self._dto_to_proto_node(node)

            created = await self.__remnawave.nodes.create_node(
                CreateNodeRequestDto(
                    name=request.name,
                    address=request.address,
                    port=request.port,
                    country_code=(
                        request.country_code
                        if request.HasField("country_code")
                        else "XX"
                    ),
                    config_profile=NodeConfigProfileRequestDto(
                        activeConfigProfileUuid=request.config_profile_uuid,
                        activeInbounds=list(request.inbound_uuids),
                    ),
                )
            )
            self.__logger.info(
                "node created: uuid=%s name=%s address=%s",
                created.uuid,
                created.name,
                created.address,
            )
            return self._dto_to_proto_node(created)
        except ApiError as e:
            self.__logger.error(f"failed to create node: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to create node: {e}")
            return proto.Node()

    async def GetNodeUsersUsage(
        self,
        request: proto.GetNodeUsersUsageRequest,
        context: grpc.aio.ServicerContext,
    ) -> proto.GetNodeUsersUsageResponse:
        try:
            self.__logger.info(
                "get node users usage: node=%s start=%s end=%s",
                request.node_uuid,
                request.start.ToDatetime(),
                request.end.ToDatetime(),
            )
            rows = (
                await self.__remnawave.bandwidthstats.get_node_users_usage_legacy_stats(
                    uuid=request.node_uuid,
                    start=request.start.ToDatetime().strftime(STATS_DATE_FORMAT),
                    end=request.end.ToDatetime().strftime(STATS_DATE_FORMAT),
                )
            )
            return proto.GetNodeUsersUsageResponse(
                items=[
                    proto.NodeUserUsage(
                        user_uuid=str(row.user_uuid),
                        username=row.username,
                        total_bytes=row.total,
                        date=row.date,
                    )
                    for row in rows
                ]
            )
        except ApiError as e:
            self.__logger.error(f"failed to get node users usage: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get node users usage: {e}")
            return proto.GetNodeUsersUsageResponse()
