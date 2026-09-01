from __future__ import annotations

import unittest

from answer_checker import check_stockinette_answer
from server import compile_source


CORRECT_SOLUTION = """pattern_width = 10;
pattern_height = 6;
c = 1;
with Carrier as c:{
  in Leftward direction:{ tuck Front_Needles[1:pattern_width:2]; }
  in reverse direction:{ tuck Front_Needles[0:pattern_width:2]; }
  in reverse direction:{ knit Loops; }
  in reverse direction:{ knit Loops; }
  releasehook;
  for row in range(pattern_height):{
    in reverse direction:{ knit Loops; }
  }
}
"""


class AnswerCheckerTests(unittest.TestCase):
    def test_correct_stockinette_solution_passes_every_requirement(self) -> None:
        _status, compile_result = compile_source(CORRECT_SOLUTION)

        check = check_stockinette_answer(compile_result)

        self.assertTrue(check["passed"])
        self.assertEqual(5, check["passed_count"])
        self.assertEqual(5, check["total_count"])
        self.assertTrue(all(test["passed"] for test in check["tests"]))

    def test_extra_stitch_operations_cannot_hide_between_valid_rows(self) -> None:
        _status, compile_result = compile_source(CORRECT_SOLUTION)
        compile_result["knitout"] = compile_result["knitout"].replace(
            "releasehook 1\n",
            "releasehook 1\ntuck + f0 1\n",
            1,
        )

        check = check_stockinette_answer(compile_result)

        self.assertFalse(check["passed"])
        cast_on_check = next(test for test in check["tests"] if test["id"] == "cast-on")
        self.assertFalse(cast_on_check["passed"])

    def test_transfers_cannot_change_the_fabric_after_valid_rows(self) -> None:
        _status, compile_result = compile_source(CORRECT_SOLUTION)
        compile_result["knitout"] = compile_result["knitout"].replace(
            "outhook 1",
            "xfer f0 b0\nouthook 1",
            1,
        )

        check = check_stockinette_answer(compile_result)

        self.assertFalse(check["passed"])
        row_check = next(test for test in check["tests"] if test["id"] == "stockinette-rows")
        self.assertFalse(row_check["passed"])


if __name__ == "__main__":
    unittest.main()
