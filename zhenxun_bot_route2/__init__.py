"""
Bot路由器 v2.0
==============

核心逻辑:
1. 主Bot接收所有消息并处理，副Bot不接收消息仅作为发送通道
2. 受管插件(在副Bot配置中注册的插件)由余量最多的副Bot发送
3. 非受管插件: 主Bot发送，受主Bot限额约束
4. 主Bot不参与受管插件的发送
5. 副Bot满载时发送一次"消息已满"通知，随后该Bot今日不再发送
6. 0点自动重置（通过数据库stat_date实现）
7. 白名单插件和超级用户(可配置)绕过限额
8. 消息去重: 防止多Bot发送相同消息

配置文件: 插件目录下 config.yaml
"""

from nonebot import get_driver
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor, run_preprocessor
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import Alconna, Args, Arparma, on_alconna
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .commands import get_command_handlers
from .config import CONFIG_PATH, RouteConfig
from .dedup import MessageDedup
from .models import BotDailyStats, BotPluginDailyStats
from .patches import (
    apply_call_api_patch,
    apply_get_bots_patch,
    get_current_plugin,
    get_original_get_bots,
    set_dedup,
)

_WHITELIST_PREFIXES = ("zhenxun.builtin_plugins", "zhenxun_bot_route2")


def _is_whitelisted(module: str) -> bool:
    return any(module.startswith(prefix) for prefix in _WHITELIST_PREFIXES)


__plugin_meta__ = PluginMetadata(
    name="Bot路由器",
    description="v2.0: 余量优先路由+满载通知+消息去重+白名单+超级用户绕过",
    usage="发送'负载均衡 帮助'查看使用说明",
    extra={
        "author": "AI Assistant",
        "version": "2.0.0",
        "priority": 1,
    },
)

Config.add_plugin_config(
    "route",
    "ENABLED",
    True,
    help="启用Bot路由功能",
    default_value=True,
    type=bool,
)

Config.add_plugin_config(
    "route",
    "DEDUP_ENABLED",
    True,
    help="启用消息去重",
    default_value=True,
    type=bool,
)

Config.add_plugin_config(
    "route",
    "DEDUP_WINDOW",
    20,
    help="消息去重时间窗口（秒）",
    default_value=20,
    type=int,
)


