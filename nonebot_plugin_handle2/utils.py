import json
import random
from pathlib import Path

resource_dir = Path(__file__).parent / "resources"
data_dir = resource_dir / "data"
answer_path = data_dir / "answers.json"
_answers: list[dict[str, str]] | None = None


def load_answers() -> list[dict[str, str]]:
    global _answers
    if _answers is None:
        with answer_path.open("r", encoding="utf-8") as file:
            _answers = json.load(file)
    return _answers


def random_idiom() -> tuple[str, str]:
    answer = random.choice(load_answers())
    return answer["word"], answer["explanation"]
