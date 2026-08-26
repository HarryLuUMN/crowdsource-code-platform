from __future__ import annotations

import unittest
from http import HTTPStatus

from server import compile_source


VALID_PROGRAM = """pattern_width = 6;
pattern_height = 2;
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


class CompilerTests(unittest.TestCase):
    def test_valid_program_generates_knitout_and_metrics(self) -> None:
        status, result = compile_source(VALID_PROGRAM)

        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(result["ok"])
        self.assertIn(";!knitout-", result["knitout"])
        self.assertGreater(result["metrics"]["loops"], 0)
        self.assertGreater(result["metrics"]["courses"], 0)

    def test_invalid_program_returns_compiler_diagnostic(self) -> None:
        status, result = compile_source("with Carrier as c:{ knit ???; }")

        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"]["type"])
        self.assertTrue(result["error"]["message"])

    def test_empty_program_is_rejected_before_compilation(self) -> None:
        status, result = compile_source("   ")

        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertFalse(result["ok"])

    def test_machine_error_preserves_partial_knitout(self) -> None:
        status, result = compile_source(
            """width = 8;
with Carrier as 1:{
  in Leftward direction:{ tuck Front_Needles[0:width:2]; }
  in reverse direction:{ tuck Front_Needles[1:width:2]; }
}
"""
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(result["ok"])
        self.assertIn("partial_knitout", result)
        self.assertIn("inhook 1", result["partial_knitout"])


if __name__ == "__main__":
    unittest.main()
