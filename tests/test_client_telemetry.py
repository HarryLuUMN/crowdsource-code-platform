from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ClientTelemetryTests(unittest.TestCase):
    def test_pagehide_keeps_unacknowledged_events_and_uses_bounded_beacons(self) -> None:
        result = subprocess.run(
            ["node", "tests/client_telemetry_pagehide.js"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
