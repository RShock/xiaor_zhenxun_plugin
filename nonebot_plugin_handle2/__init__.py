import asyncio
from asyncio import TimerHandle
from datetime import datetime
from typing import Annotated, Any

from nonebot import on_regex, require
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Event, Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import Depends, RegexDict
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot.utils import run_sync

require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")

from nonebot_plugin_alconna import Image, UniMessage, on_alconna
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.user_console import UserConsole

from .config import Config
from .data_source import GuessResult, Handle2Game
from .models import Handle2Record
from .utils import random_idiom

__plugin_meta__ = PluginMetadata(
    name="拆字猜成语",
    description="字形拆分Wordle 猜成语",
    usage=(
        '发送"猜成语"开始游戏；\n'
        "你有十次的机会猜一个四字成语；\n"
        "每个汉字按一级拆分分为部件，部件独立匹配；\n"
        "青色 表示部件存在且位置正确；\n"
        "橙色 表示部件存在但位置不正确；\n"
        "灰色 表示部件不存在；\n"
        "独体字不可拆分，整字作为一个部件匹配；\n"
        "若答案中没有超过20画的字，使用20画以上的字将被拒绝；\n"
        "3次和6次失败后会额外揭示部件提示。\n"
        '可发送"结束"结束游戏。\n'
        "每人每天只能发起3次猜成语游戏。"
    ),
    type="application",
    config=Config,
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna", "nonebot_plugin_uninfo"
    ),
)


DAILY_LIMIT = 3
GAME_TIMEOUT = 300

games: dict[str, Handle2Game] = {}
timers: dict[str, TimerHandle] = {}
group_games: dict[str, set[str]] = {}
grab_timers: dict[str, TimerHandle] = {}
game_owner_real_ids: dict[str, str] = {}
grab_notified: set[str] = set()
game_records: dict[str, Handle2Record] = {}


def get_user_id(uninfo: Uninfo) -> str:
    return f"{uninfo.scope}_{uninfo.self_id}_{uninfo.scene_path}_{uninfo.user.id}"


def get_group_id(uninfo: Uninfo) -> str:
    return f"{uninfo.scope}_{uninfo.self_id}_{uninfo.scene_path}"


def calculate_gold_reward(is_win: bool, attempts: int) -> int:
    if not is_win:
        return 0
    base = 400
    # 第一次猜中400，每多猜错一次×0.75
    reward = base * (0.75 ** (attempts - 1))
    return max(int(reward), 10)


UserId = Annotated[str, Depends(get_user_id)]


