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


if __name__ == "__main__":
    unittest.main()
