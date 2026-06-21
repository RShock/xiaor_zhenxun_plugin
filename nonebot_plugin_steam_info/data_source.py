import json
import time
from PIL import Image
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple

from .models import Player, ProcessedPlayer

# 连续检查不玩游戏的次数阈值，达到后确认停止并播报
PENDING_STOP_THRESHOLD = 5


def _is_same_game(player_a: Dict, player_b: Dict) -> bool:
    """判断两个玩家状态是否在玩同一款游戏（优先比较 gameid，回退比较 gameextrainfo）"""
    gameid_a = player_a.get("gameid")
    gameid_b = player_b.get("gameid")
    if gameid_a and gameid_b and str(gameid_a) == str(gameid_b):
        return True
    if player_a.get("gameextrainfo") and player_a.get("gameextrainfo") == player_b.get("gameextrainfo"):
        return True
    return False


class BindData:
    def __init__(self, save_path: Path) -> None:
        self.content: Dict[str, List[Dict[str, str]]] = {}
        self._save_path = save_path

        if save_path.exists():
            self.content = json.loads(Path(save_path).read_text("utf-8"))
        else:
            self.save()

    def save(self) -> None:
        with open(self._save_path, "w", encoding="utf-8") as f:
            json.dump(self.content, f, indent=4)

    def add(self, parent_id: str, content: Dict[str, str]) -> None:
        if parent_id not in self.content:
            self.content[parent_id] = [content]
        else:
            self.content[parent_id].append(content)

    def remove(self, parent_id: str, user_id: str) -> None:
        if parent_id not in self.content:
            return
        for data in self.content[parent_id]:
            if data["user_id"] == user_id:
                self.content[parent_id].remove(data)
                break

    def update(self, parent_id: str, content: Dict[str, str]) -> None:
        self.content[parent_id] = content

    def get(self, parent_id: str, user_id: str) -> Optional[Dict[str, str]]:
        if parent_id not in self.content:
            return None
        for data in self.content[parent_id]:
            if data["user_id"] == user_id:
                if not data.get("nickname"):
                    data["nickname"] = None
                return data
        return None

    def get_by_steam_id(
        self, parent_id: str, steam_id: str
    ) -> Optional[Dict[str, str]]:
        if parent_id not in self.content:
            return None
        for data in self.content[parent_id]:
            if data["steam_id"] == steam_id:
                if not data.get("nickname"):
                    data["nickname"] = None
                return data
        return None

    def get_all(self, parent_id: str) -> List[str]:
        if parent_id not in self.content:
            return []

        result = []

        for data in self.content[parent_id]:
            if not data["steam_id"] in result:
                result.append(data["steam_id"])

        return result

    def get_all_steam_id(self) -> List[str]:
        result = []
        for parent_id in self.content:
            for data in self.content[parent_id]:
                if not data["steam_id"] in result:
                    result.append(data["steam_id"])
        return result


