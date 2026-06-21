"""
handle2 - 字形拆分猜成语游戏 Web 测试服务器。

测试入口弱化为核心状态接口，不再 monkey patch Handle2Game。
"""

import random
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

if __package__:
    from .data_source import GuessResult, Handle2Game, _init_data
    from .utils import random_idiom
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from zhenxun.plugins.nonebot_plugin_handle2.data_source import (
        GuessResult,
        Handle2Game,
        _init_data,
    )
    from zhenxun.plugins.nonebot_plugin_handle2.utils import random_idiom

app = Flask(__name__)
games: dict[str, Handle2Game] = {}


@app.route("/")
def index():
    return send_from_directory(".", "game.html")


@app.route("/api/new_game", methods=["POST"])
def new_game():
    game_id = f"game_{random.randint(0, 999999)}"
    idiom, explanation = random_idiom()
    game = Handle2Game(idiom, explanation)
    games[game_id] = game
    state = game.to_state()
    state["game_id"] = game_id
    return jsonify(state)


@app.route("/api/guess", methods=["POST"])
def guess():
    data = request.json or {}
    game = games.get(data.get("game_id"))
    if not game:
        return jsonify({"error": "游戏不存在"}), 404

    result = game.guess(data.get("idiom", ""))
    if result == GuessResult.STROKE_LIMIT:
        return jsonify({"error": "答案中没有超过20画的字，不能使用20画以上的字"})
    if result == GuessResult.DUPLICATE:
        return jsonify({"error": "你已经猜过这个成语了"})
    if result == GuessResult.ILLEGAL:
        return jsonify({"error": "请输入合法的四字成语"})
    return jsonify(game.to_state())


if __name__ == "__main__":
    _init_data()
    print("数据加载完成，启动服务器...")
    app.run(host="0.0.0.0", port=5000, debug=False)
