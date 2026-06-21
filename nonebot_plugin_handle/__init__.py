import asyncio
from asyncio import TimerHandle
from typing import Annotated, Any

from nonebot import on_regex, require
from nonebot.adapters import Bot
from nonebot.matcher import Matcher
from nonebot.params import Depends, RegexDict
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot.utils import run_sync

require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")

from nonebot_plugin_alconna import At, Image, UniMessage, on_alconna
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.user_console import UserConsole

from .config import Config, handle_config
from .data_source import GuessResult, Handle
from .models import HandleRecord
from .utils import random_idiom

__plugin_meta__ = PluginMetadata(
    name="猜成语",
    description="汉字Wordle 猜成语",
    usage=(
        '发送"猜成语"开始游戏（默认精确模式）；\n'
        '发送"猜成语 模糊"开始模糊模式（不区分ü）；\n'
        '发送"猜成语简单"开始简单模式（显示声母韵母提示）；\n'
        '发送"猜成语简单 模糊"开始简单模糊模式；\n'
        "你有十次的机会猜一个四字词语；\n"
        "每次猜测后，汉字与拼音的颜色将会标识其与正确答案的区别；\n"
        "青色 表示其出现在答案中且在正确的位置；\n"
        "橙色 表示其出现在答案中但不在正确的位置；\n"
        "每个格子的 汉字、声母、韵母、声调 都会独立进行颜色的指示。\n"
        "当四个格子都为青色时，你便赢得了游戏！\n"
        '可发送"结束"结束游戏；可发送"提示"查看提示。\n'
        "每人每天只能发起3次猜成语游戏。"
    ),
    type="application",
    homepage="https://github.com/noneplugin/nonebot-plugin-handle",
    config=Config,
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna", "nonebot_plugin_uninfo"
    ),
)


DAILY_LIMIT = 3
GAME_TIMEOUT = 300

games: dict[str, Handle] = {}
timers: dict[str, TimerHandle] = {}
group_games: dict[str, set[str]] = {}
grab_timers: dict[str, TimerHandle] = {}
game_owner_real_ids: dict[str, str] = {}
grab_notified: set[str] = set()
game_records: dict[str, HandleRecord] = {}


def get_user_id(uninfo: Uninfo) -> str:
    return f"{uninfo.scope}_{uninfo.self_id}_{uninfo.scene_path}_{uninfo.user.id}"


def get_group_id(uninfo: Uninfo) -> str:
    return f"{uninfo.scope}_{uninfo.self_id}_{uninfo.scene_path}"


def calculate_gold_reward(is_win: bool, attempts: int, easy_mode: bool = False) -> int:
    if not is_win:
        return 0
    if attempts <= 3:
        base = 400
    elif attempts <= 5:
        base = 200
    elif attempts == 6:
        base = 160
    else:
        base = 100
    return base // 2 if easy_mode else base


UserId = Annotated[str, Depends(get_user_id)]


def game_is_running(user_id: UserId) -> bool:
    return user_id in games


def game_not_running(user_id: UserId) -> bool:
    return user_id not in games


