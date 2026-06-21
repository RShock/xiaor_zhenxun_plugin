import random
import time
from dataclasses import dataclass
from enum import Enum
from io import BytesIO

from PIL import Image, ImageDraw
from PIL.Image import Image as IMG

from .config import handle_config
from .utils import (
    FINALS,
    INITIALS,
    U_FINALS,
    get_pinyin,
    get_tone_position,
    legal_idiom,
    load_font,
    save_jpg,
)


class GuessResult(Enum):
    WIN = 0  # 猜出正确成语
    LOSS = 1  # 达到最大可猜次数，未猜出正确成语
    DUPLICATE = 2  # 成语重复
    ILLEGAL = 3  # 成语不合法


class GuessState(Enum):
    CORRECT = 0  # 存在且位置正确
    EXIST = 1  # 存在但位置不正确
    WRONG = 2  # 不存在


@dataclass
class ColorGroup:
    bg_color: str  # 背景颜色
    block_color: str  # 方块颜色
    correct_color: str  # 存在且位置正确时的颜色
    exist_color: str  # 存在但位置不正确时的颜色
    wrong_color_pinyin: str  # 不存在时的颜色
    wrong_color_char: str  # 不存在时的颜色


NORMAL_COLOR = ColorGroup(
    "#ffffff", "#f7f8f9", "#1d9c9c", "#de7525", "#b4b8be", "#5d6673"
)

ENHANCED_COLOR = ColorGroup(
    "#ffffff", "#f7f8f9", "#5ba554", "#ff46ff", "#b4b8be", "#5d6673"
)


