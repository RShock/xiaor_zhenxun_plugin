"""拆字猜成语图片渲染。"""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .ids import IDS_OPERATORS, IDSNode
from .resources import FONTS_DIR


class FontManager:
    _fonts: dict[str, ImageFont.FreeTypeFont] = {}
    _char_cache: dict[tuple[int, int], str | None] = {}

    @classmethod
    def get_font(cls, text: str, fontsize: int) -> ImageFont.FreeTypeFont:
        char = text[0] if text else "?"
        cache_key = (ord(char), fontsize)
        if cache_key in cls._char_cache:
            font_name = cls._char_cache[cache_key]
            return cls._get_or_load(font_name or "NotoSerifSC-Regular.otf", fontsize)
        for font_name in ("HanaMinA.otf", "HanaMinB.otf", "NotoSerifSC-Regular.otf"):
            font = cls._get_or_load(font_name, fontsize)
            try:
                if font.getmask(char):
                    cls._char_cache[cache_key] = font_name
                    return font
            except Exception:
                continue
        cls._char_cache[cache_key] = None
        return cls._get_or_load("NotoSerifSC-Regular.otf", fontsize)

    @classmethod
    def _get_or_load(cls, name: str, fontsize: int) -> ImageFont.FreeTypeFont:
        key = f"{name}:{fontsize}"
        if key not in cls._fonts:
            cls._fonts[key] = ImageFont.truetype(
                str(FONTS_DIR / name), fontsize, encoding="utf-8"
            )
        return cls._fonts[key]


