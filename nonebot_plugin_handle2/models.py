from datetime import datetime

from tortoise import fields

from zhenxun.services.db_context import Model


class Handle2Record(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    user_id = fields.CharField(255, description="用户ID", index=True)
    is_win = fields.BooleanField(default=False, description="是否猜对")
    attempts = fields.IntField(default=0, description="猜测次数")
    created_at = fields.DatetimeField(
        auto_now_add=True, description="创建时间", index=True
    )

    class Meta:
        table = "handle2_game_record"
        table_description = "字形拆分猜成语游戏记录"
        indexes = [("user_id", "created_at")]

    @classmethod
    async def get_daily_count(cls, user_id: str) -> int:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return await cls.filter(user_id=user_id, created_at__gte=today).count()