class Handle:
    def __init__(
        self,
        idiom: str,
        explanation: str,
        strict: bool = False,
        easy_mode: bool = False,
        precise_mode: bool = False,
    ):
        self.idiom: str = idiom
        self.explanation: str = explanation
        self.strict: bool = strict
        self.easy_mode: bool = easy_mode
        self.precise_mode: bool = precise_mode
        self.start_time: float = time.time()
        self.result = f"【成语】：{idiom}\n【释义】：{explanation}"
        self.pinyin: list[tuple[str, str, str]] = get_pinyin(idiom, precise_mode)
        self.length = 4
        self.times: int = 10
        self.guessed_idiom: list[str] = []
        self.guessed_pinyin: list[list[tuple[str, str, str]]] = []
        self.hint_count: int = 0

        self.block_size = (160, 160)
        self.block_padding = (20, 20)
        self.padding = (40, 40)
        font_size_char = 60
        font_size_pinyin = 30
        font_size_tone = 44
        font_size_hint = 18
        self.font_char = load_font("NotoSerifSC-Regular.otf", font_size_char)
        self.font_pinyin = load_font("NotoSansMono-Regular.ttf", font_size_pinyin)
        self.font_tone = load_font("NotoSansMono-Regular.ttf", font_size_tone)
        self.font_hint = load_font("NotoSansMono-Regular.ttf", font_size_hint)

        self.colors = (
            ENHANCED_COLOR if handle_config.handle_color_enhance else NORMAL_COLOR
        )
        self.color_bar = f"#{random.randint(0x000000, 0xFFFFFF):06x}"

    def guess(self, idiom: str) -> GuessResult | None:
        if self.strict and not legal_idiom(idiom):
            return GuessResult.ILLEGAL
        if idiom in self.guessed_idiom:
            return GuessResult.DUPLICATE
        self.guessed_idiom.append(idiom)
        self.guessed_pinyin.append(get_pinyin(idiom, self.precise_mode))
        if idiom == self.idiom:
            return GuessResult.WIN
        if len(self.guessed_idiom) == self.times:
            return GuessResult.LOSS

    def use_hint(self) -> bool:
        if len(self.guessed_idiom) + self.hint_count >= self.times:
            return False
        self.hint_count += 1
        return True

    def get_total_attempts(self) -> int:
        return len(self.guessed_idiom) + self.hint_count

    def is_expired(self, seconds: float = 300) -> bool:
        return time.time() - self.start_time >= seconds

    def _get_used_pinyin(self) -> tuple[set[str], set[str]]:
        used_initials = set()
        used_finals = set()
        for pinyin in self.guessed_pinyin:
            for p in pinyin:
                if p[0]:
                    used_initials.add(p[0])
                if p[1]:
                    used_finals.add(p[1])
        return used_initials, used_finals

    def _draw_pinyin_hint(self) -> IMG:
        used_initials, used_finals = self._get_used_pinyin()

        answer_initials = set(p[0] for p in self.pinyin if p[0])
        answer_finals = set(p[1] for p in self.pinyin if p[1])

        hint_padding = 20
        item_padding_h = 6
        item_padding_v = 4
        item_spacing = 6
        line_spacing = 8
        section_spacing = 12

        def get_text_width(text: str) -> int:
            return int(self.font_hint.getlength(text))

        def get_text_height() -> int:
            return self.font_hint.getbbox("Ay")[3] - self.font_hint.getbbox("Ay")[1]

        text_h = get_text_height()
        item_h = text_h + item_padding_v * 2

        initials_per_line = 6
        finals_per_line = 8

        initial_lines = []
        current_line = []
        for initial in INITIALS:
            if len(current_line) >= initials_per_line:
                initial_lines.append(current_line)
                current_line = []
            current_line.append(initial)
        if current_line:
            initial_lines.append(current_line)

        display_finals = (
            FINALS if self.precise_mode else [f for f in FINALS if f not in U_FINALS]
        )

        final_lines = []
        current_line = []
        for final in display_finals:
            if len(current_line) >= finals_per_line:
                final_lines.append(current_line)
                current_line = []
            current_line.append(final)
        if current_line:
            final_lines.append(current_line)

        def calc_line_width(line: list[str]) -> int:
            total = 0
            for item in line:
                total += get_text_width(item) + item_padding_h * 2 + item_spacing
            return total - item_spacing if line else 0

        max_initial_width = max(calc_line_width(line) for line in initial_lines)
        max_final_width = (
            max(calc_line_width(line) for line in final_lines) if final_lines else 0
        )
        max_line_width = max(max_initial_width, max_final_width)

        board_w = (
            self.length * self.block_size[0]
            + (self.length - 1) * self.block_padding[0]
            + 2 * self.padding[0]
        )
        hint_w = max(board_w, max_line_width + 2 * hint_padding)

        initial_section_h = len(initial_lines) * (item_h + line_spacing) - line_spacing
        final_section_h = (
            len(final_lines) * (item_h + line_spacing) - line_spacing
            if final_lines
            else 0
        )
        hint_h = (
            initial_section_h + final_section_h + 2 * hint_padding + section_spacing
        )

        hint_img = Image.new("RGB", (hint_w, hint_h), self.colors.bg_color)
        draw = ImageDraw.Draw(hint_img)

        black_color = "#333333"
        gray_color = "#999999"
        green_color = "#2e7d32"

        y = hint_padding

        for line in initial_lines:
            line_width = calc_line_width(line)
            x = (hint_w - line_width) // 2
            for initial in line:
                item_w = get_text_width(initial) + item_padding_h * 2
                if initial in used_initials:
                    if initial in answer_initials:
                        text_color = green_color
                    else:
                        text_color = gray_color
                else:
                    text_color = black_color

                text_x = x + (item_w - get_text_width(initial)) // 2
                text_y = y + (item_h - text_h) // 2
                draw.text(
                    (text_x, text_y), initial, font=self.font_hint, fill=text_color
                )
                x += item_w + item_spacing
            y += item_h + line_spacing

        y += section_spacing - line_spacing

        for line in final_lines:
            line_width = calc_line_width(line)
            x = (hint_w - line_width) // 2
            for final in line:
                item_w = get_text_width(final) + item_padding_h * 2
                if final in used_finals:
                    if final in answer_finals:
                        text_color = green_color
                    else:
                        text_color = gray_color
                else:
                    text_color = black_color

                text_x = x + (item_w - get_text_width(final)) // 2
                text_y = y + (item_h - text_h) // 2
                draw.text((text_x, text_y), final, font=self.font_hint, fill=text_color)
                x += item_w + item_spacing
            y += item_h + line_spacing

        return hint_img

    def draw_block(
        self,
        block_color: str,
        char: str = "",
        char_color: str = "",
        initial: str = "",
        initial_color: str = "",
        final: str = "",
        final_color: str = "",
        tone: str = "",
        tone_color: str = "",
        underline: bool = False,
        underline_color: str = "",
    ) -> IMG:
        block = Image.new("RGB", self.block_size, block_color)
        if not char:
            return block
        draw = ImageDraw.Draw(block)

        char_size = self.font_char.getbbox(char)[2:]
        x = (self.block_size[0] - char_size[0]) / 2
        y = (self.block_size[1] - char_size[1]) / 5 * 3
        draw.text((x, y), char, font=self.font_char, fill=char_color)

        space = 5
        need_space = bool(initial and final)
        py_length = self.font_pinyin.getlength(initial + final)
        if need_space:
            py_length += space
        py_start = (self.block_size[0] - py_length) / 2
        x = py_start
        y = self.block_size[0] / 8
        draw.text((x, y), initial, font=self.font_pinyin, fill=initial_color)
        x += self.font_pinyin.getlength(initial)
        if need_space:
            x += space

        if tone and final:
            tone_pos = get_tone_position(final)
            tone_x = x
            for i in range(tone_pos):
                if i < len(final):
                    tone_x += self.font_pinyin.getlength(final[i])
            tone_char_width = (
                self.font_pinyin.getlength(final[tone_pos])
                if tone_pos < len(final)
                else 0
            )
            tone_x += tone_char_width / 2
            tone_width = self.font_tone.getlength(tone)
            draw.text(
                (tone_x - tone_width / 2, y - 18),
                tone,
                font=self.font_tone,
                fill=tone_color,
            )

        draw.text((x, y), final, font=self.font_pinyin, fill=final_color)

        if underline:
            x = py_start
            py_size = self.font_pinyin.getbbox(initial + final)[2:]
            y = self.block_size[0] / 8 + py_size[1] + 2
            draw.line((x, y, x + py_length, y), fill=underline_color, width=1)
            y += 3
            draw.line((x, y, x + py_length, y), fill=underline_color, width=1)

        return block

    def draw(self) -> BytesIO:
        rows = min(len(self.guessed_idiom) + 1, self.times)
        board_w = self.length * self.block_size[0]
        board_w += (self.length - 1) * self.block_padding[0] + 2 * self.padding[0]
        board_h = rows * self.block_size[1]
        board_h += (rows - 1) * self.block_padding[1] + 2 * self.padding[1]

        color_bar_height = 8

        if self.easy_mode:
            hint_img = self._draw_pinyin_hint()
            total_w = max(board_w, hint_img.width)
            total_h = color_bar_height + hint_img.height + board_h + 10
            board = Image.new("RGB", (total_w, total_h), self.colors.bg_color)
            draw = ImageDraw.Draw(board)
            draw.rectangle([0, 0, total_w, color_bar_height], fill=self.color_bar)
            board.paste(hint_img, ((total_w - hint_img.width) // 2, color_bar_height))
            game_y_offset = color_bar_height + hint_img.height + 10
        else:
            total_h = color_bar_height + board_h
            board = Image.new("RGB", (board_w, total_h), self.colors.bg_color)
            draw = ImageDraw.Draw(board)
            draw.rectangle([0, 0, board_w, color_bar_height], fill=self.color_bar)
            game_y_offset = color_bar_height

        def get_states(guessed: list[str], answer: list[str]) -> list[GuessState]:
            states = []
            incorrect = []
            for i in range(self.length):
                if guessed[i] != answer[i]:
                    incorrect.append(answer[i])
                else:
                    incorrect.append("_")
            for i in range(self.length):
                if guessed[i] == answer[i]:
                    states.append(GuessState.CORRECT)
                elif guessed[i] in incorrect:
                    states.append(GuessState.EXIST)
                    incorrect[incorrect.index(guessed[i])] = "_"
                else:
                    states.append(GuessState.WRONG)
            return states

        def get_pinyin_color(state: GuessState) -> str:
            if state == GuessState.CORRECT:
                return self.colors.correct_color
            elif state == GuessState.EXIST:
                return self.colors.exist_color
            else:
                return self.colors.wrong_color_pinyin

        def get_char_color(state: GuessState) -> str:
            if state == GuessState.CORRECT:
                return self.colors.correct_color
            elif state == GuessState.EXIST:
                return self.colors.exist_color
            else:
                return self.colors.wrong_color_char

        def block_pos(row: int, col: int) -> tuple[int, int]:
            x = (
                board_w
                - (
                    self.length * self.block_size[0]
                    + (self.length - 1) * self.block_padding[0]
                )
            ) // 2
            x += (self.block_size[0] + self.block_padding[0]) * col
            y = (
                self.padding[1]
                + (self.block_size[1] + self.block_padding[1]) * row
                + game_y_offset
            )
            return x, y

        for i in range(len(self.guessed_idiom)):
            idiom = self.guessed_idiom[i]
            pinyin = self.guessed_pinyin[i]
            char_states = get_states(list(idiom), list(self.idiom))
            initial_states = get_states(
                [p[0] for p in pinyin], [p[0] for p in self.pinyin]
            )
            final_states = get_states(
                [p[1] for p in pinyin], [p[1] for p in self.pinyin]
            )
            tone_states = get_states(
                [p[2] for p in pinyin], [p[2] for p in self.pinyin]
            )
            underline_states = get_states(
                [p[0] + p[1] for p in pinyin], [p[0] + p[1] for p in self.pinyin]
            )
            for j in range(self.length):
                char = idiom[j]
                i2, f2, t2 = pinyin[j]
                if char == self.idiom[j]:
                    block_color = self.colors.correct_color
                    char_color = initial_color = final_color = tone_color = (
                        self.colors.bg_color
                    )
                    underline = False
                    underline_color = ""
                else:
                    block_color = self.colors.block_color
                    char_color = get_char_color(char_states[j])
                    initial_color = get_pinyin_color(initial_states[j])
                    final_color = get_pinyin_color(final_states[j])
                    tone_color = get_pinyin_color(tone_states[j])
                    underline_color = get_pinyin_color(underline_states[j])
                    underline = underline_color in (
                        self.colors.correct_color,
                        self.colors.exist_color,
                    )
                block = self.draw_block(
                    block_color,
                    char,
                    char_color,
                    i2,
                    initial_color,
                    f2,
                    final_color,
                    t2,
                    tone_color,
                    underline,
                    underline_color,
                )
                board.paste(block, block_pos(i, j))

        for i in range(len(self.guessed_idiom), rows):
            for j in range(self.length):
                block = self.draw_block(self.colors.block_color)
                board.paste(block, block_pos(i, j))

        return save_jpg(board)

    def draw_hint(self) -> BytesIO:
        guessed_char = set("".join(self.guessed_idiom))
        guessed_initial = set()
        guessed_final = set()
        guessed_tone = set()
        for pinyin in self.guessed_pinyin:
            for p in pinyin:
                guessed_initial.add(p[0])
                guessed_final.add(p[1])
                guessed_tone.add(p[2])

        color_bar_height = 8
        board_w = self.length * self.block_size[0]
        board_w += (self.length - 1) * self.block_padding[0] + 2 * self.padding[0]
        board_h = self.block_size[1] + 2 * self.padding[1]
        total_h = color_bar_height + board_h
        board = Image.new("RGB", (board_w, total_h), self.colors.bg_color)
        draw = ImageDraw.Draw(board)
        draw.rectangle([0, 0, board_w, color_bar_height], fill=self.color_bar)

        for i in range(self.length):
            char = self.idiom[i]
            hi, hf, ht = self.pinyin[i]
            color = char_c = initial_c = final_c = tone_c = self.colors.correct_color
            if char not in guessed_char:
                char = "?"
                color = self.colors.block_color
                char_c = self.colors.wrong_color_char
            else:
                char_c = initial_c = final_c = tone_c = self.colors.bg_color
            if hi not in guessed_initial:
                hi = "?"
                initial_c = self.colors.wrong_color_pinyin
            if hf not in guessed_final:
                hf = "?"
                final_c = self.colors.wrong_color_pinyin
            if ht not in guessed_tone:
                ht = "?"
                tone_c = self.colors.wrong_color_pinyin
            block = self.draw_block(
                color, char, char_c, hi, initial_c, hf, final_c, ht, tone_c
            )
            x = self.padding[0] + (self.block_size[0] + self.block_padding[0]) * i
            y = color_bar_height + self.padding[1]
            board.paste(block, (x, y))
        return save_jpg(board)
