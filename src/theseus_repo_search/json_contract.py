from __future__ import annotations

import json
from typing import cast


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> object:
    return cast(
        object,
        json.loads(
            text,
            parse_constant=_reject_nonstandard_constant,
            object_pairs_hook=_unique_object,
        ),
    )
