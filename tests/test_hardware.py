from __future__ import annotations

import tempfile
import unittest

from amca.hardware import LocalComputeProfile, profile_local_compute


class HardwareProfileTests(unittest.TestCase):
    def test_profile_is_serializable_and_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = profile_local_compute(directory)

        self.assertIsInstance(profile, LocalComputeProfile)
        self.assertGreaterEqual(profile.logical_cores, 1)
        self.assertGreater(profile.free_disk_bytes, 0)
        self.assertIn("recommended_local_tier", profile.to_dict())
        self.assertIn("operating_system", profile.to_json())


if __name__ == "__main__":
    unittest.main()