matcher_handle = on_regex(
    r"^猜成语$",
    rule=game_not_running,
    block=True,
    priority=13,
)
matcher_handle_fuzzy = on_regex(
    r"^猜成语 模糊$",
    rule=game_not_running,
    block=True,
    priority=13,
)
matcher_handle_easy = on_regex(
    r"^猜成语简单$",
    rule=game_not_running,
    block=True,
    priority=13,
)
matcher_handle_easy_fuzzy = on_regex(
    r"^猜成语简单 模糊$",
    rule=game_not_running,
    block=True,
    priority=13,
)
matcher_hint = on_alconna(
    "handle_hint",
    aliases=("提示", "猜成语提示"),
    rule=game_is_running,
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_stop = on_alconna(
    "handle_stop",
    aliases=("结束", "结束游戏", "结束猜成语"),
    rule=game_is_running,
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_idiom = on_regex(
    r"^(?P<idiom>[\u4e00-\u9fa5]{4})$",
    rule=game_is_running,
    block=True,
    priority=14,
)
matcher_grab = on_regex(
    r"^抢答\s*(?P<idiom>[\u4e00-\u9fa5]{4})$",
    block=True,
    priority=14,
)


def stop_game(user_id: str):
    if timer := timers.pop(user_id, None):
        timer.cancel()
    if timer := grab_timers.pop(user_id, None):
        timer.cancel()
    games.pop(user_id, None)
    game_owner_real_ids.pop(user_id, None)
    game_records.pop(user_id, None)
    grab_notified.discard(user_id)
    for group_id, owner_ids in group_games.items():
        owner_ids.discard(user_id)


async def stop_game_timeout(matcher: Matcher, user_id: str):
    game = games.get(user_id, None)
    owner_real_id = game_owner_real_ids.get(user_id, "")
    record = game_records.get(user_id)
    stop_game(user_id)
    if game:
        msg = "猜成语超时，游戏结束。"
        if game.get_total_attempts() >= 1:
            msg += f"\n{game.result}"
        if record:
            record.is_win = False
            record.attempts = game.get_total_attempts()
            await record.save()
        if owner_real_id:
            await UniMessage(At("user", owner_real_id) + " " + msg).finish()
        else:
            await matcher.finish(msg)


def set_timeout(matcher: Matcher, user_id: str, timeout: float = GAME_TIMEOUT):
    if timer := timers.get(user_id, None):
        timer.cancel()
    loop = asyncio.get_running_loop()
    timer = loop.call_later(
        timeout, lambda: asyncio.ensure_future(stop_game_timeout(matcher, user_id))
    )
    timers[user_id] = timer


async def grab_time_notify(matcher: Matcher, user_id: str):
    game = games.get(user_id)
    if not game:
        return
    grab_notified.add(user_id)
    owner_real_id = game_owner_real_ids.get(user_id, "")
    if owner_real_id:
        await UniMessage(
            At("user", owner_real_id)
            + ' 的游戏进入自由抢答时间！发送"抢答 四字成语"来参与抢答！'
        ).send()
    else:
        await matcher.send('游戏进入自由抢答时间！发送"抢答 四字成语"来参与抢答！')
    set_timeout(matcher, user_id)


def set_grab_timer(matcher: Matcher, user_id: str):
    if user_id in grab_notified:
        return
    if timer := grab_timers.get(user_id, None):
        timer.cancel()
    loop = asyncio.get_running_loop()
    timer = loop.call_later(
        GAME_TIMEOUT,
        lambda: asyncio.ensure_future(grab_time_notify(matcher, user_id)),
    )
    grab_timers[user_id] = timer


@matcher_handle.handle()
async def _(
    matcher: Matcher,
    bot: Bot,
    uninfo: Uninfo,
    user_id: UserId,
):
    real_user_id = user_id.split("_")[-1]
    if real_user_id not in bot.config.superusers:
        daily_count = await HandleRecord.get_daily_count(real_user_id)
        if daily_count >= DAILY_LIMIT:
            await matcher.finish(f"你今天已经玩了{daily_count}次猜成语了，明天再来吧！")

    is_strict = handle_config.handle_strict_mode
    idiom, explanation = random_idiom()
    game = Handle(
        idiom, explanation, strict=is_strict, easy_mode=False, precise_mode=True
    )

    games[user_id] = game
    group_id = "_".join(user_id.split("_")[:-1])
    if group_id not in group_games:
        group_games[group_id] = set()
    group_games[group_id].add(user_id)
    game_owner_real_ids[user_id] = uninfo.user.id
    game_records[user_id] = await HandleRecord.create(
        user_id=real_user_id, is_win=False, attempts=0
    )
    set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    msg = f"你有{game.times}次机会猜一个四字成语，发送任意四字词语以参与游戏。"
    await UniMessage(msg + Image(raw=await run_sync(game.draw)())).send()


@matcher_handle_fuzzy.handle()
async def _(
    matcher: Matcher,
    bot: Bot,
    uninfo: Uninfo,
    user_id: UserId,
):
    real_user_id = user_id.split("_")[-1]
    if real_user_id not in bot.config.superusers:
        daily_count = await HandleRecord.get_daily_count(real_user_id)
        if daily_count >= DAILY_LIMIT:
            await matcher.finish(f"你今天已经玩了{daily_count}次猜成语了，明天再来吧！")

    is_strict = handle_config.handle_strict_mode
    idiom, explanation = random_idiom()
    game = Handle(
        idiom, explanation, strict=is_strict, easy_mode=False, precise_mode=False
    )

    games[user_id] = game
    group_id = "_".join(user_id.split("_")[:-1])
    if group_id not in group_games:
        group_games[group_id] = set()
    group_games[group_id].add(user_id)
    game_owner_real_ids[user_id] = uninfo.user.id
    game_records[user_id] = await HandleRecord.create(
        user_id=real_user_id, is_win=False, attempts=0
    )
    set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    msg = f"你有{game.times}次机会猜一个四字成语，发送任意四字词语以参与游戏。\n（模糊模式：不区分ü相关韵母）"
    await UniMessage(msg + Image(raw=await run_sync(game.draw)())).send()


@matcher_handle_easy.handle()
async def _(
    matcher: Matcher,
    bot: Bot,
    uninfo: Uninfo,
    user_id: UserId,
):
    real_user_id = user_id.split("_")[-1]
    if real_user_id not in bot.config.superusers:
        daily_count = await HandleRecord.get_daily_count(real_user_id)
        if daily_count >= DAILY_LIMIT:
            await matcher.finish(f"你今天已经玩了{daily_count}次猜成语了，明天再来吧！")

    is_strict = handle_config.handle_strict_mode
    idiom, explanation = random_idiom()
    game = Handle(
        idiom, explanation, strict=is_strict, easy_mode=True, precise_mode=True
    )

    games[user_id] = game
    group_id = "_".join(user_id.split("_")[:-1])
    if group_id not in group_games:
        group_games[group_id] = set()
    group_games[group_id].add(user_id)
    game_owner_real_ids[user_id] = uninfo.user.id
    game_records[user_id] = await HandleRecord.create(
        user_id=real_user_id, is_win=False, attempts=0
    )
    set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    msg = f"你有{game.times}次机会猜一个四字成语，发送任意四字词语以参与游戏。\n（简单模式：上方显示声母韵母提示）"
    await UniMessage(msg + Image(raw=await run_sync(game.draw)())).send()


@matcher_handle_easy_fuzzy.handle()
async def _(
    matcher: Matcher,
    bot: Bot,
    uninfo: Uninfo,
    user_id: UserId,
):
    real_user_id = user_id.split("_")[-1]
    if real_user_id not in bot.config.superusers:
        daily_count = await HandleRecord.get_daily_count(real_user_id)
        if daily_count >= DAILY_LIMIT:
            await matcher.finish(f"你今天已经玩了{daily_count}次猜成语了，明天再来吧！")

    is_strict = handle_config.handle_strict_mode
    idiom, explanation = random_idiom()
    game = Handle(
        idiom, explanation, strict=is_strict, easy_mode=True, precise_mode=False
    )

    games[user_id] = game
    group_id = "_".join(user_id.split("_")[:-1])
    if group_id not in group_games:
        group_games[group_id] = set()
    group_games[group_id].add(user_id)
    game_owner_real_ids[user_id] = uninfo.user.id
    game_records[user_id] = await HandleRecord.create(
        user_id=real_user_id, is_win=False, attempts=0
    )
    set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    msg = f"你有{game.times}次机会猜一个四字成语，发送任意四字词语以参与游戏。\n（简单模糊模式：显示声母韵母提示，不区分ü）"
    await UniMessage(msg + Image(raw=await run_sync(game.draw)())).send()


@matcher_hint.handle()
async def _(matcher: Matcher, user_id: UserId):
    game = games[user_id]
    if not game.use_hint():
        await matcher.finish("你的猜测次数已用完，无法使用提示！")
    set_timeout(matcher, user_id)
    await UniMessage.image(raw=await run_sync(game.draw_hint)()).send()


@matcher_stop.handle()
async def _(matcher: Matcher, user_id: UserId):
    game = games[user_id]
    record = game_records.get(user_id)
    stop_game(user_id)

    msg = "游戏已结束"
    if game.get_total_attempts() >= 1:
        msg += f"\n{game.result}"
    if record:
        record.is_win = False
        record.attempts = game.get_total_attempts()
        await record.save()
    await matcher.finish(msg)


@matcher_idiom.handle()
async def _(
    matcher: Matcher,
    uninfo: Uninfo,
    user_id: UserId,
    matched: dict[str, Any] = RegexDict(),
):
    game = games[user_id]
    if user_id in grab_notified:
        set_timeout(matcher, user_id)
    else:
        set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    idiom = str(matched["idiom"])
    result = game.guess(idiom)

    if result in [GuessResult.WIN, GuessResult.LOSS]:
        is_win = result == GuessResult.WIN
        attempts = game.get_total_attempts()
        real_user_id = user_id.split("_")[-1]

        record = game_records.get(user_id)
        if record:
            record.is_win = is_win
            record.attempts = attempts
            await record.save()

        stop_game(user_id)

        gold_reward = calculate_gold_reward(is_win, attempts, game.easy_mode)
        if gold_reward > 0:
            await UserConsole.add_gold(real_user_id, gold_reward, "handle")

        if is_win:
            if uninfo.scene.is_private:
                msg = f"恭喜你猜出了成语！获得{gold_reward}金币奖励！\n{game.result}"
                await UniMessage(msg + Image(raw=await run_sync(game.draw)())).send()
            else:
                await UniMessage(
                    At("user", uninfo.user.id)
                    + f" 恭喜你猜出了成语！获得{gold_reward}金币奖励！\n{game.result}"
                    + Image(raw=await run_sync(game.draw)())
                ).send()
        else:
            if uninfo.scene.is_private:
                msg = f"很遗憾，没有人猜出来呢。\n{game.result}"
                await UniMessage(msg + Image(raw=await run_sync(game.draw)())).send()
            else:
                await UniMessage(
                    At("user", uninfo.user.id)
                    + f" 很遗憾，没有人猜出来呢。\n{game.result}"
                    + Image(raw=await run_sync(game.draw)())
                ).send()

    elif result == GuessResult.DUPLICATE:
        if uninfo.scene.is_private:
            await matcher.finish("你已经猜过这个成语了呢")
        else:
            await UniMessage(
                At("user", uninfo.user.id) + " 你已经猜过这个成语了呢"
            ).finish()

    elif result == GuessResult.ILLEGAL:
        if uninfo.scene.is_private:
            await matcher.finish(f'你确定"{idiom}"是个成语吗？')
        else:
            await UniMessage(
                At("user", uninfo.user.id) + f' 你确定"{idiom}"是个成语吗？'
            ).finish()

    else:
        if uninfo.scene.is_private:
            await UniMessage.image(raw=await run_sync(game.draw)()).send()
        else:
            await UniMessage(
                At("user", uninfo.user.id) + Image(raw=await run_sync(game.draw)())
            ).send()


@matcher_grab.handle()
async def _(
    matcher: Matcher,
    uninfo: Uninfo,
    user_id: UserId,
    matched: dict[str, Any] = RegexDict(),
):
    group_id = "_".join(user_id.split("_")[:-1])

    if user_id in games:
        await matcher.finish()

    for uid in games:
        if uid != user_id and uid.endswith(f"_{uninfo.user.id}"):
            await matcher.finish()

    owner_ids = group_games.get(group_id, set())
    grabbable = [oid for oid in owner_ids if oid in grab_notified and oid in games]
    if not grabbable:
        await matcher.finish()

    idiom = str(matched["idiom"])

    for owner_id in grabbable:
        game = games[owner_id]
        set_timeout(matcher, owner_id)
        result = game.guess(idiom)

        if result in [GuessResult.WIN, GuessResult.LOSS]:
            is_win = result == GuessResult.WIN
            attempts = game.get_total_attempts()
            real_user_id = user_id.split("_")[-1]

            record = game_records.get(owner_id)
            if record:
                record.is_win = is_win
                record.attempts = attempts
                await record.save()

            stop_game(owner_id)

            gold_reward = calculate_gold_reward(is_win, attempts, game.easy_mode)
            if gold_reward > 0:
                await UserConsole.add_gold(real_user_id, gold_reward, "handle")

            if is_win:
                await UniMessage(
                    At("user", uninfo.user.id)
                    + f" 抢答成功！恭喜你猜出了成语！获得{gold_reward}金币奖励！\n{game.result}"
                    + Image(raw=await run_sync(game.draw)())
                ).send()
            else:
                await UniMessage(
                    At("user", uninfo.user.id)
                    + f" 很遗憾，没有人猜出来呢。\n{game.result}"
                    + Image(raw=await run_sync(game.draw)())
                ).send()

        elif result == GuessResult.DUPLICATE:
            await UniMessage(
                At("user", uninfo.user.id) + " 你已经猜过这个成语了呢"
            ).send()

        elif result == GuessResult.ILLEGAL:
            await UniMessage(
                At("user", uninfo.user.id) + f' 你确定"{idiom}"是个成语吗？'
            ).send()

        else:
            await UniMessage(
                At("user", uninfo.user.id) + Image(raw=await run_sync(game.draw)())
            ).send()
