"""拆字猜成语游戏规则与匹配逻辑。"""

import random
from collections import Counter
from enum import Enum
from io import BytesIO

from .ids import IDSNode
from .resources import init_data


class GuessResult(Enum):
    WIN = 0
    LOSS = 1
    DUPLICATE = 2
    ILLEGAL = 3
    STROKE_LIMIT = 4


class Handle2Game:
    MAX_ATTEMPTS = 7
    STROKE_THRESHOLD = 20
    HINT_MILESTONES = [3, 5]

    def __init__(self, idiom: str, explanation: str):
        self.parser, self.stroke_data = init_data()
        self.idiom = idiom
        self.explanation = explanation
        self.answer_chars = list(idiom)
        self.answer_nodes = [self.parser.decompose_level1(char) for char in idiom]
        self.answer_components = [node.get_components() for node in self.answer_nodes]
        self.answer_structures = [node.get_structure_name() for node in self.answer_nodes]
        self.answer_max_strokes = max(
            (self.stroke_data.get(char, 0) for char in idiom), default=0
        )
        self.result = f"【成语】：{idiom}\n【释义】：{explanation}"
        self.times = self.MAX_ATTEMPTS
        self.hints: list[dict] = self._generate_initial_hints()
        self.guessed: list[str] = []
        self.guessed_results: list[dict] = []
        self._hint_milestones_reached: set[int] = set()
        self._forced_attempts = 0
        self.color_bar = f"#{random.randint(0, 0xFFFFFF):06x}"

    def guess(self, idiom: str) -> GuessResult | None:
        if idiom in self.guessed:
            return GuessResult.DUPLICATE
        if len(idiom) != 4 or not all("\u4e00" <= char <= "\u9fff" for char in idiom):
            return GuessResult.ILLEGAL
        if self._hits_stroke_limit(idiom):
            return GuessResult.STROKE_LIMIT

        self.guessed.append(idiom)
        guess_nodes = [self.parser.decompose_level1(char) for char in idiom]
        guess_components = [node.get_components() for node in guess_nodes]
        char_results = self._match_all(guess_components, idiom)
        self.guessed_results.append(
            {"idiom": idiom, "chars": char_results, "win": idiom == self.idiom}
        )

        if idiom == self.idiom:
            return GuessResult.WIN
        self._check_progressive_hints()
        self._reveal_new_hint()
        if self.get_total_attempts() >= self.MAX_ATTEMPTS:
            return GuessResult.LOSS
        self._check_progressive_hints()
        return None

    def get_total_attempts(self) -> int:
        return len(self.guessed) + self._forced_attempts

    def request_hint(self) -> dict | None:
        if self.get_total_attempts() >= self.MAX_ATTEMPTS:
            return None
        self._forced_attempts += 1
        # 先检查里程碑；若触发，里程碑揭示的组件即为本此提示结果
        hints_before = len(self.hints)
        self._check_progressive_hints()
        if len(self.hints) > hints_before:
            return self.hints[-1]
        # 未触发里程碑，正常揭示1个
        hint = self._reveal_new_hint()
        if hint is None:
            self._forced_attempts -= 1
            return None
        return hint

    def draw(self) -> BytesIO:
        from .renderer import Handle2Renderer

        return Handle2Renderer(self).draw()

    def to_state(self) -> dict:
        game_over = self.get_total_attempts() >= self.MAX_ATTEMPTS or bool(
            self.guessed and self.guessed[-1] == self.idiom
        )
        won = bool(self.guessed and self.guessed[-1] == self.idiom)
        return {
            "idiom_length": 4,
            "max_attempts": self.MAX_ATTEMPTS,
            "attempts_used": self.get_total_attempts(),
            "guessed_results": self.guessed_results,
            "answer_max_strokes": self.answer_max_strokes,
            "stroke_threshold": self.STROKE_THRESHOLD,
            "game_over": game_over,
            "won": won,
            "answer": self.idiom if game_over else None,
            "explanation": self.explanation if game_over else None,
            "hints": self.hints,
            "answer_structures": self.answer_structures,
            "answer_component_counts": [len(parts) for parts in self.answer_components],
        }

    def _hits_stroke_limit(self, idiom: str) -> bool:
        guess_max_strokes = max(
            (self.stroke_data.get(char, 0) for char in idiom), default=0
        )
        return (
            self.answer_max_strokes < self.STROKE_THRESHOLD
            and guess_max_strokes >= self.STROKE_THRESHOLD
        )

    def _generate_initial_hints(self) -> list[dict]:
        hints = []
        revealed = set()
        for char_index, parts in enumerate(self.answer_components):
            # 独体字（只有一个部件）不自动揭示，否则游戏太简单
            if len(parts) == 1:
                continue
            for part_index, part in enumerate(parts):
                if self.stroke_data.get(part, 99) == 1:
                    hints.append(self._make_hint(char_index, part_index))
                    revealed.add((char_index, part_index))
        if not hints:
            candidate = self._least_stroke_candidate(exclude=revealed)
            if candidate:
                char_index, part_index = candidate
                hints.append(self._make_hint(char_index, part_index))
                revealed.add((char_index, part_index))
        candidate = self._least_stroke_candidate(exclude=revealed)
        if candidate:
            hints.append(self._make_hint(*candidate))
        return hints

    def _check_progressive_hints(self):
        attempts = self.get_total_attempts()
        for milestone in self.HINT_MILESTONES:
            if attempts >= milestone and milestone not in self._hint_milestones_reached:
                self._hint_milestones_reached.add(milestone)
                self._reveal_new_hint()

    def _reveal_new_hint(self) -> dict | None:
        exclude = {(hint["char_index"], hint["part_index"]) for hint in self.hints}
        exclude.update(self._get_found_parts())
        candidate = self._least_stroke_candidate(exclude=exclude)
        if not candidate:
            return None
        hint = self._make_hint(*candidate)
        self.hints.append(hint)
        return hint

    def _least_stroke_candidate(self, exclude: set[tuple[int, int]]) -> tuple[int, int] | None:
        candidates = []
        for char_index, parts in enumerate(self.answer_components):
            for part_index, part in enumerate(parts):
                if (char_index, part_index) not in exclude:
                    candidates.append(
                        (self.stroke_data.get(part, 99), char_index, part_index)
                    )
        if not candidates:
            return None
        _, char_index, part_index = min(candidates)
        return char_index, part_index

    def _make_hint(self, char_index: int, part_index: int) -> dict:
        return {
            "char_index": char_index,
            "part_index": part_index,
            "part": self.answer_components[char_index][part_index],
        }

    def _get_found_parts(self) -> set[tuple[int, int]]:
        found = set()
        for result in self.guessed_results:
            for char_index, char_result in enumerate(result["chars"]):
                for part_index, part in enumerate(char_result["parts"]):
                    if part["state"] == "correct":
                        found.add((char_index, part_index))
        return found

    def _match_all(self, guess_components: list[list[str]], guess_idiom: str) -> list[dict]:
        char_results = [None] * 4
        part_states = [["wrong"] * len(guess_components[index]) for index in range(4)]
        answer_char_remaining = Counter(self.idiom)

        for index in range(4):
            if guess_idiom[index] == self.idiom[index]:
                part_states[index] = ["correct"] * len(guess_components[index])
                char_results[index] = self._make_char_result(
                    guess_idiom[index], "correct", guess_components[index], part_states[index]
                )
                answer_char_remaining[self.idiom[index]] -= 1

        remaining_answer = []
        for char_index, parts in enumerate(self.answer_components):
            if char_results[char_index] is not None:
                continue
            for part_index, part in enumerate(parts):
                remaining_answer.append((char_index, part_index, part))

        for index in range(4):
            if char_results[index] is not None:
                continue
            for part_index, guess_part in enumerate(guess_components[index]):
                for remaining_index, (answer_char_index, _, answer_part) in enumerate(remaining_answer):
                    if answer_char_index == index and guess_part == answer_part:
                        part_states[index][part_index] = "correct"
                        remaining_answer.pop(remaining_index)
                        break

        for index in range(4):
            if char_results[index] is not None:
                continue
            for part_index, guess_part in enumerate(guess_components[index]):
                if part_states[index][part_index] == "correct":
                    continue
                for remaining_index, (_, _, answer_part) in enumerate(remaining_answer):
                    if guess_part == answer_part:
                        part_states[index][part_index] = "exist"
                        remaining_answer.pop(remaining_index)
                        break
            char_state = self._match_char_state(index, guess_idiom, answer_char_remaining)
            char_results[index] = self._make_char_result(
                guess_idiom[index], char_state, guess_components[index], part_states[index]
            )

        return char_results

    def _match_char_state(
        self, index: int, guess_idiom: str, answer_char_remaining: Counter
    ) -> str:
        if guess_idiom[index] == self.idiom[index]:
            return "correct"
        if answer_char_remaining.get(guess_idiom[index], 0) > 0:
            answer_char_remaining[guess_idiom[index]] -= 1
            return "exist"
        return "wrong"

    @staticmethod
    def _make_char_result(
        char: str, char_state: str, components: list[str], states: list[str]
    ) -> dict:
        return {
            "char": char,
            "char_state": char_state,
            "parts": [
                {"part": part, "state": state}
                for part, state in zip(components, states)
            ],
        }
