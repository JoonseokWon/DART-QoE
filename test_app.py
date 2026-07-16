import base64
import unittest
from pathlib import Path

from app import packaged_update_path, updater_command


class AutomaticRestartTests(unittest.TestCase):
    def test_packaged_update_uses_side_by_side_executable(self):
        current = Path(r"C:\Tools\DART-QoE.exe")
        self.assertEqual(packaged_update_path(current), Path(r"C:\Tools\DART-QoE.update.exe"))

    def test_updater_waits_replaces_and_relaunches(self):
        current = Path(r"C:\Tools\DART-QoE.exe")
        update = Path(r"C:\Tools\DART-QoE.update.exe")
        command = updater_command(current, update, 1234)
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("Wait-Process -Id 1234", script)
        self.assertIn("Copy-Item -LiteralPath", script)
        self.assertIn("Start-Process -FilePath $current", script)
        self.assertIn(str(update), script)


if __name__ == "__main__":
    unittest.main()
