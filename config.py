import os

from dotenv import load_dotenv

load_dotenv()

RW_MS_LOG_LEVEL = "RW_MS_LOG_LEVEL"
RW_MS_BASE_URL = "RW_MS_BASE_URL"
RW_MS_TOKEN = "RW_MS_TOKEN"
RW_MS_GRPC_PORT = "RW_MS_GRPC_PORT"

def get_required_env_var(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise ValueError(f"environment variable {var_name} was not set")

    return value


def get_optional_env_var(var_name: str, default: str) -> str:
    return os.getenv(var_name, default)


class Config:
    def __init__(self):
        self.log_level: str = get_optional_env_var(RW_MS_LOG_LEVEL, "info")
        self.grpc_port = get_required_env_var(RW_MS_GRPC_PORT)
        self.base_url = get_required_env_var(RW_MS_BASE_URL)
        self.token = get_required_env_var(RW_MS_TOKEN)