async def _send_game_image(
    matcher: Matcher, game: Handle2Game, text: str = "", at_uid: str = ""
):
    """用 UniMessage + Image(raw=bytes) 发送游戏图片，与原版 handle 一致"""
    img_bytes = (await run_sync(game.draw)()).read()
    uni = UniMessage()
    if at_uid:
        uni += UniMessage.at(at_uid)
    if text:
        uni += UniMessage.text(text + "\n")
    uni += UniMessage.image(raw=img_bytes)
    await uni.send()


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
matcher_stop = on_alconna(
    "handle2_stop",
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
matcher_reset = on_regex(
    r"^重置次数",
    block=True,
    priority=13,
)
matcher_hint = on_regex(
    r"^提示$",
    rule=game_is_running,
    block=True,
    priority=13,
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
        text = "猜成语超时，游戏结束。"
        text += f"\n{game.result}"
        if record:
            record.is_win = False
            record.attempts = game.get_total_attempts()
            await record.save()
        if owner_real_id:
            m = Message()
            m += MessageSegment.at(owner_real_id)
            m += MessageSegment.text(" " + text)
            await matcher.finish(m)
        else:
            await matcher.finish(text)


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
        m = Message()
        m += MessageSegment.at(owner_real_id)
        m += MessageSegment.text(
            ' 的游戏进入自由抢答时间！发送"抢答 四字成语"来参与抢答！'
        )
        await matcher.send(m)
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
        daily_count = await Handle2Record.get_daily_count(real_user_id)
        if daily_count >= DAILY_LIMIT:
            await matcher.finish(f"你今天已经玩了{daily_count}次猜成语了，明天再来吧！")

    idiom, explanation = random_idiom()
    game = Handle2Game(idiom, explanation)

    games[user_id] = game
    group_id = get_group_id(uninfo)
    if group_id not in group_games:
        group_games[group_id] = set()
    group_games[group_id].add(user_id)
    game_owner_real_ids[user_id] = uninfo.user.id
    game_records[user_id] = await Handle2Record.create(
        user_id=real_user_id, is_win=False, attempts=0
    )
    set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    msg = f"你有{game.times}次机会猜一个四字成语，发送任意四字词语以参与游戏。"
    await _send_game_image(matcher, game, text=msg)


@matcher_stop.handle()
async def _(matcher: Matcher, user_id: UserId):
    game = games[user_id]
    record = game_records.get(user_id)
    stop_game(user_id)

    msg = "游戏已结束"
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

        gold_reward = calculate_gold_reward(is_win, attempts)
        if gold_reward > 0:
            await UserConsole.add_gold(real_user_id, gold_reward, "handle2")

        if is_win:
            text = f"恭喜你猜出了成语！获得{gold_reward}金币奖励！\n{game.result}"
            at_uid = "" if uninfo.scene.is_private else uninfo.user.id
            await _send_game_image(matcher, game, text=text, at_uid=at_uid)
        else:
            text = f"很遗憾，没有人猜出来呢。\n{game.result}"
            at_uid = "" if uninfo.scene.is_private else uninfo.user.id
            await _send_game_image(matcher, game, text=text, at_uid=at_uid)

    elif result == GuessResult.STROKE_LIMIT:
        if uninfo.scene.is_private:
            await matcher.finish("答案中没有超过20画的字，不能使用20画以上的字")
        else:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 答案中没有超过20画的字，不能使用20画以上的字")
            await matcher.finish(m)

    elif result == GuessResult.DUPLICATE:
        if uninfo.scene.is_private:
            await matcher.finish("你已经猜过这个成语了")
        else:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 你已经猜过这个成语了")
            await matcher.finish(m)

    elif result == GuessResult.ILLEGAL:
        if uninfo.scene.is_private:
            await matcher.finish("请输入合法的四字成语")
        else:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 请输入合法的四字成语")
            await matcher.finish(m)

    else:
        at_uid = "" if uninfo.scene.is_private else uninfo.user.id
        await _send_game_image(matcher, game, at_uid=at_uid)


@matcher_hint.handle()
async def _(matcher: Matcher, uninfo: Uninfo, user_id: UserId):
    """提示：暴露一个部件，推进到下一个3的倍数次数"""
    game = games[user_id]
    set_timeout(matcher, user_id, GAME_TIMEOUT * 2)
    set_grab_timer(matcher, user_id)

    total_before = game.get_total_attempts()
    hint = game.request_hint()

    if hint is None:
        if uninfo.scene.is_private:
            await matcher.finish("已经没有更多提示了，或次数已用完。")
        else:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 已经没有更多提示了，或次数已用完。")
            await matcher.finish(m)

    total_after = game.get_total_attempts()
    text = f"揭示部件「{hint['part']}」（第{hint['char_index'] + 1}字），已使用{total_after}次机会。"

    # 检查是否因此用完所有次数
    if total_after >= game.MAX_ATTEMPTS:
        record = game_records.get(user_id)
        if record:
            record.is_win = False
            record.attempts = total_after
            await record.save()
        stop_game(user_id)
        text += f"\n次数已用完，游戏结束。\n{game.result}"

    at_uid = "" if uninfo.scene.is_private else uninfo.user.id
    await _send_game_image(matcher, game, text=text, at_uid=at_uid)


@matcher_reset.handle()
async def _(matcher: Matcher, bot: Bot, user_id: UserId, event: Event):
    """重置指定用户的今日猜成语次数"""
    sender_id = user_id.split("_")[-1]
    if sender_id not in bot.config.superusers:
        await matcher.finish()

    # 按 fishing 插件 _get_at_list 模式提取 @ 目标
    target_qq = None
    for seg in event.get_message():
        if seg.type == "at":
            target_qq = seg.data.get("qq", "")
            break

    if not target_qq:
        await matcher.finish("请@你要重置次数的用户")

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    await Handle2Record.filter(user_id=target_qq, created_at__gte=today).delete()

    await matcher.finish(f"已重置用户 {target_qq} 的今日猜成语次数，现在可以再玩3次。")


@matcher_grab.handle()
async def _(
    matcher: Matcher,
    uninfo: Uninfo,
    user_id: UserId,
    matched: dict[str, Any] = RegexDict(),
):
    group_id = get_group_id(uninfo)

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

            gold_reward = calculate_gold_reward(is_win, attempts)
            if gold_reward > 0:
                await UserConsole.add_gold(real_user_id, gold_reward, "handle2")

            if is_win:
                text = f"抢答成功！恭喜你猜出了成语！获得{gold_reward}金币奖励！\n{game.result}"
                await _send_game_image(matcher, game, text=text, at_uid=uninfo.user.id)
            else:
                text = f"很遗憾，没有人猜出来呢。\n{game.result}"
                await _send_game_image(matcher, game, text=text, at_uid=uninfo.user.id)

        elif result == GuessResult.STROKE_LIMIT:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 答案中没有超过20画的字，不能使用20画以上的字")
            await matcher.send(m)

        elif result == GuessResult.DUPLICATE:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 你已经猜过这个成语了")
            await matcher.send(m)

        elif result == GuessResult.ILLEGAL:
            m = Message()
            m += MessageSegment.at(uninfo.user.id)
            m += MessageSegment.text(" 请输入合法的四字成语")
            await matcher.send(m)

        else:
            await _send_game_image(matcher, game, at_uid=uninfo.user.id)
