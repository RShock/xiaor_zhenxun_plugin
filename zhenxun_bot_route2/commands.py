import inspect
from datetime import date
from pathlib import Path

from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .models import BotDailyStats, BotGroupDailyStats, BotPluginDailyStats
from .patches import detect_plugin_from_stack, get_dedup, get_original_get_bots
from .render import render_status_page


def show_load(lb):
    async def _show_load(bot: Bot):
        config = lb.config
        bots = get_original_get_bots()

        primary_send, primary_images = 0, 0
        try:
            primary_stats = await BotDailyStats.get_stats(config.primary_bot)
            primary_send = primary_stats["send_count"]
            primary_images = primary_stats["image_count"]
        except Exception:
            pass

        primary_daily_limit = config.primary_daily_limit
        primary_remaining = (
            max(0, primary_daily_limit - primary_send) if primary_daily_limit else 0
        )
        primary_limit_pct = (
            (primary_send / primary_daily_limit * 100) if primary_daily_limit else 0
        )
        primary_online = config.primary_bot in bots

        primary_plugin_data = []
        for pl in config.primary_plugin_limits:
            try:
                plugin_count = await BotPluginDailyStats.get_count(
                    config.primary_bot, plugin=pl.plugin
                )
            except Exception:
                plugin_count = 0
            primary_plugin_data.append(
                {
                    "plugin": pl.plugin,
                    "send": plugin_count,
                    "daily_limit": pl.daily_limit,
                    "remaining": max(0, pl.daily_limit - plugin_count),
                    "limit_pct": min(
                        (plugin_count / pl.daily_limit * 100) if pl.daily_limit else 0,
                        100,
                    ),
                }
            )

        secondary_bots_data = []
        for sb in config.secondary_bots:
            try:
                stats = await BotDailyStats.get_stats(sb.bot_id)
                sb_send = stats["send_count"]
            except Exception:
                sb_send = 0
            sb_remaining = max(0, sb.daily_limit - sb_send)
            sb_limit_pct = (sb_send / sb.daily_limit * 100) if sb.daily_limit else 0

            secondary_bots_data.append(
                {
                    "bot_id": sb.bot_id,
                    "online": sb.bot_id in bots,
                    "send": sb_send,
                    "daily_limit": sb.daily_limit,
                    "remaining": sb_remaining,
                    "limit_pct": min(sb_limit_pct, 100),
                    "plugins": sb.plugins,
                }
            )

        dedup = get_dedup()
        dedup_info = None
        if dedup:
            dedup_info = {"blocked": dedup.blocked_count, "cached": dedup.entry_count}

        data = {
            "primary_bot": config.primary_bot,
            "primary_online": primary_online,
            "primary_send": primary_send,
            "primary_images": primary_images,
            "primary_daily_limit": primary_daily_limit,
            "primary_remaining": primary_remaining,
            "primary_limit_pct": min(primary_limit_pct, 100),
            "primary_plugin_limits": primary_plugin_data,
            "primary_plugins": config.primary_plugins,
            "secondary_bots": secondary_bots_data,
            "all_plugins": config.get_all_plugins(),
            "dedup_enabled": dedup is not None,
            "dedup_info": dedup_info,
            "intercept_superusers": lb.intercept_superusers,
            "body_bg": "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
            "width": 450,
        }

        img_bytes = await render_status_page(data)
        return MessageSegment.image(img_bytes)

    return _show_load


def show_status(lb):
    async def _show_status(bot: Bot):
        return await show_load(lb)(bot)

    return _show_status


def show_plugins(lb):
    async def _show_plugins(bot: Bot) -> str:
        config = lb.config
        bots = get_original_get_bots()
        all_plugins = config.get_all_plugins()

        if not all_plugins:
            return "暂无副Bot分管插件"

        lines = ["=== 插件分配详情 ===", ""]

        for plugin in all_plugins:
            candidates = config.get_bots_for_plugin(plugin)
            lines.append(f"[{plugin}]")
            for cfg in candidates:
                try:
                    stats = await BotDailyStats.get_stats(cfg.bot_id)
                    remaining = max(0, cfg.daily_limit - stats["send_count"])
                    status = "在线" if cfg.bot_id in bots else "离线"
                    lines.append(
                        f"  -> {cfg.bot_id} [{status}] "
                        f"限额:{cfg.daily_limit} 剩余:{remaining}"
                    )
                except Exception as e:
                    lines.append(f"  -> {cfg.bot_id} (查询失败: {e})")
            lines.append("")

        return "\n".join(lines).rstrip()

    return _show_plugins


