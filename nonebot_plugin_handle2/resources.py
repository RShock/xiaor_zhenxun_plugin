"""插件资源路径与懒加载数据。"""

from pathlib import Path

from .ids import IDSParser, load_ids_data, load_stroke_data

RESOURCE_DIR = Path(__file__).parent / "resources"
FONTS_DIR = RESOURCE_DIR / "fonts"
DATA_DIR = RESOURCE_DIR / "data"
IDS_PATH = DATA_DIR / "ids.txt"
UNIHAN_PATH = DATA_DIR / "Unihan.zip"

_ids_data: dict[str, str] | None = None
_stroke_data: dict[str, int] | None = None
_ids_parser: IDSParser | None = None


def init_data() -> tuple[IDSParser, dict[str, int]]:
    global _ids_data, _stroke_data, _ids_parser
    if _ids_parser is None:
        _ids_data = load_ids_data(str(IDS_PATH))
        _stroke_data = load_stroke_data(str(UNIHAN_PATH))
        _ids_parser = IDSParser(_ids_data, _stroke_data)
    return _ids_parser, _stroke_data or {}
