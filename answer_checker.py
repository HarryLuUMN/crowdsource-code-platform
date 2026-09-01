"""Evaluate compiled KnitScript artifacts against the study task."""

from __future__ import annotations

from typing import Any


TASK_ID = "stockinette-swatch-v1"
EXPECTED_NEEDLES = {f"f{index}" for index in range(10)}


def _operation_lines(knitout: str) -> list[list[str]]:
    return [line.split() for line in knitout.splitlines() if line and not line.startswith(";")]


def _knit_rows(operations: list[list[str]]) -> list[list[list[str]]]:
    rows: list[list[list[str]]] = []
    current: list[list[str]] = []
    current_direction: str | None = None
    for operation in operations:
        if not operation or operation[0] != "knit":
            continue
        direction = operation[1]
        if current and direction != current_direction:
            rows.append(current)
            current = []
        current.append(operation)
        current_direction = direction
    if current:
        rows.append(current)
    return rows


def _is_full_front_row(row: list[list[str]]) -> bool:
    return len(row) == 10 and {operation[2] for operation in row} == EXPECTED_NEEDLES


def _result(test_id: str, label: str, passed: bool, success: str, failure: str) -> dict[str, Any]:
    return {
        "id": test_id,
        "label": label,
        "passed": passed,
        "message": success if passed else failure,
    }


def check_stockinette_answer(compile_result: dict[str, Any]) -> dict[str, Any]:
    compiled = compile_result.get("ok") is True
    knitout = compile_result.get("knitout") if compiled else ""
    operations = _operation_lines(knitout if isinstance(knitout, str) else "")

    first_knit_index = next((index for index, operation in enumerate(operations) if operation[0] == "knit"), len(operations))
    release_index = next((index for index, operation in enumerate(operations) if operation[0] == "releasehook"), -1)
    before_first_knit = operations[:first_knit_index]
    before_release = operations[:release_index] if release_index >= 0 else operations
    after_release = operations[release_index + 1 :] if release_index >= 0 else []

    stitch_operations = [operation for operation in operations if operation[0] in {"tuck", "knit"} and len(operation) >= 3]
    used_needles = {operation[2] for operation in stitch_operations}
    cast_on_operations = [
        operation for operation in before_first_knit if operation[0] == "tuck" and len(operation) >= 3
    ]
    all_tuck_operations = [
        operation for operation in operations if operation[0] == "tuck" and len(operation) >= 3
    ]
    cast_on_needles = {operation[2] for operation in cast_on_operations}
    securing_rows = _knit_rows(before_release)
    stockinette_rows = _knit_rows(after_release)
    only_expected_operations = all(
        operation[0] in {"inhook", "tuck", "knit", "releasehook", "outhook"}
        for operation in operations
    )
    alternating_directions = all(
        stockinette_rows[index][0][1] != stockinette_rows[index - 1][0][1]
        for index in range(1, len(stockinette_rows))
    )

    tests = [
        _result(
            "compiles",
            "Program compiles",
            compiled,
            "The compiler produced knitout successfully.",
            "Fix the compiler error before checking the pattern.",
        ),
        _result(
            "needle-width",
            "Uses exactly 10 front-bed needles",
            compiled and used_needles == EXPECTED_NEEDLES,
            "Stitches stay on front needles 0 through 9.",
            "Use every front needle from 0 through 9, and no others.",
        ),
        _result(
            "cast-on",
            "Casts on all 10 stitches",
            compiled
            and cast_on_needles == EXPECTED_NEEDLES
            and len(cast_on_operations) == 10
            and len(all_tuck_operations) == 10,
            "The tuck cast-on covers all 10 needles once.",
            "Before the first knit, tuck once on each front needle from 0 through 9.",
        ),
        _result(
            "secure-yarn",
            "Secures the cast-on before releasehook",
            compiled and release_index >= 0 and len(securing_rows) == 2 and all(_is_full_front_row(row) for row in securing_rows),
            "Two complete rows secure the yarn before releasehook.",
            "Knit two complete 10-stitch rows before calling releasehook.",
        ),
        _result(
            "stockinette-rows",
            "Knits 6 stockinette rows",
            compiled
            and len(stockinette_rows) == 6
            and all(_is_full_front_row(row) for row in stockinette_rows)
            and alternating_directions
            and only_expected_operations,
            "Six complete alternating rows follow releasehook.",
            "After releasehook, knit exactly 6 complete rows and alternate direction each row.",
        ),
    ]
    passed_count = sum(test["passed"] for test in tests)
    return {
        "task_id": TASK_ID,
        "passed": passed_count == len(tests),
        "passed_count": passed_count,
        "total_count": len(tests),
        "tests": tests,
    }
