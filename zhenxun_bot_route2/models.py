from datetime import date
from typing import ClassVar

from tortoise import fields

from zhenxun.services.db_context import Model


class _DailyStatsBase(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    bot_id = fields.CharField(255)
    stat_date = fields.DateField()
    send_count = fields.IntField(default=0)
    last_updated = fields.DatetimeField(auto_now=True)

    class Meta:
        abstract = True

    @classmethod
    async def _get_or_create_today(cls, bot_id: str, **dimensions) -> "_DailyStatsBase":
        filters = {"bot_id": bot_id, "stat_date": date.today(), **dimensions}
        stats = await cls.filter(**filters).first()
        if not stats:
            defaults = {"send_count": 0}
            defaults.update(dimensions)
            stats = await cls.create(bot_id=bot_id, stat_date=date.today(), **defaults)
        return stats

    @classmethod
    async def increment_count(cls, bot_id: str, **dimensions) -> int:
        stats = await cls._get_or_create_today(bot_id, **dimensions)
        stats.send_count += 1
        await stats.save(update_fields=["send_count"])
        return stats.send_count

    @classmethod
    async def get_count(cls, bot_id: str, **dimensions) -> int:
        return (await cls._get_or_create_today(bot_id, **dimensions)).send_count


class BotDailyStats(_DailyStatsBase):
    image_count = fields.IntField(default=0)
    alert_sent = fields.BooleanField(default=False)

    class Meta:
        table = "bot_daily_stats"
        table_description = "Bot每日发送统计"
        indexes: ClassVar = [("bot_id", "stat_date")]

    @classmethod
    def _run_script(cls):
        return ["ALTER TABLE bot_daily_stats ADD image_count INTEGER DEFAULT 0;"]

    @classmethod
    async def _get_or_create_today(cls, bot_id: str, **_kwargs) -> "BotDailyStats":
        stats = await cls.filter(bot_id=bot_id, stat_date=date.today()).first()
        if not stats:
            stats = await cls.create(
                bot_id=bot_id,
                stat_date=date.today(),
                send_count=0,
                image_count=0,
                alert_sent=False,
            )
        return stats

    @classmethod
    async def increment_count(cls, bot_id: str, image_count: int = 0, **_kwargs) -> int:
        stats = await cls._get_or_create_today(bot_id)
        stats.send_count += 1
        stats.image_count += image_count
        await stats.save(update_fields=["send_count", "image_count"])
        return stats.send_count

    @classmethod
    async def get_stats(cls, bot_id: str) -> dict:
        stats = await cls._get_or_create_today(bot_id)
        return {"send_count": stats.send_count, "image_count": stats.image_count}

    @classmethod
    async def should_alert(cls, bot_id: str) -> bool:
        stats = await cls._get_or_create_today(bot_id)
        if stats.alert_sent:
            return False
        stats.alert_sent = True
        await stats.save(update_fields=["alert_sent"])
        return True

    @classmethod
    async def is_full(cls, bot_id: str, daily_limit: int) -> bool:
        stats = await cls._get_or_create_today(bot_id)
        return stats.send_count >= daily_limit

    @classmethod
    async def get_remaining(cls, bot_id: str, daily_limit: int) -> int:
        stats = await cls._get_or_create_today(bot_id)
        return max(0, daily_limit - stats.send_count)


class BotPluginDailyStats(_DailyStatsBase):
    plugin = fields.CharField(255)

    class Meta:
        table = "bot_plugin_daily_stats"
        table_description = "Bot插件维度每日发送统计"
        indexes: ClassVar = [("bot_id", "stat_date", "plugin")]

    @classmethod
    async def _get_or_create_today(cls, bot_id: str, **kwargs) -> "BotPluginDailyStats":
        plugin = kwargs.get("plugin", "")
        stats = await cls.filter(
            bot_id=bot_id, stat_date=date.today(), plugin=plugin
        ).first()
        if not stats:
            stats = await cls.create(
                bot_id=bot_id,
                stat_date=date.today(),
                plugin=plugin,
                send_count=0,
            )
        return stats


class BotGroupDailyStats(_DailyStatsBase):
    group_id = fields.CharField(255)

    class Meta:
        table = "bot_group_daily_stats"
        table_description = "Bot群号维度每日发送统计"
        indexes: ClassVar = [("bot_id", "stat_date", "group_id")]

    @classmethod
    async def _get_or_create_today(cls, bot_id: str, **kwargs) -> "BotGroupDailyStats":
        group_id = kwargs.get("group_id", "")
        stats = await cls.filter(
            bot_id=bot_id, stat_date=date.today(), group_id=group_id
        ).first()
        if not stats:
            stats = await cls.create(
                bot_id=bot_id,
                stat_date=date.today(),
                group_id=group_id,
                send_count=0,
            )
        return stats


async def record_send_stats(
    bot_id: str, plugin: str | None, group_id: str | None, image_count: int = 0
):
    from zhenxun.services.log import logger

    try:
        await BotDailyStats.increment_count(bot_id, image_count=image_count)
    except Exception as e:
        logger.debug(f"[Route] 总统计失败: {e}")

    if plugin:
        try:
            await BotPluginDailyStats.increment_count(bot_id, plugin=plugin)
        except Exception as e:
            logger.debug(f"[Route] 插件统计失败: {e}")

    if group_id:
        try:
            await BotGroupDailyStats.increment_count(bot_id, group_id=group_id)
        except Exception as e:
            logger.debug(f"[Route] 群统计失败: {e}")
