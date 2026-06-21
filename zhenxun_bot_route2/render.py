import asyncio
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nonebot.log import logger
from nonebot_plugin_htmlrender import html_to_pic

TEMPLATES_PATH = Path(__file__).parent / "templates"
FONTS_PATH = Path(__file__).parent.parent.parent.parent / "resources" / "fonts"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_PATH)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _get_emoji_font_path() -> str:
    noto = FONTS_PATH / "NotoColorEmoji-Regular.ttf"
    if noto.exists():
        return noto.as_posix()
    return ""


async def _render_html(html: str, width: int = 450) -> bytes:
    last_error = None
    for attempt in range(3):
        try:
            return await html_to_pic(
                html,
                wait=500,
                template_path=f"file:///{TEMPLATES_PATH.as_posix()}",
                viewport={"width": width, "height": 10},
            )
        except Exception as e:
            last_error = e
            logger.warning(f"[Route] 渲染截图失败 (尝试 {attempt + 1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
    raise last_error


async def render_status_page(data: dict) -> bytes:
    data["font_path_emoji"] = _get_emoji_font_path()
    template = _jinja_env.get_template("lb_status.html")
    html = template.render(**data)
    return await _render_html(html, 450)