class SteamInfoData:
    def __init__(self, save_path: Path) -> None:
        self.content: List[ProcessedPlayer] = []
        self._save_path = save_path
        # 待确认的停止状态，不持久化，重启后重新计时
        # { steam_id: { gameextrainfo, gameid, game_start_time, check_count, first_stop_time, personaname } }
        self.pending_stops: Dict[str, Dict] = {}

        if save_path.exists():
            self.content = json.loads(save_path.read_text("utf-8"))
            if isinstance(self.content, dict):
                self.content = []
                self.save()
        else:
            self.save()

    def save(self) -> None:
        with open(self._save_path, "w", encoding="utf-8") as f:
            json.dump(self.content, f, indent=4)

    def update(self, player: ProcessedPlayer) -> None:
        self.content.append(player)

    def update_by_players(self, players: List[Player], init_mode: bool = False) -> List[Dict[str, Any]]:
        """更新玩家状态，处理动态灵敏度逻辑，返回需要播报的事件列表。

        Args:
            players: Steam API 返回的玩家列表
            init_mode: True 时仅同步初始状态，不生成事件、不推进 pending_stop 计数。
                       用于 bot 启动/重连时的状态初始化。

        事件类型:
        - start: 玩家开始玩游戏（立即播报）
        - stop: 玩家停止玩游戏（连续 PENDING_STOP_THRESHOLD 次确认后播报）
        - change: 玩家切换游戏（立即播报，含从待确认停止状态切换的情况）
        """
        events: List[Dict[str, Any]] = []
        processed_players: List[ProcessedPlayer] = []
        now = int(time.time())

        for player in players:
            old_player = self.get_player(player["steamid"])
            pending_stop = None if init_mode else self.pending_stops.get(player["steamid"])

            if player.get("gameextrainfo") is not None:
                # ---- 玩家当前正在玩游戏 ----
                if init_mode:
                    # 启动模式：直接记录当前状态，不检测切换也不生成事件
                    player["game_start_time"] = now
                elif pending_stop:
                    # 玩家处于待确认停止状态
                    if _is_same_game(player, pending_stop):
                        # 恢复同一游戏 → 视为网络故障，取消待确认停止
                        player["game_start_time"] = pending_stop["game_start_time"]
                        del self.pending_stops[player["steamid"]]
                    else:
                        # 开始玩不同游戏 → 立即播报切换
                        old_player_for_event = {
                            "steamid": player["steamid"],
                            "personaname": pending_stop.get("personaname", player["personaname"]),
                            "gameextrainfo": pending_stop["gameextrainfo"],
                            "gameid": pending_stop.get("gameid"),
                            "game_start_time": pending_stop["game_start_time"],
                        }
                        player["game_start_time"] = now
                        events.append({
                            "type": "change",
                            "player": player,
                            "old_player": old_player_for_event,
                            "stop_time": now,
                        })
                        del self.pending_stops[player["steamid"]]
                elif old_player is None:
                    # 新玩家，当前正在玩 → 静默记录，不播报
                    player["game_start_time"] = now
                elif old_player.get("gameextrainfo") is None:
                    # 之前不在玩，现在开始玩 → 播报开始
                    player["game_start_time"] = now
                    events.append({
                        "type": "start",
                        "player": player,
                        "old_player": old_player,
                    })
                elif _is_same_game(player, old_player):
                    # 继续玩同一游戏 → 保持开始时间
                    player["game_start_time"] = old_player["game_start_time"]
                else:
                    # 切换到不同游戏 → 播报切换
                    player["game_start_time"] = now
                    events.append({
                        "type": "change",
                        "player": player,
                        "old_player": old_player,
                        "stop_time": now,
                    })
            else:
                # ---- 玩家当前不在玩游戏 ----
                if init_mode:
                    # 启动模式：不玩游戏就不玩，不创建 pending_stop
                    player["game_start_time"] = None
                elif pending_stop:
                    # 已处于待确认停止状态
                    pending_stop["check_count"] += 1
                    if pending_stop["check_count"] >= PENDING_STOP_THRESHOLD:
                        # 连续足够多次确认 → 播报停止
                        old_player_for_event = {
                            "steamid": player["steamid"],
                            "personaname": pending_stop.get("personaname", player["personaname"]),
                            "gameextrainfo": pending_stop["gameextrainfo"],
                            "gameid": pending_stop.get("gameid"),
                            "game_start_time": pending_stop["game_start_time"],
                        }
                        player["game_start_time"] = None
                        events.append({
                            "type": "stop",
                            "player": player,
                            "old_player": old_player_for_event,
                            "stop_time": pending_stop["first_stop_time"],
                        })
                        del self.pending_stops[player["steamid"]]
                    else:
                        # 仍在宽限期内
                        player["game_start_time"] = None
                elif old_player is None:
                    # 新玩家，不在玩 → 静默记录
                    player["game_start_time"] = None
                elif old_player.get("gameextrainfo") is not None:
                    # 之前在玩，现在不在 → 创建待确认停止
                    self.pending_stops[player["steamid"]] = {
                        "gameextrainfo": old_player["gameextrainfo"],
                        "gameid": old_player.get("gameid"),
                        "game_start_time": old_player["game_start_time"],
                        "check_count": 1,
                        "first_stop_time": now,
                        "personaname": player["personaname"],
                    }
                    player["game_start_time"] = None
                else:
                    # 之前不在玩，现在也不在 → 无变化
                    player["game_start_time"] = None

            processed_players.append(player)

        if not init_mode:
            # 清理不在 API 返回中的玩家的待确认停止状态
            new_steam_ids = {p["steamid"] for p in players}
            for steam_id in list(self.pending_stops.keys()):
                if steam_id not in new_steam_ids:
                    del self.pending_stops[steam_id]

        self.content = processed_players
        return events

    def get_player(self, steam_id: str) -> Optional[Player]:
        for player in self.content:
            if player["steamid"] == steam_id:
                return player
        return None

    def get_players(self, steam_ids: List[str]) -> List[Player]:
        result = []
        for player in self.content:
            if player["steamid"] in steam_ids:
                result.append(player)
        return result

    def compare(
        self, old_players: List[Player], new_players: List[Player]
    ) -> List[Dict[str, Any]]:
        result = []

        for player in new_players:
            for old_player in old_players:
                if player["steamid"] == old_player["steamid"]:
                    if player.get("gameextrainfo") != old_player.get("gameextrainfo"):
                        if (
                            player.get("gameextrainfo") is not None
                            and old_player.get("gameextrainfo") is not None
                        ):
                            result.append(
                                {
                                    "type": "change",
                                    "player": player,
                                    "old_player": old_player,
                                }
                            )
                        elif old_player.get("gameextrainfo") is not None:
                            result.append(
                                {
                                    "type": "stop",
                                    "player": player,
                                    "old_player": old_player,
                                }
                            )
                        elif player.get("gameextrainfo") is not None :
                            result.append(
                                {
                                    "type": "start",
                                    "player": player,
                                    "old_player": old_player,
                                }
                            )
                        else:
                            result.append(
                                {
                                    "type": "error",
                                    "player": player,
                                    "old_player": old_player,
                                }
                            )
        return result


