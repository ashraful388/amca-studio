"""Gate-2 follow-up (manager decision 2b): deterministic proof that the
governed-browser registry offers Microsoft Edge, Google Chrome, and Mozilla
Firefox, with Edge as the intended default.

The Browser panel's native <select> popup cannot be captured by UI
automation (not exposed to UIA; screen capture of the popup failed), so the
option list is proven at the registry level that drives the control. The
native-popup automation limitation is recorded in the Gate-2 evidence; a
manual visual check by the owner remains a five-second sanity pass.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.local_tools import GOVERNED_BROWSERS


class GovernedBrowserRegistryTests(unittest.TestCase):
    """The Browser panel select is driven by this registry: its keys are the
    options the user is offered, in presentation order."""

    def test_registry_offers_edge_chrome_and_firefox(self) -> None:
        self.assertGreaterEqual(
            set(GOVERNED_BROWSERS), {"edge", "chrome", "firefox"}
        )

    def test_display_names_are_the_human_browser_names(self) -> None:
        self.assertEqual(GOVERNED_BROWSERS["edge"]["display_name"], "Microsoft Edge")
        self.assertEqual(GOVERNED_BROWSERS["chrome"]["display_name"], "Google Chrome")
        self.assertEqual(GOVERNED_BROWSERS["firefox"]["display_name"], "Mozilla Firefox")

    def test_edge_is_the_intended_default(self) -> None:
        # The Browser panel's select defaults to Edge; the registry's first
        # entry is the default the product presents.
        self.assertEqual(next(iter(GOVERNED_BROWSERS)), "edge")

    def test_every_registry_entry_pins_install_candidates(self) -> None:
        # A governed browser is pinned to a known install path so an approval
        # can never be re-targeted at a different binary.
        for key, entry in GOVERNED_BROWSERS.items():
            self.assertTrue(entry["candidates"], f"{key} has no pinned candidates")
            self.assertTrue(entry["display_name"], f"{key} has no display name")

    def test_firefox_is_launch_only_without_dom_capture(self) -> None:
        # Firefox cannot dump the DOM to stdout; the governed launcher keeps
        # it available while skipping the DOM-capture path.
        self.assertFalse(GOVERNED_BROWSERS["firefox"].get("supports_dom_dump", True))


if __name__ == "__main__":
    unittest.main()
