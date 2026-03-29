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
    HappCrypto
)

from config import Config


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

def dto_to_proto_active_squad(squad: ActiveInternalSquadDto) -> proto.ActiveInternalSquad:
    return proto.ActiveInternalSquad(
        uuid=str(squad.uuid),
        name=squad.name,
    )

def dto_to_proto_happ(h: HappCrypto) -> proto.HappCrypto:
    return proto.HappCrypto(
        crypto_link=h.crypto_link
    )

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
        traffic_limit_bytes=(
            user.traffic_limit_bytes if user.traffic_limit_bytes is not None else None
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
            dto_to_proto_active_squad(s)
            for s in user.active_internal_squads
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
                    telegram_id=request.telegram_id if request.HasField("telegram_id") else None,
                    expire_at=from_proto_timestamp(request.expire_at),
                    created_at=(
                        from_proto_timestamp(request.created_at)
                        if request.created_at
                        else None
                    ),
                    status=status,
                    traffic_limit_strategy=traffic_limit_strategy,
                    description=request.description if request.HasField("description") else None,
                    tag=request.tag if request.HasField("tag") else None,
                    hwidDeviceLimit=request.hwid_device_limit if request.HasField("hwid_device_limit") else None,
                    last_traffic_reset_at=(
                        from_proto_timestamp(request.last_traffic_reset_at)
                        if request.last_traffic_reset_at
                        else None
                    ),
                    active_internal_squads=list(request.active_internal_squads)
                )
            )

            self.__logger.info(f"user created: {created_user.model_dump_json(by_alias=True)}")
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
                    active_internal_squads=list(request.active_internal_squads)
                )
            )

            self.__logger.info(f"user updated: {updated_user.model_dump_json(by_alias=True)}")
            return dto_to_proto_user(updated_user)
        except ApiError as e:
            self.__logger.error(f"update user operation failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"update user operation failed: {e}")
            return proto.UserResponse()
        
    async def GetAllUsers(self, request: proto.GetAllUsersRequest, context: grpc.aio.ServicerContext) -> proto.GetAllUsersReply:
        try:
            all_users = await self.__remnawave.users.get_all_users(request.offset, request.count)
            response = proto.GetAllUsersReply(total=all_users.total)
        
            proto_user_list: list[proto.UserResponse] = [
                dto_to_proto_user(user)
                for user in all_users.users
            ]

            response.users.extend(proto_user_list)
            return response
        except ApiError as e:
            self.__logger.error(f"failed to get all users: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get all users: {e}")
            return proto.GetAllUsersReply()

    async def GetInbounds(self, request: proto.Empty, context: grpc.aio.ServicerContext) -> proto.GetInboundsResponse:
        try:
            inbounds = await self.__remnawave.inbounds.get_inbounds()
            return proto.GetInboundsResponse(inbounds=inbounds)
        except ApiError as e:
            self.__logger.error(f"failed to get inbounds: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"failed to get inbounds: {e}")
            return proto.GetInboundsResponse()
