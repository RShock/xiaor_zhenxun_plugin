"""handle2 兼容导出层。"""

from .game import GuessResult, Handle2Game
from .ids import IDS_INVALID_MARKERS, IDS_OP_CHARS, IDS_OPERATORS, IDSNode, IDSParser
from .renderer import FontManager, Handle2Renderer
from .resources import init_data as _init_data

__all__ = [
    "IDS_INVALID_MARKERS",
    "IDS_OPERATORS",
    "IDS_OP_CHARS",
    "FontManager",
    "GuessResult",
    "Handle2Game",
    "Handle2Renderer",
    "IDSNode",
    "IDSParser",
    "_init_data",
]
