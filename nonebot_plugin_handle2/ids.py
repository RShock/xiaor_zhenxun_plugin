"""IDS 数据结构、解析与资源数据加载。"""

import zipfile
from dataclasses import dataclass, field

IDS_OPERATORS = {
    "⿰": {"arity": 2, "name": "左右", "type": "horizontal"},
    "⿱": {"arity": 2, "name": "上下", "type": "vertical"},
    "⿲": {"arity": 3, "name": "左中右", "type": "horizontal"},
    "⿳": {"arity": 3, "name": "上中下", "type": "vertical"},
    "⿴": {"arity": 2, "name": "全包围", "type": "surround"},
    "⿵": {"arity": 2, "name": "上包围", "type": "surround_top"},
    "⿶": {"arity": 2, "name": "下包围", "type": "surround_bottom"},
    "⿷": {"arity": 2, "name": "左包围", "type": "surround_left"},
    "⿸": {"arity": 2, "name": "左上包围", "type": "surround_top_left"},
    "⿹": {"arity": 2, "name": "右上包围", "type": "surround_top_right"},
    "⿺": {"arity": 2, "name": "左下包围", "type": "surround_bottom_left"},
    "⿻": {"arity": 2, "name": "叠加", "type": "overlay"},
}

IDS_OP_CHARS = set(IDS_OPERATORS.keys())
IDS_INVALID_MARKERS = set("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")


@dataclass
class IDSNode:
    operator: str | None = None
    children: list["IDSNode"] = field(default_factory=list)
    char: str | None = None

    @property
    def is_leaf(self) -> bool:
        return self.operator is None

    def get_components(self) -> list[str]:
        if self.is_leaf:
            return [self.char] if self.char else []
        result = []
        for child in self.children:
            result.extend(child.get_components())
        return result

    def get_structure_name(self) -> str:
        if self.is_leaf:
            return "独体"
        return IDS_OPERATORS.get(self.operator, {}).get("name", "未知")


class IDSParser:
    def __init__(self, ids_data: dict[str, str], stroke_data: dict[str, int] | None = None):
        self.ids_data = ids_data
        self.stroke_data = stroke_data or {}

    def decompose_level1(self, char: str) -> IDSNode:
        if self._is_single_component(char):
            return IDSNode(char=char)
        ids_str = self.ids_data.get(char)
        if ids_str is None or not any(op in ids_str for op in IDS_OP_CHARS):
            return IDSNode(char=char)
        try:
            node, _ = self._parse_ids(ids_str, 0)
        except (IndexError, ValueError):
            return IDSNode(char=char)
        node.children = [child if child.is_leaf else self._flatten(child) for child in node.children]
        return node

    def _is_single_component(self, char: str) -> bool:
        ids_str = self.ids_data.get(char)
        if ids_str is None or ids_str == char:
            return True
        if ids_str.startswith("⿻"):
            try:
                node, _ = self._parse_ids(ids_str, 0)
            except (IndexError, ValueError):
                return False
            return all(
                child.is_leaf and self.stroke_data.get(child.char or "", 99) <= 3
                for child in node.children
            )
        return False

    def _flatten(self, node: IDSNode) -> IDSNode:
        if node.is_leaf:
            return node
        node.children = [child if child.is_leaf else self._flatten(child) for child in node.children]
        return node

    def _parse_ids(self, ids: str, pos: int) -> tuple[IDSNode, int]:
        if pos >= len(ids):
            raise ValueError(f"IDS字符串意外结束: {ids}")
        char = ids[pos]
        if char not in IDS_OP_CHARS:
            return IDSNode(char=char), pos + 1
        children = []
        cur_pos = pos + 1
        for _ in range(IDS_OPERATORS[char]["arity"]):
            child, cur_pos = self._parse_ids(ids, cur_pos)
            children.append(child)
        return IDSNode(operator=char, children=children), cur_pos


def load_ids_data(filepath: str) -> dict[str, str]:
    ids_map = {}
    with open(filepath, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            char = parts[1]
            ids_str = _pick_ids_entry(parts[2:])
            if ids_str and char and ids_str != char:
                if not any(marker in ids_str for marker in IDS_INVALID_MARKERS):
                    ids_map[char] = ids_str
    return ids_map


def _pick_ids_entry(entries: list[str]) -> str | None:
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if entry.endswith("]"):
            bracket_pos = entry.rfind("[")
            if bracket_pos > 0:
                return entry[:bracket_pos]
        else:
            return entry
    return None


def load_stroke_data(unihan_zip_path: str) -> dict[str, int]:
    stroke_map = {}
    with zipfile.ZipFile(unihan_zip_path, "r") as archive:
        data = archive.read("Unihan_IRGSources.txt").decode("utf-8")
    for line in data.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3 or parts[1] != "kTotalStrokes":
            continue
        try:
            char = chr(int(parts[0][2:], 16))
            stroke_map.setdefault(char, int(parts[2].split()[0]))
        except (ValueError, IndexError):
            pass
    return stroke_map