def show_config(lb, config_path):
    async def _show_config(bot: Bot) -> str:
        config = lb.config
        lines = [
            "=== 路由配置 ===",
            "",
            f"主Bot: {config.primary_bot}",
            f"主Bot限额: {config.primary_daily_limit}条/天",
            f"超级用户拦截: {'开启' if lb.intercept_superusers else '关闭'}",
            "",
            f"副Bot池 ({len(config.secondary_bots)}个):",
        ]
        for sb in config.secondary_bots:
            lines.append(f"  [{sb.bot_id}] 限额:{sb.daily_limit} 插件:{sb.plugins}")
        lines.extend(
            [
                "",
                f"所有副Bot插件: {config.get_all_plugins()}",
                f"配置文件: {config_path}",
            ]
        )
        return "\n".join(lines)

    return _show_config


def reload_config(lb):
    async def _reload_config(bot: Bot) -> str:
        try:
            lb.reload_config()
            return "配置已重载"
        except Exception as e:
            return f"重载失败: {e}"

    return _reload_config


def reset_stats():
    async def _reset_stats(bot: Bot) -> str:
        try:
            deleted = await BotDailyStats.filter(stat_date=date.today()).delete()
            try:
                await BotPluginDailyStats.filter(stat_date=date.today()).delete()
            except Exception:
                pass
            try:
                await BotGroupDailyStats.filter(stat_date=date.today()).delete()
            except Exception:
                pass
            return f"已重置今日统计（删除{deleted}条）"
        except Exception as e:
            return f"重置失败: {e}"

    return _reset_stats


def test_detection(lb):
    async def _test_detection(bot: Bot) -> str:
        plugin = detect_plugin_from_stack()

        if not plugin:
            return "未检测到插件"

        is_managed = lb.config.is_managed_plugin(plugin)
        target = await lb.select_bot(plugin=plugin)

        candidates = lb.config.get_bots_for_plugin(plugin)
        candidate_ids = [c.bot_id for c in candidates]

        lines = [
            f"检测到插件: {plugin}",
            f"类型: {'受管插件' if is_managed else '非受管插件'}",
            f"候选Bot: {candidate_ids if candidate_ids else '无'}",
            f"发送: {target.self_id if target else '主Bot'}",
            "",
            "调用栈:",
        ]

        for i, f in enumerate(inspect.stack(context=0)[1:6], 1):
            lines.append(f"  {i}. [{f.function}] {Path(f.filename).name}:{f.lineno}")

        return "\n".join(lines)

    return _test_detection


def toggle_intercept(lb):
    async def _toggle_intercept(bot: Bot) -> str:
        new_state = lb.toggle_intercept_superusers()
        if new_state:
            return "超级用户拦截模式已开启（超级用户也受限额约束）"
        else:
            return "超级用户拦截模式已关闭（超级用户绕过限额）"

    return _toggle_intercept


async def _show_help(bot: Bot) -> str:
    return """
Bot路由器 v2.0

架构:
  主Bot: 接收所有消息，处理所有插件
  副Bot: 不接收消息，仅发送分管插件的回复
  受管插件: 在副Bot配置中注册的插件，由余量最多的副Bot发送
  非受管插件: 主Bot发送，受主Bot限额约束
  主Bot不参与受管插件的发送
  副Bot满载时发送一次通知，随后今日不再发送
  0点自动重置
  白名单: builtin_plugins和zhenxun_bot_route2不受限额拦截
  超级用户: 默认绕过限额，可通过命令临时开启拦截

流程:
  用户消息 -> 主Bot处理 -> 检测插件
    -> 白名单插件 -> 直接放行
    -> 超级用户(拦截关闭) -> 直接放行
    -> 非受管插件 -> 主Bot发送(受主Bot限额约束)
    -> 受管插件 -> 选余量最多的副Bot发送
                    所有副Bot满/离线 -> 拦截+通知

命令:
  负载均衡 状态 - 查看配额(图片)
  负载均衡 插件 - 查看插件分配
  负载均衡 查看 - 查看配置
  负载均衡 重载 - 重载配置
  负载均衡 重置 - 重置统计
  负载均衡 测试 - 测试检测
  负载均衡 拦截 - 切换超级用户拦截模式
  负载均衡 帮助 - 查看帮助

配置: 插件目录下 config.yaml
""".strip()


def get_command_handlers(lb, config_path) -> dict:
    return {
        ("状态", "status"): show_status(lb),
        ("查看", "config"): show_config(lb, config_path),
        ("负载", "load"): show_load(lb),
        ("插件", "plugins"): show_plugins(lb),
        ("重载", "reload"): reload_config(lb),
        ("重置", "reset"): reset_stats(),
        ("测试", "test"): test_detection(lb),
        ("拦截", "intercept"): toggle_intercept(lb),
        ("帮助", "help"): _show_help,
    }