class LoadBalancer:
    _instance: "LoadBalancer | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = RouteConfig()
            cls._instance._intercept_superusers = False
        return cls._instance

    @property
    def config(self) -> RouteConfig:
        return self._config

    @property
    def intercept_superusers(self) -> bool:
        return self._intercept_superusers

    @intercept_superusers.setter
    def intercept_superusers(self, value: bool):
        self._intercept_superusers = value

    def toggle_intercept_superusers(self) -> bool:
        self._intercept_superusers = not self._intercept_superusers
        return self._intercept_superusers

    @classmethod
    def get_instance(cls) -> "LoadBalancer":
        return cls()

    def reload_config(self):
        self.config.load()

    _group_members_cache: dict[str, dict[str, float]] = {}

    async def _bot_in_group(self, bot_id: str, group_id: str, bots: dict) -> bool:
        cache_key = f"{bot_id}:{group_id}"
        import time

        now = time.time()
        cached = self._group_members_cache.get(cache_key)
        if cached is not None:
            if now - cached["ts"] < 300:
                return cached["in_group"]

        if bot_id not in bots:
            return False

        try:
            await bots[bot_id].get_group_member_info(
                group_id=int(group_id), user_id=int(bot_id)
            )
            self._group_members_cache[cache_key] = {"in_group": True, "ts": now}
            return True
        except Exception:
            self._group_members_cache[cache_key] = {"in_group": False, "ts": now}
            return False

    def invalidate_group_cache(self, bot_id: str, group_id: str):
        cache_key = f"{bot_id}:{group_id}"
        self._group_members_cache.pop(cache_key, None)

    async def get_primary_bot_if_in_group(
        self, group_id: str | None, bots: dict
    ) -> Bot | None:
        config = self.config
        if not config.primary_bot or config.primary_bot not in bots:
            return None
        if group_id and not await self._bot_in_group(
            config.primary_bot, group_id, bots
        ):
            return None
        return bots[config.primary_bot]

    async def has_available_bot(self, plugin: str, group_id: str | None = None) -> bool:
        bots = get_original_get_bots()
        config = self.config

        if plugin in config.primary_plugins and config.primary_bot in bots:
            if group_id and not await self._bot_in_group(
                config.primary_bot, group_id, bots
            ):
                pass
            else:
                limit = config.get_primary_plugin_limit(plugin)
                if limit is not None:
                    try:
                        count = await BotPluginDailyStats.get_count(
                            config.primary_bot, plugin=plugin
                        )
                        if count < limit:
                            return True
                    except Exception as e:
                        logger.warning(f"[Route] 主Bot插件配额查询异常: {e}")
                else:
                    return True

        candidates = config.get_bots_for_plugin(plugin)
        for cfg in candidates:
            if cfg.bot_id not in bots:
                continue
            if group_id and not await self._bot_in_group(cfg.bot_id, group_id, bots):
                continue
            try:
                if not await BotDailyStats.is_full(cfg.bot_id, cfg.daily_limit):
                    return True
            except Exception as e:
                logger.warning(f"[Route] 副Bot {cfg.bot_id} 配额查询异常: {e}")

        return False

    async def select_bot(self, plugin: str, group_id: str | None = None) -> Bot | None:
        bots = get_original_get_bots()
        config = self.config

        best_id: str | None = None
        best_remaining: int = -1

        if plugin in config.primary_plugins and config.primary_bot in bots:
            in_group = not group_id or await self._bot_in_group(
                config.primary_bot, group_id, bots
            )
            if in_group:
                limit = config.get_primary_plugin_limit(plugin)
                if limit is not None:
                    try:
                        count = await BotPluginDailyStats.get_count(
                            config.primary_bot, plugin=plugin
                        )
                        remaining = max(0, limit - count)
                        if remaining > 0 and remaining > best_remaining:
                            best_remaining = remaining
                            best_id = config.primary_bot
                    except Exception as e:
                        logger.warning(f"[Route] 主Bot插件配额查询异常: {e}")
                else:
                    best_id = config.primary_bot
                    best_remaining = 999999
            else:
                logger.info(
                    f"[Route] 主Bot {config.primary_bot} 不在群 {group_id}，跳过"
                )

        candidates = config.get_bots_for_plugin(plugin)
        for cfg in candidates:
            if cfg.bot_id not in bots:
                logger.info(f"[Route] 副Bot {cfg.bot_id} 不在线，跳过")
                continue

            if group_id and not await self._bot_in_group(cfg.bot_id, group_id, bots):
                logger.info(f"[Route] 副Bot {cfg.bot_id} 不在群 {group_id}，跳过")
                continue

            try:
                remaining = await BotDailyStats.get_remaining(
                    cfg.bot_id, cfg.daily_limit
                )
                if remaining <= 0:
                    count = await BotDailyStats.get_count(cfg.bot_id)
                    logger.info(
                        f"[Route] 副Bot {cfg.bot_id} 今日已满 "
                        f"({count}/{cfg.daily_limit})，跳过"
                    )
                    continue

                if remaining > best_remaining:
                    best_remaining = remaining
                    best_id = cfg.bot_id
            except Exception as e:
                logger.warning(f"[Route] 副Bot {cfg.bot_id} 配额查询异常: {e}，跳过")

        if best_id:
            if best_id == config.primary_bot:
                logger.info(
                    f"[Route] 插件 '{plugin}' 选择主Bot {best_id} 发送 "
                    f"(剩余: {best_remaining})"
                )
            else:
                logger.info(
                    f"[Route] 插件 '{plugin}' 选择副Bot {best_id} 发送 "
                    f"(剩余: {best_remaining})"
                )
            return bots[best_id]

        logger.info(f"[Route] 插件 '{plugin}' 无可用Bot")
        return None


async def _send_alert(bot: Bot, session: Uninfo, message: str):
    token = get_current_plugin().set(None)
    try:
        if session.group:
            await bot.send_group_msg(group_id=int(session.group.id), message=message)
        elif session.user:
            await bot.send_private_msg(user_id=int(session.user.id), message=message)
    except Exception:
        pass
    finally:
        get_current_plugin().reset(token)


@event_preprocessor
async def filter_secondary_bot(event: Event, bot: Bot):
    if not Config.get_config("route", "ENABLED"):
        return

    config = LoadBalancer.get_instance().config
    if not config.primary_bot or not config.secondary_bots:
        return

    if not hasattr(event, "group_id") or getattr(event, "group_id", None) is None:
        return

    bot_id = str(bot.self_id)
    user_id = str(getattr(event, "user_id", ""))

    if config.is_secondary_bot(user_id):
        raise IgnoredException(f"[Route] 过滤Bot消息: {user_id}")

    if config.is_secondary_bot(bot_id):
        raise IgnoredException(f"[Route] 副Bot不接收消息: {bot_id}")


def _is_bypassed(module: str, session: Uninfo, lb: LoadBalancer) -> bool:
    if _is_whitelisted(module):
        logger.info(f"[Route] 白名单插件 '{module}' 放行")
        get_current_plugin().set(module)
        return True
    superusers = get_driver().config.superusers
    user_id = str(session.user.id) if session.user else ""
    if user_id in superusers and not lb.intercept_superusers:
        logger.info(f"[Route] 超级用户 '{user_id}' 触发 '{module}'，放行")
        get_current_plugin().set(module)
        return True
    return False


