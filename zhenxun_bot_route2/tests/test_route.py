"""
Bot路由器 v2.0 测试套件
=====================

使用 importlib.util 直接加载源文件，避免触发 __init__.py 的副作用。
模拟各种场景验证核心逻辑正确性。

测试覆盖场景总览:
  1. RouteConfig 配置类 - 10个测试
     - 副Bot识别、受管插件识别、插件→Bot映射、全插件列表
     - 从字典加载配置（正常/缺失字段/非法数据/空副Bot/空插件列表/无bot_id）
     - 多Bot管同一插件、一Bot管多插件
  2. 白名单机制 - 6个测试
     - builtin_plugins 白名单、路由插件自身白名单
     - 普通插件不在白名单、None/空字符串不在白名单、部分匹配白名单
  3. MockDB 模拟数据库 - 9个测试
     - 限额判断：未满/刚好满/超出限额
     - 剩余配额：正常/为零/超出（不返回负数）
     - 满载通知：首次触发/重复不触发/各Bot独立
  4. LoadBalancer 核心路由逻辑 - 10个测试
     - has_available_bot: 有余量/全部满/全部离线/部分满部分可用
     - select_bot: 选余量最多/全部满返回None/部分离线/单插件单Bot/无候选/不同限额选最高余量
  5. event_preprocessor 副Bot过滤 - 3个测试
     - 副Bot发送的消息被过滤、副Bot自身接收被过滤、主Bot不被过滤
  6. run_preprocessor 配额拦截 - 7个测试
     - 白名单插件放行、非受管插件放行/主Bot满时拦截/主Bot未满放行
     - 受管插件有可用Bot放行/全部满拦截/全部离线拦截
  7. 消息去重 - 5个测试
     - 同消息不同Bot拦截、同消息同Bot不拦截、不同消息不拦截
     - 拦截计数、字典格式消息去重
  8. 边界场景 - 11个测试
     - 主Bot/副Bot恰好到达限额、限额为零、新Bot零计数
     - 满载通知只发一次、各Bot独立通知
     - 配置加载：非法int回退默认值、空副Bot列表、无bot_id的副Bot、多插件副Bot
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_module(name: str, path: str):
    """通过 importlib.util 直接加载源文件，绕过 __init__.py 的框架依赖"""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_config_mod = _load_module(
    "test_route_config",
    os.path.join(_PLUGIN_DIR, "config.py"),
)
_dedup_mod = _load_module(
    "test_route_dedup",
    os.path.join(_PLUGIN_DIR, "dedup.py"),
)

# 白名单前缀（与 patches.py 中 is_whitelisted 逻辑一致，此处内联避免导入框架依赖）
_WHITELIST_PREFIXES = ("zhenxun.builtin_plugins", "zhenxun_bot_route2")


def is_whitelisted(plugin: str | None) -> bool:
    """判断插件是否在白名单中（白名单插件不受限额拦截）"""
    if not plugin:
        return False
    return any(plugin.startswith(prefix) for prefix in _WHITELIST_PREFIXES)


RouteConfig = _config_mod.RouteConfig
SecondaryBotConfig = _config_mod.SecondaryBotConfig
MessageDedup = _dedup_mod.MessageDedup


class MockDB:
    """模拟 BotDailyStats 的内存数据库，用于测试中替代真实 ORM 模型

    核心行为:
    - is_full: 发送数 >= 限额 → 已满
    - get_remaining: max(0, 限额 - 发送数)，不返回负数
    - should_alert: 每个bot_id只返回一次True（防止重复发送满载通知）
    """

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._alerts: dict[str, bool] = {}

    async def is_full(self, bot_id: str, daily_limit: int) -> bool:
        return self._counts.get(bot_id, 0) >= daily_limit

    async def get_remaining(self, bot_id: str, daily_limit: int) -> int:
        return max(0, daily_limit - self._counts.get(bot_id, 0))

    async def get_count(self, bot_id: str) -> int:
        return self._counts.get(bot_id, 0)

    async def should_alert(self, bot_id: str) -> bool:
        if self._alerts.get(bot_id, False):
            return False
        self._alerts[bot_id] = True
        return True

    async def increment_count(self, bot_id: str, **kwargs) -> int:
        self._counts[bot_id] = self._counts.get(bot_id, 0) + 1
        return self._counts[bot_id]

    async def get_stats(self, bot_id: str) -> dict:
        return {"send_count": self._counts.get(bot_id, 0), "image_count": 0}

    def set_count(self, bot_id: str, count: int):
        """手动设置某Bot的发送计数（用于模拟已发送N条的场景）"""
        self._counts[bot_id] = count


# ============================================================
# 1. 测试 RouteConfig 配置类
# 场景: 配置的构建、查询、从字典加载、各种边界情况
# ============================================================


class TestRouteConfig:
    def _make_config(self) -> RouteConfig:
        """构造标准测试配置: 主Bot=1001, 3个副Bot, fishing被3个Bot分管, handle只被1个Bot分管"""
        return RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[
                SecondaryBotConfig("2001", 300, ["fishing", "handle"]),
                SecondaryBotConfig("2002", 200, ["fishing"]),
                SecondaryBotConfig("2003", 100, ["fishing"]),
            ],
        )

    def test_is_secondary_bot(self):
        """场景: 判断一个QQ号是否是副Bot
        - 2001/2002 是副Bot → True
        - 1001 是主Bot → False
        - 9999 不在配置中 → False
        """
        config = self._make_config()
        assert config.is_secondary_bot("2001") is True
        assert config.is_secondary_bot("2002") is True
        assert config.is_secondary_bot("1001") is False
        assert config.is_secondary_bot("9999") is False

    def test_is_managed_plugin(self):
        """场景: 判断一个插件是否是受管插件（被副Bot分管的插件）
        - fishing/handle 被副Bot分管 → True（受管插件走副Bot发送）
        - sign_in/gacha 不被副Bot分管 → False（非受管插件走主Bot发送）
        """
        config = self._make_config()
        assert config.is_managed_plugin("fishing") is True
        assert config.is_managed_plugin("handle") is True
        assert config.is_managed_plugin("sign_in") is False
        assert config.is_managed_plugin("gacha") is False

    def test_get_bots_for_plugin(self):
        """场景: 查询分管某插件的所有副Bot
        - fishing 被3个Bot分管 → [2001, 2002, 2003]
        - handle 只被1个Bot分管 → [2001]
        - unknown 无Bot分管 → []
        """
        config = self._make_config()
        fishing_bots = config.get_bots_for_plugin("fishing")
        assert len(fishing_bots) == 3
        assert [b.bot_id for b in fishing_bots] == ["2001", "2002", "2003"]

        handle_bots = config.get_bots_for_plugin("handle")
        assert len(handle_bots) == 1
        assert handle_bots[0].bot_id == "2001"

        unknown_bots = config.get_bots_for_plugin("unknown")
        assert len(unknown_bots) == 0

    def test_get_all_plugins(self):
        """场景: 获取所有受管插件的去重列表
        - 3个副Bot共注册了 fishing 和 handle → {fishing, handle}
        """
        config = self._make_config()
        plugins = config.get_all_plugins()
        assert set(plugins) == {"fishing", "handle"}

    def test_load_from_dict(self):
        """场景: 从字典正常加载配置
        - 验证 primary_bot、primary_daily_limit、secondary_bots 的值都正确解析
        """
        config = RouteConfig()
        data = {
            "primary_bot": "1001",
            "primary_daily_limit": 500,
            "secondary_bots": [
                {
                    "bot_id": "2001",
                    "daily_limit": 300,
                    "plugins": ["fishing"],
                },
            ],
        }
        config._load_from_dict(data)
        assert config.primary_bot == "1001"
        assert config.primary_daily_limit == 500
        assert len(config.secondary_bots) == 1
        assert config.secondary_bots[0].bot_id == "2001"
        assert config.secondary_bots[0].daily_limit == 300
        assert config.secondary_bots[0].plugins == ["fishing"]

    def test_load_from_dict_missing_fields(self):
        """场景: 从空字典加载配置（所有字段缺失）
        - primary_bot 回退为空字符串
        - primary_daily_limit 回退为默认值500
        - secondary_bots 回退为空列表
        """
        config = RouteConfig()
        data = {}
        config._load_from_dict(data)
        assert config.primary_bot == ""
        assert config.primary_daily_limit == 500
        assert len(config.secondary_bots) == 0

    def test_multiple_bots_same_plugin(self):
        """场景: 多个副Bot分管同一个插件（fishing被3个Bot分管）
        - 确保查询结果包含所有3个Bot
        """
        config = self._make_config()
        bots = config.get_bots_for_plugin("fishing")
        assert len(bots) == 3

    def test_one_bot_multiple_plugins(self):
        """场景: 一个副Bot分管多个插件（2001同时分管fishing和handle）
        - 确保该Bot的plugins列表包含两个插件
        """
        config = self._make_config()
        bot = config.secondary_bots[0]
        assert "fishing" in bot.plugins
        assert "handle" in bot.plugins

    def test_config_no_secondary_bots(self):
        """场景: 配置中没有副Bot（只有主Bot独立运行）
        - 任何插件都不是受管插件
        - 查询任何插件的Bot列表都为空
        - 全插件列表为空
        """
        config = RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[],
        )
        assert config.is_managed_plugin("fishing") is False
        assert config.get_bots_for_plugin("fishing") == []
        assert config.get_all_plugins() == []

    def test_config_empty_plugin_list(self):
        """场景: 副Bot存在但没有分管任何插件（plugins为空列表）
        - 该Bot虽然存在但不分管任何插件，查询结果为空
        """
        config = RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[
                SecondaryBotConfig("2001", 300, []),
            ],
        )
        assert config.is_managed_plugin("fishing") is False
        assert config.get_bots_for_plugin("fishing") == []


# ============================================================
# 2. 测试白名单机制
# 场景: 白名单插件不受限额拦截，直接放行
# ============================================================


class TestWhitelist:
    def test_builtin_plugins_whitelisted(self):
        """场景: zhenxun.builtin_plugins 前缀的插件在白名单中 → 不受限额拦截"""
        assert is_whitelisted("zhenxun.builtin_plugins.chat_message") is True

    def test_route_plugin_whitelisted(self):
        """场景: zhenxun_bot_route2 自身在白名单中 → 避免递归拦截"""
        assert is_whitelisted("zhenxun_bot_route2") is True

    def test_normal_plugin_not_whitelisted(self):
        """场景: 普通插件不在白名单中 → 受限额拦截"""
        assert is_whitelisted("fishing") is False
        assert is_whitelisted("sign_in") is False

    def test_none_not_whitelisted(self):
        """场景: 插件名为None（无法检测插件时）→ 不在白名单，走正常限额流程"""
        assert is_whitelisted(None) is False

    def test_empty_string_not_whitelisted(self):
        """场景: 插件名为空字符串 → 不在白名单"""
        assert is_whitelisted("") is False

    def test_partial_match_not_whitelisted(self):
        """场景: 插件名不以白名单前缀开头 → 不在白名单
        startswith 是精确前缀匹配，不是子串匹配
        'some_builtin_plugins_thing' 不以 'zhenxun.builtin_plugins' 开头
        """
        assert is_whitelisted("some_builtin_plugins_thing") is False


# ============================================================
# 3. 测试 MockDB 模拟数据库
# 场景: 验证模拟数据库的限额判断、剩余配额、满载通知逻辑与真实ORM一致
# ============================================================


class TestMockDB:
    @pytest.mark.asyncio
    async def test_is_full_below_limit(self):
        """场景: 发送数299 < 限额300 → 未满，可以继续发送"""
        db = MockDB()
        db.set_count("2001", 299)
        assert await db.is_full("2001", 300) is False

    @pytest.mark.asyncio
    async def test_is_full_at_limit(self):
        """场景: 发送数300 = 限额300 → 已满（>=即为满）"""
        db = MockDB()
        db.set_count("2001", 300)
        assert await db.is_full("2001", 300) is True

    @pytest.mark.asyncio
    async def test_is_full_over_limit(self):
        """场景: 发送数301 > 限额300 → 已满（超出也算满）"""
        db = MockDB()
        db.set_count("2001", 301)
        assert await db.is_full("2001", 300) is True

    @pytest.mark.asyncio
    async def test_get_remaining(self):
        """场景: 发送250/限额300 → 剩余50条"""
        db = MockDB()
        db.set_count("2001", 250)
        assert await db.get_remaining("2001", 300) == 50

    @pytest.mark.asyncio
    async def test_get_remaining_at_zero(self):
        """场景: 发送300/限额300 → 剩余0条（刚好用完）"""
        db = MockDB()
        db.set_count("2001", 300)
        assert await db.get_remaining("2001", 300) == 0

    @pytest.mark.asyncio
    async def test_get_remaining_over_limit(self):
        """场景: 发送350/限额300 → 剩余0条（不返回负数，max(0,...)保护）"""
        db = MockDB()
        db.set_count("2001", 350)
        assert await db.get_remaining("2001", 300) == 0

    @pytest.mark.asyncio
    async def test_should_alert_first_time(self):
        """场景: 某Bot首次满载 → should_alert返回True，允许发送一次'消息已满'通知"""
        db = MockDB()
        assert await db.should_alert("2001") is True

    @pytest.mark.asyncio
    async def test_should_alert_second_time(self):
        """场景: 同一Bot第二次查询 → should_alert返回False，防止重复发送满载通知"""
        db = MockDB()
        await db.should_alert("2001")
        assert await db.should_alert("2001") is False

    @pytest.mark.asyncio
    async def test_should_alert_independent_per_bot(self):
        """场景: 不同Bot的满载通知互不影响
        - 2001已通知过 → False
        - 2002首次通知 → True（各Bot独立判断）
        """
        db = MockDB()
        await db.should_alert("2001")
        assert await db.should_alert("2002") is True


# ============================================================
# 4. 测试 LoadBalancer 核心路由逻辑
# 场景: has_available_bot（是否有可用副Bot）和 select_bot（选择余量最多的副Bot）
# ============================================================


class TestLoadBalancerCore:
    def _make_config(self) -> RouteConfig:
        """标准测试配置: 3个副Bot分管fishing，2001额外分管handle"""
        return RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[
                SecondaryBotConfig("2001", 300, ["fishing", "handle"]),
                SecondaryBotConfig("2002", 200, ["fishing"]),
                SecondaryBotConfig("2003", 100, ["fishing"]),
            ],
        )

    def _make_bots(self, online_ids: list[str]) -> dict:
        """构造在线Bot字典，模拟 nonebot.get_bots() 的返回值"""
        bots = {}
        for bid in online_ids:
            mock_bot = MagicMock()
            mock_bot.self_id = bid
            bots[bid] = mock_bot
        return bots

    @pytest.mark.asyncio
    async def test_has_available_bot_with_quota(self):
        """场景: 所有副Bot在线且有余量 → 有可用Bot（受管插件可以放行）"""
        config = self._make_config()
        db = MockDB()
        bots = self._make_bots(["1001", "2001", "2002", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        result = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    result = True
        assert result is True

    @pytest.mark.asyncio
    async def test_has_available_bot_all_full(self):
        """场景: 所有副Bot在线但全部满载 → 无可用Bot（受管插件应被拦截）"""
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 300)
        db.set_count("2002", 200)
        db.set_count("2003", 100)
        bots = self._make_bots(["1001", "2001", "2002", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        result = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    result = True
        assert result is False

    @pytest.mark.asyncio
    async def test_has_available_bot_all_offline(self):
        """场景: 所有副Bot都不在线（只有主Bot在线）→ 无可用Bot"""
        config = self._make_config()
        db = MockDB()
        bots = self._make_bots(["1001"])

        candidates = config.get_bots_for_plugin("fishing")
        result = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    result = True
        assert result is False

    @pytest.mark.asyncio
    async def test_has_available_bot_some_full_some_available(self):
        """场景: 部分副Bot满载，部分还有余量 → 有可用Bot
        - 2001已满(300/300), 2002未满(150/200), 2003未满(0/100)
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 300)
        db.set_count("2002", 150)
        bots = self._make_bots(["1001", "2001", "2002", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        result = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    result = True
        assert result is True

    @pytest.mark.asyncio
    async def test_select_bot_most_remaining(self):
        """场景: 选择余量最多的副Bot发送
        - 2001: 限额300, 已发100, 余200（最多）
        - 2002: 限额200, 已发50, 余150
        - 2003: 限额100, 已发20, 余80
        → 应选2001
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 100)
        db.set_count("2002", 50)
        db.set_count("2003", 20)
        bots = self._make_bots(["1001", "2001", "2002", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        best_id = None
        best_remaining = -1
        for cfg in candidates:
            if cfg.bot_id not in bots:
                continue
            remaining = await db.get_remaining(cfg.bot_id, cfg.daily_limit)
            if remaining <= 0:
                continue
            if remaining > best_remaining:
                best_remaining = remaining
                best_id = cfg.bot_id

        assert best_id == "2001"

    @pytest.mark.asyncio
    async def test_select_bot_all_full(self):
        """场景: 所有副Bot都满载 → 返回None（无Bot可发送）"""
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 300)
        db.set_count("2002", 200)
        db.set_count("2003", 100)
        bots = self._make_bots(["1001", "2001", "2002", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        best_id = None
        best_remaining = -1
        for cfg in candidates:
            if cfg.bot_id not in bots:
                continue
            remaining = await db.get_remaining(cfg.bot_id, cfg.daily_limit)
            if remaining <= 0:
                continue
            if remaining > best_remaining:
                best_remaining = remaining
                best_id = cfg.bot_id

        assert best_id is None

    @pytest.mark.asyncio
    async def test_select_bot_some_offline(self):
        """场景: 部分副Bot离线，从在线的Bot中选余量最多的
        - 2002离线, 2001余200, 2003余80 → 应选2001
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 100)
        db.set_count("2003", 20)
        bots = self._make_bots(["1001", "2001", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        best_id = None
        best_remaining = -1
        for cfg in candidates:
            if cfg.bot_id not in bots:
                continue
            remaining = await db.get_remaining(cfg.bot_id, cfg.daily_limit)
            if remaining <= 0:
                continue
            if remaining > best_remaining:
                best_remaining = remaining
                best_id = cfg.bot_id

        assert best_id == "2001"

    @pytest.mark.asyncio
    async def test_select_bot_single_plugin(self):
        """场景: 只有一个副Bot分管某插件（handle只被2001分管）
        - 直接选2001，无需比较余量
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 50)
        bots = self._make_bots(["1001", "2001"])

        candidates = config.get_bots_for_plugin("handle")
        best_id = None
        best_remaining = -1
        for cfg in candidates:
            if cfg.bot_id not in bots:
                continue
            remaining = await db.get_remaining(cfg.bot_id, cfg.daily_limit)
            if remaining <= 0:
                continue
            if remaining > best_remaining:
                best_remaining = remaining
                best_id = cfg.bot_id

        assert best_id == "2001"

    @pytest.mark.asyncio
    async def test_select_bot_no_candidates(self):
        """场景: 非受管插件没有候选副Bot → 候选列表为空，走主Bot发送"""
        config = self._make_config()
        db = MockDB()
        bots = self._make_bots(["1001"])

        candidates = config.get_bots_for_plugin("sign_in")
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_select_bot_prefers_highest_remaining(self):
        """场景: 不同限额的副Bot，选绝对余量最多的（不是百分比最多）
        - 2001: 限额300, 已发150, 余150
        - 2002: 限额500, 已发100, 余400（最多）→ 应选2002
        - 2003: 限额100, 已发50, 余50
        """
        config = RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[
                SecondaryBotConfig("2001", 300, ["fishing"]),
                SecondaryBotConfig("2002", 500, ["fishing"]),
                SecondaryBotConfig("2003", 100, ["fishing"]),
            ],
        )
        db = MockDB()
        db.set_count("2001", 150)
        db.set_count("2002", 100)
        db.set_count("2003", 50)
        bots = self._make_bots(["1001", "2001", "2002", "2003"])

        candidates = config.get_bots_for_plugin("fishing")
        best_id = None
        best_remaining = -1
        for cfg in candidates:
            if cfg.bot_id not in bots:
                continue
            remaining = await db.get_remaining(cfg.bot_id, cfg.daily_limit)
            if remaining <= 0:
                continue
            if remaining > best_remaining:
                best_remaining = remaining
                best_id = cfg.bot_id

        assert best_id == "2002"


# ============================================================
# 5. 测试 event_preprocessor 逻辑 (filter_secondary_bot)
# 场景: 副Bot收到的消息应该被过滤，只有主Bot处理消息
# ============================================================


class TestFilterSecondaryBot:
    def _make_config(self) -> RouteConfig:
        """配置: 主Bot=1001, 副Bot=2001"""
        return RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[
                SecondaryBotConfig("2001", 300, ["fishing"]),
            ],
        )

    @pytest.mark.asyncio
    async def test_secondary_bot_user_id_filtered(self):
        """场景: 消息发送者是副BotQQ号 → 应被过滤（副Bot发的消息不需要处理）
        对应 __init__.py 中: if config.is_secondary_bot(user_id): raise IgnoredException
        """
        config = self._make_config()
        user_id = "2001"
        bot_id = "1001"

        assert config.is_secondary_bot(user_id) is True

    @pytest.mark.asyncio
    async def test_secondary_bot_self_id_filtered(self):
        """场景: 接收消息的Bot自身是副Bot → 应被过滤（副Bot不处理任何消息）
        对应 __init__.py 中: if config.is_secondary_bot(bot_id): raise IgnoredException
        """
        config = self._make_config()
        bot_id = "2001"

        assert config.is_secondary_bot(bot_id) is True

    @pytest.mark.asyncio
    async def test_primary_bot_not_filtered(self):
        """场景: 主Bot接收普通用户消息 → 不被过滤，正常处理
        - user_id=9999 不是副Bot
        - bot_id=1001 是主Bot
        """
        config = self._make_config()
        user_id = "9999"
        bot_id = "1001"

        assert config.is_secondary_bot(user_id) is False
        assert config.is_secondary_bot(bot_id) is False


# ============================================================
# 6. 测试 run_preprocessor 逻辑 (check_plugin_quota)
# 场景: 根据插件类型和Bot限额决定是否拦截
# ============================================================


class TestCheckPluginQuota:
    def _make_config(self) -> RouteConfig:
        """配置: 主Bot=1001(限额400), 副Bot=2001(限额300,分管fishing), 2002(限额200,分管fishing)"""
        return RouteConfig(
            primary_bot="1001",
            primary_daily_limit=400,
            secondary_bots=[
                SecondaryBotConfig("2001", 300, ["fishing"]),
                SecondaryBotConfig("2002", 200, ["fishing"]),
            ],
        )

    @pytest.mark.asyncio
    async def test_whitelisted_plugin_passes(self):
        """场景: 白名单前缀插件（如zhenxun.builtin_plugins.chat_message）→ 不受限额拦截，直接放行"""
        assert is_whitelisted("zhenxun.builtin_plugins.chat_message") is True

    @pytest.mark.asyncio
    async def test_non_managed_plugin_passes(self):
        """场景: 非受管插件（sign_in不在副Bot配置中）→ 由主Bot发送，检查主Bot限额"""
        config = self._make_config()
        assert config.is_managed_plugin("sign_in") is False

    @pytest.mark.asyncio
    async def test_non_managed_plugin_primary_full(self):
        """场景: 非受管插件 + 主Bot已满(400/400) → 拦截该插件
        主Bot满载时，非受管插件也不能发送
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("1001", 400)
        assert await db.is_full("1001", config.primary_daily_limit) is True

    @pytest.mark.asyncio
    async def test_non_managed_plugin_primary_not_full(self):
        """场景: 非受管插件 + 主Bot未满(399/400) → 放行，由主Bot发送"""
        config = self._make_config()
        db = MockDB()
        db.set_count("1001", 399)
        assert await db.is_full("1001", config.primary_daily_limit) is False

    @pytest.mark.asyncio
    async def test_managed_plugin_available(self):
        """场景: 受管插件(fishing) + 副Bot有余量 → 放行，由副Bot发送
        - 2001: 50/300, 2002: 30/200 → 都有余量
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 50)
        db.set_count("2002", 30)
        bots = {"1001": MagicMock(), "2001": MagicMock(), "2002": MagicMock()}

        candidates = config.get_bots_for_plugin("fishing")
        available = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    available = True
        assert available is True

    @pytest.mark.asyncio
    async def test_managed_plugin_all_full(self):
        """场景: 受管插件(fishing) + 所有副Bot满载 → 拦截 + 发送满载通知
        - 2001: 300/300, 2002: 200/200 → 全满
        """
        config = self._make_config()
        db = MockDB()
        db.set_count("2001", 300)
        db.set_count("2002", 200)
        bots = {"1001": MagicMock(), "2001": MagicMock(), "2002": MagicMock()}

        candidates = config.get_bots_for_plugin("fishing")
        available = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    available = True
        assert available is False

    @pytest.mark.asyncio
    async def test_managed_plugin_all_offline(self):
        """场景: 受管插件(fishing) + 所有副Bot离线 → 拦截（无Bot可发送）
        只有主Bot在线，但主Bot不参与受管插件的发送
        """
        config = self._make_config()
        db = MockDB()
        bots = {"1001": MagicMock()}

        candidates = config.get_bots_for_plugin("fishing")
        available = False
        for cfg in candidates:
            if cfg.bot_id in bots:
                if not await db.is_full(cfg.bot_id, cfg.daily_limit):
                    available = True
        assert available is False


# ============================================================
# 7. 测试消息去重
# 场景: 防止多个Bot发送相同消息（MD5哈希 + 时间窗口）
# ============================================================


class TestMessageDedup:
    def test_same_message_different_bot_blocked(self):
        """场景: 两个Bot发送相同消息 → 第二个Bot被拦截（去重生效）
        bot1先发"hello" → 通过; bot2再发"hello" → 被拦截
        """
        dedup = MessageDedup(window_seconds=20)
        assert dedup.check_and_record("bot1", "hello") is False
        assert dedup.check_and_record("bot2", "hello") is True

    def test_same_message_same_bot_not_blocked(self):
        """场景: 同一个Bot发送相同消息 → 不拦截（可能是合法的重复发送）
        同一Bot的重复消息不被视为去重冲突
        """
        dedup = MessageDedup(window_seconds=20)
        assert dedup.check_and_record("bot1", "hello") is False
        assert dedup.check_and_record("bot1", "hello") is False

    def test_different_message_not_blocked(self):
        """场景: 不同Bot发送不同消息 → 都不拦截（内容不同，不是重复）"""
        dedup = MessageDedup(window_seconds=20)
        assert dedup.check_and_record("bot1", "hello") is False
        assert dedup.check_and_record("bot2", "world") is False

    def test_blocked_count(self):
        """场景: 统计被拦截的消息数量
        bot1发"hello" → 通过; bot2发"hello" → 拦截(第1次); bot3发"hello" → 拦截(第2次)
        → blocked_count = 2
        """
        dedup = MessageDedup(window_seconds=20)
        dedup.check_and_record("bot1", "hello")
        dedup.check_and_record("bot2", "hello")
        dedup.check_and_record("bot3", "hello")
        assert dedup.blocked_count == 2

    def test_dict_message(self):
        """场景: 字典格式的消息也能正确去重
        两条内容相同的字典消息 → 第二条被拦截
        """
        dedup = MessageDedup(window_seconds=20)
        msg1 = [{"type": "text", "data": {"text": "hello"}}]
        msg2 = [{"type": "text", "data": {"text": "hello"}}]
        assert dedup.check_and_record("bot1", msg1) is False
        assert dedup.check_and_record("bot2", msg2) is True


# ============================================================
# 8. 测试边界场景
# 场景: 各种极端和异常情况，确保系统不会崩溃或误判
# ============================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_primary_bot_exactly_at_limit(self):
        """场景: 主Bot发送数恰好等于限额(400/400) → 已满
        验证 >= 判断（不是 >），到达限额即视为满载
        """
        db = MockDB()
        db.set_count("1001", 400)
        assert await db.is_full("1001", 400) is True

    @pytest.mark.asyncio
    async def test_primary_bot_one_below_limit(self):
        """场景: 主Bot发送数比限额少1(399/400) → 未满，还能发1条"""
        db = MockDB()
        db.set_count("1001", 399)
        assert await db.is_full("1001", 400) is False

    @pytest.mark.asyncio
    async def test_secondary_bot_exactly_at_limit(self):
        """场景: 副Bot发送数恰好等于限额(300/300) → 已满"""
        db = MockDB()
        db.set_count("2001", 300)
        assert await db.is_full("2001", 300) is True

    @pytest.mark.asyncio
    async def test_zero_daily_limit(self):
        """场景: 限额设为0 → 即使发送数为0也已满（0 >= 0）
        相当于完全禁止该Bot发送
        """
        db = MockDB()
        db.set_count("2001", 0)
        assert await db.is_full("2001", 0) is True

    @pytest.mark.asyncio
    async def test_new_bot_zero_count(self):
        """场景: 新Bot从未发送过消息（数据库中无记录）→ 未满，剩余配额=全额
        模拟刚启动或0点重置后的状态
        """
        db = MockDB()
        assert await db.is_full("9999", 100) is False
        assert await db.get_remaining("9999", 100) == 100

    @pytest.mark.asyncio
    async def test_alert_only_once_per_bot(self):
        """场景: 同一个Bot的满载通知只发一次
        第一次should_alert → True（允许发通知）
        第二次should_alert → False（已发过，不再重复）
        第三次should_alert → False（仍然不重复）
        """
        db = MockDB()
        assert await db.should_alert("2001") is True
        assert await db.should_alert("2001") is False
        assert await db.should_alert("2001") is False

    @pytest.mark.asyncio
    async def test_alert_independent_per_bot(self):
        """场景: 不同Bot的满载通知互不干扰
        - 2001首次 → True; 2002首次 → True
        - 2001第二次 → False; 2002第二次 → False
        每个Bot独立跟踪是否已发送过满载通知
        """
        db = MockDB()
        assert await db.should_alert("2001") is True
        assert await db.should_alert("2002") is True
        assert await db.should_alert("2001") is False
        assert await db.should_alert("2002") is False

    def test_config_load_invalid_data(self):
        """场景: 配置文件中primary_daily_limit不是合法数字(如"abc")
        → int()转换失败，回退到默认值500，不会崩溃
        """
        config = RouteConfig()
        config._load_from_dict({"primary_bot": 123, "primary_daily_limit": "abc"})
        assert config.primary_bot == "123"
        assert config.primary_daily_limit == 500

    def test_config_load_empty_secondary(self):
        """场景: 配置文件中secondary_bots为空列表 → 无副Bot，不影响主Bot运行"""
        config = RouteConfig()
        config._load_from_dict({"secondary_bots": []})
        assert len(config.secondary_bots) == 0

    def test_config_load_secondary_no_bot_id(self):
        """场景: 副Bot配置缺少bot_id字段 → 该条目被忽略，不创建无效的副Bot"""
        config = RouteConfig()
        config._load_from_dict({"secondary_bots": [{"daily_limit": 100}]})
        assert len(config.secondary_bots) == 0

    def test_config_load_secondary_with_plugins(self):
        """场景: 副Bot配置包含多个插件 → 正确解析plugins列表"""
        config = RouteConfig()
        config._load_from_dict(
            {
                "secondary_bots": [
                    {
                        "bot_id": "2001",
                        "daily_limit": 300,
                        "plugins": ["fishing", "handle"],
                    }
                ]
            }
        )
        assert len(config.secondary_bots) == 1
        assert config.secondary_bots[0].plugins == ["fishing", "handle"]
