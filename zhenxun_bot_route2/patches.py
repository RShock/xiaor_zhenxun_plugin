import inspect
from contextvars import ContextVar
from pathlib import Path

import nonebot
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.exception import ActionFailed

from zhenxun.services.log import logger

from .config import RouteConfig
from .dedup import MessageDedup, count_images
from .models import BotDailyStats, record_send_stats

_current_plugin: ContextVar[str | None] = ContextVar("_current_plugin", default=None)

_original_call_api = None
_original_get_bots = nonebot.get_bots
_SEND_APIS = {"send_msg", "send_group_msg", "send_private_msg"}
_dedup: "MessageDedup | None" = None

_WHITELIST_PREFIXES = ("zhenxun.builtin_plugins", "zhenxun_bot_route2")


def get_current_plugin() -> ContextVar:
    return _current_plugin


def get_original_get_bots():
    driver = get_driver()
    if hasattr(driver, "_bots"):
        return driver._bots
    return _original_get_bots()


def set_dedup(dedup: "MessageDedup | None"):
    global _dedup
    _dedup = dedup


def get_dedup() -> "MessageDedup | None":
    return _dedup


def _walk_plugin_stack(max_depth: int = 15):
    try:
        for frame_info in inspect.stack(context=0)[1 : max_depth + 1]:
            path = Path(frame_info.filename)
            try:
                idx = path.parts.index("plugins")
                if idx + 1 < len(path.parts):
                    yield path.parts[idx + 1]
            except (ValueError, IndexError):
                continue
    except Exception as e:
        logger.debug(f"[Route] 调用栈检测失败: {e}")


def detect_plugin_from_stack(max_depth: int = 15) -> str | None:
    for name in _walk_plugin_stack(max_depth):
        if name != "zhenxun_bot_route2":
            return name
    return None


def extract_group_id(api: str, data: dict) -> str | None:
    if api in ("send_group_msg", "send_msg"):
        gid = data.get("group_id")
        return str(gid) if gid is not None else None
    return None


def is_whitelisted(plugin: str | None) -> bool:
    if not plugin:
        return False
    return any(plugin.startswith(prefix) for prefix in _WHITELIST_PREFIXES)


def apply_call_api_patch(router):
    global _original_call_api
    if _original_call_api is not None:
        return

    _original_call_api = Bot.call_api

    async def _patched_call_api(self: Bot, api: str, **data):
        if api not in _SEND_APIS:
            return await _original_call_api(self, api, **data)

        bot_id = str(self.self_id)
        plugin = detect_plugin_from_stack() or _current_plugin.get()
        group_id = extract_group_id(api, data)

        if not plugin or is_whitelisted(plugin):
            return await _original_call_api(self, api, **data)

        if group_id is None:
            return await _original_call_api(self, api, **data)

        if _dedup and _dedup.check_and_record(bot_id, data.get("message", "")):
            return None

        config = router.config

        if not config.is_managed_plugin(plugin):
            if config.primary_daily_limit:
                try:
                    if await BotDailyStats.is_full(bot_id, config.primary_daily_limit):
                        logger.info(
                            f"[Route] 主Bot {bot_id} 今日限额已满 "
                            f"({await BotDailyStats.get_count(bot_id)}/{config.primary_daily_limit})，"
                            f"非受管插件 '{plugin}' 发送被拒"
                        )
                        return None
                except Exception as e:
                    logger.warning(f"[Route] 主Bot限额查询异常: {e}，放行发送")

            result = await _original_call_api(self, api, **data)
            await record_send_stats(
                bot_id, plugin, group_id, count_images(data.get("message", ""))
            )
            logger.info(f"[Route] 非受管插件 '{plugin}' 由主Bot {bot_id} 发送")
            return result

        target_bot = None
        try:
            target_bot = await router.select_bot(plugin=plugin, group_id=group_id)
        except Exception as e:
            logger.warning(f"[Route] 路由选择异常: {e}")

        if target_bot is None:
            logger.warning(
                f"[Route] 受管插件 '{plugin}' 无可用Bot(竞态?)，"
                f"由当前Bot {bot_id} 兜底发送"
            )
            target_bot = self

        if target_bot != self:
            logger.info(
                f"[Route] 受管插件 '{plugin}' 发送路由: {self.self_id} → {target_bot.self_id}"
            )

        try:
            result = await _original_call_api(target_bot, api, **data)
            actual_bot_id = str(target_bot.self_id)
        except ActionFailed as e:
            failed_id = str(target_bot.self_id)
            logger.warning(
                f"[Route] Bot {failed_id} 发送失败(ActionFailed): {e}，"
                f"尝试 fallback 到主Bot"
            )
            if group_id:
                router.invalidate_group_cache(failed_id, group_id)

            all_bots = get_original_get_bots()
            primary = await router.get_primary_bot_if_in_group(group_id, all_bots)
            if primary is None:
                logger.warning(
                    f"[Route] 主Bot不可用(不在群 {group_id} 或离线)，放弃发送"
                )
                raise

            if primary is target_bot:
                logger.warning(f"[Route] 主Bot {failed_id} 发送也失败，放弃")
                raise

            logger.info(
                f"[Route] Fallback: {failed_id} → {primary.self_id} 发送"
            )
            result = await _original_call_api(primary, api, **data)
            actual_bot_id = str(primary.self_id)

        await record_send_stats(
            actual_bot_id, plugin, group_id, count_images(data.get("message", ""))
        )
        return result

    Bot.call_api = _patched_call_api
    logger.info("[Route] call_api 补丁已应用")


def apply_get_bots_patch(config: RouteConfig):
    if not config.primary_bot or not config.secondary_bots:
        return

    driver = get_driver()
    driver_cls = driver.__class__
    original_bots_prop = driver_cls.bots

    @property  # type: ignore
    def _patched_bots_prop(self):
        all_bots = original_bots_prop.fget(self)  # type: ignore
        if _is_external_plugin_call():
            primary_id = config.primary_bot
            if primary_id and primary_id in all_bots:
                return {primary_id: all_bots[primary_id]}
        return all_bots

    driver_cls.bots = _patched_bots_prop

    def _patched_get_bots() -> dict[str, Bot]:
        return get_driver().bots

    nonebot.get_bots = _patched_get_bots

    logger.info("[Route] get_bots 补丁已应用（外部插件只能看到主Bot）")


def _is_external_plugin_call(max_depth: int = 15) -> bool:
    for name in _walk_plugin_stack(max_depth):
        return name != "zhenxun_bot_route2"
    return False
