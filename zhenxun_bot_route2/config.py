from dataclasses import dataclass, field
from pathlib import Path

from zhenxun.services.log import logger

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class PluginLimit:
    plugin: str
    daily_limit: int


@dataclass
class SecondaryBotConfig:
    bot_id: str
    daily_limit: int
    plugins: list[str] = field(default_factory=list)


@dataclass
class RouteConfig:
    primary_bot: str = ""
    primary_daily_limit: int = 500
    primary_plugins: list[str] = field(default_factory=list)
    primary_plugin_limits: list[PluginLimit] = field(default_factory=list)
    secondary_bots: list[SecondaryBotConfig] = field(default_factory=list)

    def is_secondary_bot(self, bot_id: str) -> bool:
        return any(b.bot_id == bot_id for b in self.secondary_bots)

    def is_managed_plugin(self, plugin: str) -> bool:
        if plugin in self.primary_plugins:
            return True
        return any(plugin in b.plugins for b in self.secondary_bots)

    def get_bots_for_plugin(self, plugin: str) -> list[SecondaryBotConfig]:
        return [b for b in self.secondary_bots if plugin in b.plugins]

    def get_primary_plugin_limit(self, plugin: str) -> int | None:
        for pl in self.primary_plugin_limits:
            if pl.plugin == plugin:
                return pl.daily_limit
        return None

    def get_all_plugins(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for p in self.primary_plugins:
            if p not in seen:
                seen.add(p)
                result.append(p)
        for b in self.secondary_bots:
            for p in b.plugins:
                if p not in seen:
                    seen.add(p)
                    result.append(p)
        return result

    def load(self):
        if not CONFIG_PATH.exists():
            self._create_default_config()
            return
        try:
            import yaml

            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._load_from_dict(data)
            logger.info(
                f"[Route] 配置加载: 主Bot={self.primary_bot}, "
                f"主Bot限额={self.primary_daily_limit}, "
                f"主Bot受管插件={self.primary_plugins}, "
                f"主Bot插件限额={[(pl.plugin, pl.daily_limit) for pl in self.primary_plugin_limits]}, "
                f"副Bot={[(b.bot_id, b.daily_limit, b.plugins) for b in self.secondary_bots]}"
            )
        except Exception as e:
            logger.error(f"[Route] 加载配置失败: {e}")

    def _load_from_dict(self, data: dict):
        self.primary_bot = str(data.get("primary_bot", ""))
        try:
            self.primary_daily_limit = int(data.get("primary_daily_limit", 500))
        except (ValueError, TypeError):
            self.primary_daily_limit = 500
        self.primary_plugins = [str(p) for p in data.get("primary_plugins", [])]
        self.primary_plugin_limits = []
        for item in data.get("primary_plugin_limits", []):
            if isinstance(item, dict):
                plugin = str(item.get("plugin", ""))
                try:
                    daily_limit = int(item.get("daily_limit", 0))
                except (ValueError, TypeError):
                    daily_limit = 0
                if plugin:
                    self.primary_plugin_limits.append(PluginLimit(plugin, daily_limit))
        self.secondary_bots = []
        for item in data.get("secondary_bots", []):
            if isinstance(item, dict):
                bot_id = str(item.get("bot_id", ""))
                try:
                    daily_limit = int(item.get("daily_limit", 200))
                except (ValueError, TypeError):
                    daily_limit = 200
                plugins = [str(p) for p in item.get("plugins", [])]
                if bot_id:
                    self.secondary_bots.append(
                        SecondaryBotConfig(bot_id, daily_limit, plugins)
                    )

    def _create_default_config(self):
        content = """\
primary_bot: "主Bot_QQ号"
primary_daily_limit: 500

primary_plugins:
  - "zhenxun_plugin_fishing"

primary_plugin_limits:
  - plugin: "zhenxun_plugin_fishing"
    daily_limit: 120

secondary_bots:
  - bot_id: "副Bot1_QQ号"
    daily_limit: 300
    plugins:
      - "nonebot_plugin_handle"

  - bot_id: "副Bot2_QQ号"
    daily_limit: 300
    plugins:
      - "zhenxun_plugin_fishing"
"""
        CONFIG_PATH.write_text(content, encoding="utf-8")
        logger.info(f"[Route] 已创建默认配置: {CONFIG_PATH}")
