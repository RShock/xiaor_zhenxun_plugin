import json
import random
from io import BytesIO
from pathlib import Path

from PIL import ImageFont
from PIL.Image import Image as IMG
from PIL.ImageFont import FreeTypeFont
from pypinyin import Style, pinyin

resource_dir = Path(__file__).parent / "resources"
fonts_dir = resource_dir / "fonts"
data_dir = resource_dir / "data"
idiom_path = data_dir / "idioms.txt"
answer_path = data_dir / "answers.json"


def legal_idiom(word: str) -> bool:
    with idiom_path.open("r", encoding="utf-8") as f:
        return word in (idiom.strip() for idiom in f.readlines())


def random_idiom() -> tuple[str, str]:
    with answer_path.open("r", encoding="utf-8") as f:
        answers: list[dict[str, str]] = json.load(f)
        answer = random.choice(answers)
        return answer["word"], answer["explanation"]


# fmt: off
# 声母
INITIALS = [
    "zh", "z", "y", "x", "w", "t", "sh", "s", "r", "q", "p",
    "n", "m", "l", "k", "j", "h", "g", "f", "d", "ch", "c", "b"
]
# 韵母
FINALS = [
    "ün", "üe", "üan", "ü", "uo", "un", "ui", "ue", "uang",
    "uan", "uai","ua", "ou", "iu", "iong", "ong", "io", "ing",
    "in", "ie", "iao", "iang", "ian", "ia", "er", "eng", "en",
    "ei", "ao", "ang", "an", "ai", "u", "o", "i", "e", "a"
]
# ü相关韵母（精确模式专用）
U_FINALS = ["ün", "üe", "üan", "ü"]

TONE_MARKS = {
    "1": "ˉ",
    "2": "ˊ",
    "3": "ˇ",
    "4": "ˋ",
    "5": "",
}

TONE_POSITION = {
    "a": 0,
    "ang": 0,
    "an": 0,
    "ao": 0,
    "ai": 0,
    "o": 0,
    "ou": 0,
    "ong": 0,
    "e": 0,
    "ei": 0,
    "eng": 0,
    "en": 0,
    "er": 0,
    "i": 0,
    "in": 0,
    "ing": 0,
    "ie": 1,
    "iao": 1,
    "iang": 1,
    "ian": 1,
    "ia": 1,
    "u": 0,
    "un": 0,
    "uo": 1,
    "ui": 1,
    "ua": 1,
    "uang": 1,
    "uan": 1,
    "uai": 1,
    "ü": 0,
    "ün": 0,
    "üe": 1,
    "üan": 1,
    "iu": 1,
    "iong": 1,
    "io": 1,
}
# fmt: on


def get_tone_position(final: str) -> int:
    return TONE_POSITION.get(final, 0)


def get_pinyin(idiom: str, precise_mode: bool = False) -> list[tuple[str, str, str]]:
    pys = pinyin(idiom, style=Style.TONE3, v_to_u=True)
    results = []
    for p in pys:
        py = p[0]
        if py[-1].isdigit():
            tone = TONE_MARKS.get(py[-1], "")
            py = py[:-1]
        else:
            tone = ""
        initial = ""
        for i in INITIALS:
            if py.startswith(i):
                initial = i
                break
        final = ""
        for f in FINALS:
            if py.endswith(f):
                final = f
                break
        if initial in ("j", "q", "x") and final == "u":
            final = "ü"
        elif initial in ("j", "q", "x") and final == "ue":
            final = "üe"
        elif initial in ("j", "q", "x") and final == "uan":
            final = "üan"
        elif initial in ("j", "q", "x") and final == "un":
            final = "ün"
        if not precise_mode:
            if final == "ü":
                final = "u"
            elif final == "üe":
                final = "ue"
            elif final == "üan":
                final = "uan"
            elif final == "ün":
                final = "un"
        results.append((initial, final, tone))
    return results


def save_jpg(frame: IMG) -> BytesIO:
    output = BytesIO()
    frame = frame.convert("RGB")
    frame.save(output, format="jpeg")
    return output


_font_cache: dict[str, FreeTypeFont] = {}


def load_font(name: str, fontsize: int) -> FreeTypeFont:
    cache_key = f"{name}:{fontsize}"
    if cache_key in _font_cache:
        return _font_cache[cache_key]
    font = ImageFont.truetype(str(fonts_dir / name), fontsize, encoding="utf-8")
    _font_cache[cache_key] = font
    return font
