"""Reject scrape regressions even when old historical metrics still match."""

import copy
import unittest

from recovery.acceptance import compare_checks, scrape_targets


def response(health="up", url="http://node:9100/metrics"):
    """Return an effective target, without runtime network access."""
    return {
        "status": "success",
        "data": {
            "activeTargets": [
                {
                    "scrapePool": "node-exporter",
                    "labels": {"job": "node-exporter", "instance": "node"},
                    "scrapeUrl": url,
                    "health": health,
                }
            ]
        },
    }


def checks(health="up"):
    """Historical data can remain equal after a new scrape failure."""
    return {
        "historical_timestamp": 100,
        "historical_up": [{"value": [100, "1"]}],
        "scrape_targets": scrape_targets(response(health)),
    }


class TargetAcceptanceTests(unittest.TestCase):
    """Current endpoint identity and health are independent acceptance gates."""

    def test_historical_match_does_not_hide_new_down_target(self):
        """Require current health despite matching historical samples."""
        with self.assertRaisesRegex(ValueError, "not UP"):
            compare_checks("prometheus", checks("down"), checks())

    def test_preexisting_down_target_may_recover(self):
        """Allow a target health improvement without weakening identity."""
        compare_checks("prometheus", checks(), checks("down"))

    def test_duplicate_label_identity_with_different_url_is_rejected(self):
        """Reject conflicting endpoints for the same effective series labels."""
        value = response()
        duplicate = copy.deepcopy(value["data"]["activeTargets"][0])
        duplicate["scrapeUrl"] = "http://other:9100/metrics"
        value["data"]["activeTargets"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            scrape_targets(value)

    def test_missing_target_and_changed_endpoint_are_rejected(self):
        """A removed, replaced or redirected target invalidates acceptance."""
        for change in ("missing", "endpoint", "identity"):
            with self.subTest(change=change):
                current = checks()
                if change == "missing":
                    current["scrape_targets"] = []
                elif change == "endpoint":
                    current["scrape_targets"][0]["scrape_url"] = "http://other/metrics"
                else:
                    current["scrape_targets"][0]["identity"] = "another-target"
                with self.assertRaises(ValueError):
                    compare_checks("prometheus", current, checks())

    def test_history_only_baseline_is_rejected(self):
        """Never silently skip the new gate for an older receipt."""
        before = checks()
        del before["scrape_targets"]
        with self.assertRaisesRegex(ValueError, "baseline required"):
            compare_checks("prometheus", checks(), before)

    def test_pending_first_scrape_does_not_pass(self):
        """Wait until the target has completed a real scrape."""
        with self.assertRaisesRegex(ValueError, "no completed scrape"):
            scrape_targets(response("unknown"))