class ParentData:
    def __init__(self, save_path: Path) -> None:
        self.content: Dict[str, str] = {}  # parent_id: name
        self._save_path = save_path

        if not save_path.exists():
            save_path.parent.mkdir(parents=True, exist_ok=True)
            self.save()
        else:
            self.content = json.loads(save_path.read_text("utf-8"))

    def save(self) -> None:
        with open(self._save_path, "w", encoding="utf-8") as f:
            json.dump(self.content, f, indent=4)

    def update(self, parent_id: str, avatar: Image.Image, name: str) -> None:
        self.content[parent_id] = name
        self.save()
        # 保存图片
        avatar_path = self._save_path.parent / f"{parent_id}.png"
        avatar.save(avatar_path)

    def get(self, parent_id: str) -> Tuple[Image.Image, str]:
        if parent_id not in self.content:
            return (
                Image.open(Path(__file__).parent / "res/unknown_avatar.jpg"),
                parent_id,
            )
        avatar_path = self._save_path.parent / f"{parent_id}.png"
        return Image.open(avatar_path), self.content[parent_id]


class DisableParentData:
    """储存禁用 Steam 通知的 parent"""

    def __init__(self, save_path: Path) -> None:
        self.content: List[str] = []
        self._save_path = save_path

        if save_path.exists():
            self.content = json.loads(save_path.read_text("utf-8"))
        else:
            self.save()

    def save(self) -> None:
        with open(self._save_path, "w", encoding="utf-8") as f:
            json.dump(self.content, f, indent=4)

    def add(self, parent_id: str) -> None:
        if parent_id not in self.content:
            self.content.append(parent_id)
            self.save()

    def remove(self, parent_id: str) -> None:
        if parent_id in self.content:
            self.content.remove(parent_id)
            self.save()

    def is_disabled(self, parent_id: str) -> bool:
        return parent_id in self.content
