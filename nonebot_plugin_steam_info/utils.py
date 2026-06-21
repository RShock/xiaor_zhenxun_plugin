import asyncio
import json
import time
import pytz
import httpx
import datetime
import calendar
from PIL import Image
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional
from nonebot.log import logger

from .models import Player
from .data_source import BindData


_game_name_cache: Dict[str, str] = {}
_game_name_cache_path: Optional[Path] = None


def init_game_name_cache(cache_path: Path):
    global _game_name_cache_path, _game_name_cache
    _game_name_cache_path = cache_path
    if cache_path.exists():
        try:
            _game_name_cache = json.loads(cache_path.read_text("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _game_name_cache = {}
            save_game_name_cache()
    else:
        _game_name_cache = {}
        save_game_name_cache()


def save_game_name_cache():
    if _game_name_cache_path is not None:
        with open(_game_name_cache_path, "w", encoding="utf-8") as f:
            json.dump(_game_name_cache, f, indent=4, ensure_ascii=False)


async def get_chinese_game_name(game_id: str, proxy: str = None) -> Optional[str]:
    if not game_id or not str(game_id).isdigit():
        return None

    game_id_str = str(game_id)

    if game_id_str in _game_name_cache:
        return _game_name_cache[game_id_str]

    try:
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.get(
                f"https://store.steampowered.com/api/appdetails?appids={game_id_str}&l=schinese"
            )
            if response.status_code == 200:
                data = response.json()
                app_data = data.get(game_id_str, {})
                if app_data.get("success", False):
                    chinese_name = app_data["data"]["name"]
                    _game_name_cache[game_id_str] = chinese_name
                    save_game_name_cache()
                    return chinese_name
    except Exception as e:
        logger.error(f"获取游戏中文名失败 (gameid={game_id_str}): {e}")

    return None


AVATAR_MAX_RETRIES = 3
AVATAR_RETRY_BASE_DELAY = 2


async def _fetch_avatar(avatar_url: str, proxy: str = None) -> Image.Image:
    default_avatar_path = Path(__file__).parent / "res/unknown_avatar.jpg"
    for attempt in range(AVATAR_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(proxy=proxy) as client:
                response = await client.get(avatar_url)
                if response.status_code != 200:
                    logger.warning(
                        f"[Steam 头像] HTTP {response.status_code}"
                        f" (attempt {attempt + 1}/{AVATAR_MAX_RETRIES}), 使用默认头像"
                    )
                    return Image.open(default_avatar_path)
                return Image.open(BytesIO(response.content))
        except Exception as e:
            logger.warning(
                f"[Steam 头像] 下载异常 (attempt {attempt + 1}/{AVATAR_MAX_RETRIES}): {e}"
            )
            if attempt < AVATAR_MAX_RETRIES - 1:
                delay = AVATAR_RETRY_BASE_DELAY**attempt
                await asyncio.sleep(delay)
    logger.warning(f"[Steam 头像] 全部重试失败, 使用默认头像")
    return Image.open(default_avatar_path)


async def fetch_avatar(
    player: Player, avatar_dir: Optional[Path], proxy: str = None
) -> Image.Image:
    if avatar_dir is not None:
        avatar_path = (
            avatar_dir / f"avatar_{player['steamid']}_{player['avatarhash']}.png"
        )

        if avatar_path.exists():
            avatar = Image.open(avatar_path)
        else:
            avatar = await _fetch_avatar(player["avatarfull"], proxy)

            avatar.save(avatar_path)
    else:
        avatar = await _fetch_avatar(player["avatarfull"], proxy)

    return avatar


async def _prefetch_single_avatar(
    player: Player, avatar_path: Path, proxy: str = None
) -> bool:
    try:
        avatar = await _fetch_avatar(player["avatarfull"], proxy)
        avatar.save(avatar_path)
        return True
    except Exception as e:
        logger.warning(f"[Steam 头像] 预加载失败 ({player.get('personaname', 'unknown')}): {e}")
        return False


async def prefetch_avatars(
    players: List[Player], avatar_dir: Optional[Path], proxy: str = None
):
    if avatar_dir is None or not players:
        return
    missing = 0
    for player in players:
        avatar_path = (
            avatar_dir / f"avatar_{player['steamid']}_{player['avatarhash']}.png"
        )
        if not avatar_path.exists():
            missing += 1
    if missing == 0:
        return
    logger.info(f"[Steam 头像] 预加载开始: {missing}/{len(players)} 个待下载")
    tasks = []
    for player in players:
        avatar_path = (
            avatar_dir / f"avatar_{player['steamid']}_{player['avatarhash']}.png"
        )
        if not avatar_path.exists():
            tasks.append(_prefetch_single_avatar(player, avatar_path, proxy))
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if r is True)
        logger.info(f"[Steam 头像] 预加载完成: {success}/{len(tasks)} 成功")


def convert_player_name_to_nickname(
    data: Dict[str, str], parent_id: str, bind_data: BindData
) -> Dict[str, str]:
    data["nickname"] = bind_data.get_by_steam_id(parent_id, data["steamid"])["nickname"]
    return data


async def simplize_steam_player_data(
    player: Player, proxy: str = None, avatar_dir: Path = None
) -> Dict[str, str]:
    avatar = await fetch_avatar(player, avatar_dir, proxy)

    gameextrainfo = player.get("gameextrainfo")
    gameid = player.get("gameid")

    display_game_name = gameextrainfo
    if gameextrainfo and gameid:
        chinese_name = await get_chinese_game_name(str(gameid), proxy)
        if chinese_name:
            display_game_name = chinese_name

    if player["personastate"] == 0:
        if not player.get("lastlogoff"):
            status = "离线"
        else:
            time_logged_off = player["lastlogoff"]
            time_to_now = calendar.timegm(time.gmtime()) - time_logged_off

            if time_to_now < 60:
                status = "上次在线 刚刚"
            elif time_to_now < 3600:
                status = f"上次在线 {time_to_now // 60} 分钟前"
            elif time_to_now < 86400:
                status = f"上次在线 {time_to_now // 3600} 小时前"
            elif time_to_now < 2592000:
                status = f"上次在线 {time_to_now // 86400} 天前"
            elif time_to_now < 31536000:
                status = f"上次在线 {time_to_now // 2592000} 个月前"
            else:
                status = f"上次在线 {time_to_now // 31536000} 年前"
    elif player["personastate"] in [1, 2, 4]:
        status = "在线" if gameextrainfo is None else display_game_name
    elif player["personastate"] == 3:
        status = "离开" if gameextrainfo is None else display_game_name
    elif player["personastate"] in [5, 6]:
        status = "在线"
    else:
        status = "未知"

    return {
        "steamid": player["steamid"],
        "avatar": avatar,
        "name": player["personaname"],
        "status": status,
        "personastate": player["personastate"],
    }


def image_to_bytes(image: Image.Image) -> bytes:
    with BytesIO() as bio:
        image.save(bio, format="PNG")
        return bio.getvalue()


def hex_to_rgb(hex_color: str):
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def convert_timestamp_to_beijing_time(timestamp: int) -> str:
    beijing_timezone = pytz.timezone("Asia/Shanghai")
    date_utc = datetime.datetime.fromtimestamp(timestamp, pytz.utc)
    date_beijing = date_utc.astimezone(beijing_timezone)
    return date_beijing.strftime("%Y-%m-%d %H:%M:%S")
    # example: 2021-09-06 21:00:00