async def _check_unmanaged_plugin_quota(
    module: str, config: RouteConfig, session: Uninfo, bot: Bot
):
    if not config.primary_daily_limit:
        logger.info(f"[Route] 非受管插件 '{module}'，主Bot发送")
        return
    try:
        if not await BotDailyStats.is_full(
            config.primary_bot, config.primary_daily_limit
        ):
            logger.info(f"[Route] 非受管插件 '{module}'，主Bot发送")
            return
        count = await BotDailyStats.get_count(config.primary_bot)
        logger.info(
            f"[Route] 主Bot今日限额已满 "
            f"({count}/{config.primary_daily_limit})，"
            f"非受管插件 '{module}' 拦截"
        )
        try:
            if await BotDailyStats.should_alert(config.primary_bot):
                bots = get_original_get_bots()
                if config.primary_bot in bots:
                    await _send_alert(
                        bots[config.primary_bot],
                        session,
                        "⚠️ 主Bot今日消息限额已满，部分功能不可用",
                    )
        except Exception as e:
            logger.warning(f"[Route] 主Bot满载通知失败: {e}")
        raise IgnoredException(f"[Route] 主Bot限额已满: {module}")
    except IgnoredException:
        raise
    except Exception as e:
        logger.warning(f"[Route] 主Bot限额查询异常: {e}，放行")


async def _log_managed_bot_status(module: str, config: RouteConfig):
    available: list[str] = []
    bots = get_original_get_bots()
    for cfg in config.get_bots_for_plugin(module):
        if cfg.bot_id in bots:
            try:
                remaining = await BotDailyStats.get_remaining(
                    cfg.bot_id, cfg.daily_limit
                )
                if remaining > 0:
                    available.append(f"{cfg.bot_id}(余{remaining})")
                else:
                    available.append(f"{cfg.bot_id}(已满)")
            except Exception:
                available.append(f"{cfg.bot_id}(查询失败)")
        else:
            available.append(f"{cfg.bot_id}(离线)")
    logger.info(f"[Route] 受管插件 '{module}' 副Bot状态: {', '.join(available)}，放行")


async def _send_managed_full_alerts(
    module: str, config: RouteConfig, session: Uninfo, bot: Bot
):
    bots = get_original_get_bots()
    for cfg in config.get_bots_for_plugin(module):
        if cfg.bot_id not in bots:
            continue
        try:
            if await BotDailyStats.is_full(cfg.bot_id, cfg.daily_limit):
                if await BotDailyStats.should_alert(cfg.bot_id):
                    alert_bot = bots.get(config.primary_bot, bot)
                    await _send_alert(
                        alert_bot,
                        session,
                        f"⚠️ 副Bot {cfg.bot_id} 今日消息限额已满，相关功能不可用",
                    )
        except Exception as e:
            logger.warning(f"[Route] 发送满载通知失败: {e}")


@run_preprocessor
async def check_plugin_quota(matcher: Matcher, bot: Bot, session: Uninfo):
    if not Config.get_config("route", "ENABLED"):
        return

    config = LoadBalancer.get_instance().config
    module = getattr(matcher, "plugin_name", "") or ""

    if not module or not config.primary_bot or not config.secondary_bots:
        return

    if not session.group:
        return

    lb = LoadBalancer.get_instance()

    if _is_bypassed(module, session, lb):
        return

    get_current_plugin().set(module)

    if not config.is_managed_plugin(module):
        await _check_unmanaged_plugin_quota(module, config, session, bot)
        return

    group_id = str(session.group.id) if session.group else None

    if await lb.has_available_bot(module, group_id=group_id):
        await _log_managed_bot_status(module, config)
        return

    logger.info(f"[Route] 受管插件 '{module}' 所有副Bot已满/离线，拦截")
    await _send_managed_full_alerts(module, config, session, bot)
    raise IgnoredException(f"[Route] 所有副Bot已满: {module}")


@get_driver().on_startup
async def init_router():
    lb = LoadBalancer.get_instance()
    lb.config.load()
    apply_get_bots_patch(lb.config)
    if Config.get_config("route", "DEDUP_ENABLED"):
        window = Config.get_config("route", "DEDUP_WINDOW") or 20
        set_dedup(MessageDedup(window_seconds=window))
        logger.info(f"[Route] 消息去重已启用 (窗口: {window}秒)")
    else:
        set_dedup(None)
        logger.info("[Route] 消息去重已禁用")
    logger.info("[Route] v2.0 配置已加载，等待Bot连接应用补丁...")


@get_driver().on_bot_connect
async def on_bot_connected(bot: Bot):
    lb = LoadBalancer.get_instance()
    apply_call_api_patch(lb)
    logger.info(f"[Route] Bot {bot.self_id} 已连接，call_api 补丁已应用")


lb_manage = on_alconna(Alconna("负载均衡", Args["action", str]), priority=1, block=True)


@lb_manage.handle()
async def handle_lb_manage(bot: Bot, arparma: Arparma):
    action = getattr(arparma, "action", "").strip()
    lb = LoadBalancer.get_instance()
    handlers = get_command_handlers(lb, CONFIG_PATH)

    for keys, handler in handlers.items():
        if action in keys:
            if msg := await handler(bot):
                await lb_manage.finish(msg)
            return

    await lb_manage.finish("可用: 状态/插件/查看/重载/重置/测试/拦截/帮助")