class Handle2Renderer:
    COLOR_CORRECT = "#1d9c9c"
    COLOR_EXIST = "#de7525"
    COLOR_WRONG_CHAR = "#5d6673"
    COLOR_BG = "#ffffff"
    COLOR_BLOCK = "#f7f8f9"
    COLOR_HINT_REVEALED = "#2e7d32"
    COLOR_HINT_BG = "#e8f5e9"
    COLOR_HINT_HIDDEN_BG = "#f5f5f5"

    def __init__(self, game):
        self.game = game
        self._part_cursor: dict[int, int] = {}

    def draw(self) -> BytesIO:
        total_rows = self.game.get_total_attempts()
        rows = min(total_rows + 1, self.game.times)
        color_bar_h = 8
        hint_h = 110
        hint_padding = 15
        block_w, block_h = 140, 150
        block_pad = (8, 8)
        padding = (20, 20)
        board_w = 4 * block_w + 3 * block_pad[0] + 2 * padding[0]
        board_h = rows * block_h + (rows - 1) * block_pad[1] + 2 * padding[1]
        total_h = color_bar_h + hint_h + hint_padding + board_h
        image = Image.new("RGB", (board_w, total_h), self.COLOR_BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, board_w, color_bar_h], fill=self.game.color_bar)
        self._draw_hints(draw, board_w, hint_h, offset_y=color_bar_h)
        game_y = color_bar_h + hint_h + hint_padding
        for row_index, result in enumerate(self.game.guessed_results):
            for column_index in range(4):
                x = padding[0] + column_index * (block_w + block_pad[0])
                y = game_y + padding[1] + row_index * (block_h + block_pad[1])
                self._draw_block(
                    draw, result["chars"][column_index], x, y, block_w, block_h
                )
        for row_index in range(len(self.game.guessed), rows):
            for column_index in range(4):
                x = padding[0] + column_index * (block_w + block_pad[0])
                y = game_y + padding[1] + row_index * (block_h + block_pad[1])
                block = Image.new("RGB", (block_w, block_h), self.COLOR_BLOCK)
                block_draw = ImageDraw.Draw(block)
                block_draw.rectangle(
                    [0, 0, block_w - 1, block_h - 1], outline="#ddd", width=1
                )
                image.paste(block, (x, y))
        output = BytesIO()
        image.save(output, format="png")
        output.seek(0)
        return output

    def _draw_hints(
        self, draw: ImageDraw.Draw, total_w: int, hint_h: int, offset_y: int
    ):
        font_struct = self._default_font(12)
        font_label = self._default_font(11)
        char_w = total_w // 4
        box_w, box_h = 100, 70
        for char_index in range(4):
            cx = char_w * char_index + char_w // 2
            struct_text = self.game.answer_structures[char_index]
            text_w = font_struct.getlength(struct_text)
            draw.text(
                (cx - text_w / 2, 3 + offset_y),
                struct_text,
                fill="#999",
                font=font_struct,
            )
            box_x = cx - box_w // 2
            box_y = 18 + offset_y
            sub = Image.new("RGB", (box_w, box_h), self.COLOR_BG)
            sub_draw = ImageDraw.Draw(sub)
            sub_draw.rectangle([0, 0, box_w - 1, box_h - 1], outline="#ccc", width=1)
            revealed = {
                hint["part_index"]: hint["part"]
                for hint in self.game.hints
                if hint["char_index"] == char_index
            }
            self._part_cursor[char_index] = 0
            self._draw_struct_node(
                sub_draw,
                self.game.answer_nodes[char_index],
                2,
                2,
                box_w - 4,
                box_h - 4,
                char_index,
                revealed,
                14,
                0,
                max_depth=2,
            )
            draw._image.paste(sub, (box_x, box_y))
            label = f"第{char_index + 1}字"
            label_w = font_label.getlength(label)
            draw.text(
                (cx - label_w / 2, box_y + box_h + 2),
                label,
                fill="#aaa",
                font=font_label,
            )

    def _draw_struct_node(
        self,
        draw: ImageDraw.Draw,
        node: IDSNode,
        x: int,
        y: int,
        w: int,
        h: int,
        char_index: int,
        revealed: dict[int, str],
        fontsize: int,
        depth: int,
        max_depth: int = 1,
    ):
        outline_colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#00BCD4"]
        if depth >= max_depth and not node.is_leaf:
            self._draw_flat_components(
                draw, node, x, y, w, h, char_index, revealed, fontsize
            )
            return
        if node.is_leaf:
            self._draw_leaf(
                draw, node.char or "", x, y, w, h, char_index, revealed, fontsize
            )
            return
        draw.rectangle(
            [x, y, x + w - 1, y + h - 1],
            outline=outline_colors[depth % len(outline_colors)],
            width=1,
        )
        op_type = IDS_OPERATORS.get(node.operator, {}).get("type", "horizontal")
        children = node.children
        if op_type in ("horizontal", "overlay"):
            self._draw_horizontal(
                draw, children, x, y, w, h, char_index, revealed, fontsize, depth
            )
        elif op_type == "vertical":
            self._draw_vertical(
                draw, children, x, y, w, h, char_index, revealed, fontsize, depth
            )
        elif op_type.startswith("surround") or op_type == "surround":
            self._draw_surround(
                draw, node, x, y, w, h, char_index, revealed, fontsize, depth
            )

    def _draw_flat_components(
        self, draw, node, x, y, w, h, char_index, revealed, fontsize
    ):
        components = node.get_components()
        gap = 2
        slot_w = min(28, (w - 4) // max(len(components), 1))
        start_x = x + (w - slot_w * len(components)) / 2
        slot_y = y + (h - 18) / 2
        for offset, component in enumerate(components):
            part_index = self._next_part_index(char_index)
            slot_x = start_x + offset * slot_w
            self._draw_part_slot(
                draw,
                component,
                part_index,
                revealed,
                slot_x,
                slot_y,
                slot_w,
                gap,
                min(fontsize, 12),
            )

    def _draw_leaf(self, draw, text, x, y, w, h, char_index, revealed, fontsize):
        part_index = self._next_part_index(char_index)
        if part_index in revealed:
            shown = revealed[part_index]
            font = FontManager.get_font(shown, fontsize)
            fill, outline, color = (
                self.COLOR_HINT_BG,
                "#a5d6a7",
                self.COLOR_HINT_REVEALED,
            )
        else:
            shown = "?"
            font = self._default_font(fontsize)
            fill, outline, color = self.COLOR_HINT_HIDDEN_BG, "#ddd", "#ccc"
        draw.rectangle([x + 1, y + 1, x + w - 2, y + h - 2], fill=fill, outline=outline)
        bbox = font.getbbox(shown)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text(
            (x + (w - text_w) / 2, y + (h - text_h) / 2 - bbox[1]),
            shown,
            fill=color,
            font=font,
        )

    def _draw_part_slot(
        self, draw, component, part_index, revealed, x, y, slot_w, gap, fontsize
    ):
        if part_index in revealed:
            text = revealed[part_index]
            font = FontManager.get_font(text, fontsize)
            fill, outline, color = (
                self.COLOR_HINT_BG,
                "#a5d6a7",
                self.COLOR_HINT_REVEALED,
            )
        else:
            text = "?"
            font = self._default_font(fontsize)
            fill, outline, color = self.COLOR_HINT_HIDDEN_BG, "#ddd", "#ccc"
        draw.rectangle([x, y, x + slot_w - gap, y + 18], fill=fill, outline=outline)
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        draw.text((x + (slot_w - gap - text_w) / 2, y + 1), text, fill=color, font=font)

    def _draw_horizontal(
        self, draw, children, x, y, w, h, char_index, revealed, fontsize, depth
    ):
        gap = 2
        child_w = (w - 2 - gap * (len(children) - 1)) // max(len(children), 1)
        for child_index, child in enumerate(children):
            self._draw_struct_node(
                draw,
                child,
                x + 1 + child_index * (child_w + gap),
                y + 1,
                child_w,
                h - 2,
                char_index,
                revealed,
                fontsize,
                depth + 1,
            )

    def _draw_vertical(
        self, draw, children, x, y, w, h, char_index, revealed, fontsize, depth
    ):
        gap = 2
        child_h = (h - 2 - gap * (len(children) - 1)) // max(len(children), 1)
        for child_index, child in enumerate(children):
            self._draw_struct_node(
                draw,
                child,
                x + 1,
                y + 1 + child_index * (child_h + gap),
                w - 2,
                child_h,
                char_index,
                revealed,
                fontsize,
                depth + 1,
            )

    def _draw_surround(
        self, draw, node, x, y, w, h, char_index, revealed, fontsize, depth
    ):
        if len(node.children) != 2:
            self._draw_horizontal(
                draw, node.children, x, y, w, h, char_index, revealed, fontsize, depth
            )
            return
        outer, inner = node.children
        ox, oy, ow, oh, ix, iy, iw, ih = self._get_surround_split(
            node.operator, x + 1, y + 1, w - 2, h - 2
        )
        self._draw_struct_node(
            draw, outer, ox, oy, ow, oh, char_index, revealed, fontsize, depth + 1
        )
        self._draw_struct_node(
            draw, inner, ix, iy, iw, ih, char_index, revealed, fontsize, depth + 1
        )

    def _draw_block(
        self, draw_main: ImageDraw.Draw, ch_data: dict, x: int, y: int, w: int, h: int
    ):
        block = Image.new("RGB", (w, h), self.COLOR_BG)
        draw = ImageDraw.Draw(block)
        char_state = ch_data["char_state"]
        char_text = ch_data["char"]
        parts = ch_data["parts"]
        if char_state == "correct":
            draw.rectangle([0, 0, w, h], fill=self.COLOR_CORRECT)
        else:
            draw.rectangle([0, 0, w, h], fill=self.COLOR_BLOCK)
            draw.rectangle([0, 0, w - 1, h - 1], outline="#ddd", width=1)
        font_char = FontManager.get_font(char_text, 50)
        char_color = (
            "#ffffff"
            if char_state == "correct"
            else self.COLOR_EXIST
            if char_state == "exist"
            else self.COLOR_WRONG_CHAR
        )
        bbox = font_char.getbbox(char_text)
        draw.text(
            ((w - (bbox[2] - bbox[0])) / 2, (h - (bbox[3] - bbox[1])) / 3 - bbox[1]),
            char_text,
            fill=char_color,
            font=font_char,
        )
        part_fontsize = 14
        part_y = h * 0.7
        total_w = (
            sum(
                FontManager.get_font(part["part"], part_fontsize).getlength(
                    part["part"]
                )
                + 8
                for part in parts
            )
            - 8
        )
        part_x = (w - total_w) / 2
        for part in parts:
            part_x = self._draw_guess_part(
                draw, part, char_state, part_x, part_y, part_fontsize
            )
        draw_main._image.paste(block, (x, y))

    def _draw_guess_part(self, draw, part, char_state, x, y, fontsize):
        text = part["part"]
        state = part["state"]
        font = FontManager.get_font(text, fontsize)
        width = font.getlength(text) + 6
        if state == "correct":
            bg = "#43b7b7" if char_state == "correct" else self.COLOR_CORRECT
            color = "#ffffff"
            border = "#5ecfcf" if char_state == "correct" else self.COLOR_CORRECT
        elif state == "exist":
            bg = border = self.COLOR_EXIST
            color = "#ffffff"
        else:
            bg = "#f0f0f0"
            color = "#bbb"
            border = "#ddd"
        draw.rectangle([x, y, x + width, y + 20], fill=bg, outline=border)
        draw.text((x + 3, y + 2), text, fill=color, font=font)
        return x + width + 2

    def _next_part_index(self, char_index: int) -> int:
        part_index = self._part_cursor.get(char_index, 0)
        self._part_cursor[char_index] = part_index + 1
        return part_index

    @staticmethod
    def _default_font(size: int) -> ImageFont.FreeTypeFont:
        return FontManager._get_or_load("NotoSerifSC-Regular.otf", size)

    @staticmethod
    def _get_surround_split(op: str, x: int, y: int, w: int, h: int):
        side_w = max(w // 3, 16)
        side_h = max(h // 3, 16)
        corner_w = max(w // 2, 20)
        corner_h = max(h // 2, 20)
        if op == "⿸":
            return (
                x,
                y,
                corner_w,
                corner_h,
                x + side_w,
                y + side_h,
                w - side_w,
                h - side_h,
            )
        if op == "⿹":
            return (
                x + w - corner_w,
                y,
                corner_w,
                corner_h,
                x,
                y + side_h,
                w - side_w,
                h - side_h,
            )
        if op == "⿺":
            return (
                x,
                y + h - corner_h,
                corner_w,
                corner_h,
                x + side_w,
                y,
                w - side_w,
                h - side_h,
            )
        if op == "⿵":
            return (x, y, w, side_h, x, y + side_h, w, h - side_h)
        if op == "⿶":
            return (x, y + h - side_h, w, side_h, x, y, w, h - side_h)
        if op == "⿷":
            return (x, y, side_w, h, x + side_w, y, w - side_w, h)
        if op == "⿴":
            margin = 4
            return (x, y, w, h, x + margin, y + margin, w - margin * 2, h - margin * 2)
        return (x + w - side_w, y, side_w, h, x, y, w - side_w, h)
