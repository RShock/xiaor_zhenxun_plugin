from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    handle2_strict_mode: bool = False


handle2_config = get_plugin_config(Config)
