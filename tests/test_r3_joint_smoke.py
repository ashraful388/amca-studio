"""Offline guards for the authorized four-cloud plus one-local R3 runner."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from amca.r3_joint_smoke import (
    AUTHORIZED_CLOUD_COST_USD,
    MAX_OUTPUT_TOKENS,
    TABITOKEN_MODELS,
    _cloud_adapters,
)


class R3JointSmokeTests(unittest.TestCase):
    def test_exact_authorized_batch_is_fixed_and_bounded(self):
        environment = {variable: f"secret-{index}" for index, (_, variable, _) in enumerate(TABITOKEN_MODELS)}
        with patch.dict(os.environ, environment, clear=True):
            adapters = _cloud_adapters()
        self.assertEqual(len(adapters), 4)
        self.assertEqual(sum(adapter.estimate_cost(1, MAX_OUTPUT_TOKENS) for adapter in adapters), AUTHORIZED_CLOUD_COST_USD)
        self.assertEqual(
            [adapter.model for adapter in adapters],
            [model for model, _, _ in TABITOKEN_MODELS],
        )
        self.assertEqual(MAX_OUTPUT_TOKENS, 128)

    def test_all_four_credentials_are_required_before_calls_are_built(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "missing process-local"):
                _cloud_adapters()


if __name__ == "__main__":
    unittest.main()
