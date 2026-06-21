import sys
from unittest.mock import MagicMock

EXTRA_MOCK_MODULES = [
    "nonebot.exception",
    "nonebot.message",
    "nonebot.plugin",
    "nonebot.log",
    "nonebot_plugin_alconna",
    "nonebot_plugin_uninfo",
    "jinja2",
    "nonebot_plugin_htmlrender",
    "yaml",
]

for mod_name in EXTRA_MOCK_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()


class _SubscriptableMagicMock(MagicMock):
    def __getitem__(self, key):
        return MagicMock()


sys.modules["nonebot_plugin_alconna"].Alconna = _SubscriptableMagicMock
sys.modules["nonebot_plugin_alconna"].Args = _SubscriptableMagicMock()
sys.modules["nonebot_plugin_alconna"].Arparma = _SubscriptableMagicMock
sys.modules["nonebot_plugin_alconna"].on_alconna = _SubscriptableMagicMock()

sys.modules["nonebot_plugin_uninfo"].Uninfo = MagicMock

IgnoredException = type("IgnoredException", (Exception,), {})
sys.modules["nonebot.exception"].IgnoredException = IgnoredException

sys.modules["nonebot.message"].event_preprocessor = lambda f: f
sys.modules["nonebot.message"].run_preprocessor = lambda f: f

PluginMetadata = type("PluginMetadata", (), {"__init__": lambda self, **kw: None})
sys.modules["nonebot.plugin"].PluginMetadata = PluginMetadata
