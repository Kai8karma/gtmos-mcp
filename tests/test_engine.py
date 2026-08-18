"""Offline smoke tests for gtmos.audit.engine — pure-Python scoring, zero network.

The engine module is being built by a parallel agent. We poll for it to
import (up to 90s) instead of hardcoding a fixed startup order, and we
locate the scoring entry point via getattr scanning rather than assuming
a single fixed function name.
"""

from __future__ import annotations

import importlib
import json
import time
import unittest
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "contacts_sample.json"

ENTRY_POINT_PRIORITY = ["score_records", "run_engine", "run", "audit", "score"]

# Attribute/key aliases we accept for each piece of the contract, in
# preference order. Keeps the test resilient to minor naming choices
# without pretending the shape is fully unknown.
RESULT_RECORDS_ALIASES = ["records", "scores", "per_record", "contacts"]
RESULT_PORTAL_ALIASES = ["portal_score", "aggregate_score", "overall_score", "portal"]
RESULT_DUPES_ALIASES = ["dupe_clusters", "duplicates", "dupes", "clusters"]
RECORD_SCORE_ALIASES = ["overall", "score", "total"]
RECORD_EMAIL_ALIASES = ["email"]
RECORD_OWNERSHIP_ALIASES = ["ownership", "owner_score", "ownership_score"]


def _import_with_retry(module_name: str, timeout: float = 90.0, interval: float = 3.0):
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while True:
        try:
            return importlib.import_module(module_name)
        except ImportError as exc:
            last_err = exc
            if time.monotonic() >= deadline:
                raise ImportError(
                    f"{module_name} did not become importable within {timeout}s "
                    f"(last error: {last_err})"
                ) from last_err
            time.sleep(interval)


def _find_entry_point(module):
    for name in ENTRY_POINT_PRIORITY:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate

    scanned = [
        (name, getattr(module, name))
        for name in dir(module)
        if not name.startswith("_")
        and callable(getattr(module, name))
        and any(token in name.lower() for token in ("score", "run", "audit"))
    ]
    if scanned:
        return scanned[0][1]

    public_attrs = [n for n in dir(module) if not n.startswith("_")]
    raise AttributeError(
        f"No scoring entry point found on {module.__name__}. "
        f"Tried {ENTRY_POINT_PRIORITY} plus a scan for callables matching "
        f"score/run/audit. Public attributes available: {public_attrs}"
    )


def _get(obj, aliases: list[str]):
    for name in aliases:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"none of {aliases} found on {obj!r}")


class EngineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = _import_with_retry("gtmos.audit.engine")
        cls.entry = _find_entry_point(cls.engine)
        with open(FIXTURE_PATH, encoding="utf-8") as fh:
            cls.contacts = json.load(fh)
        cls.result = cls.entry(cls.contacts)

    def _records(self):
        return _get(self.result, RESULT_RECORDS_ALIASES)

    def _record_score(self, record):
        return _get(record, RECORD_SCORE_ALIASES)

    def _record_email(self, record):
        return _get(record, RECORD_EMAIL_ALIASES)

    def _record_ownership(self, record):
        return _get(record, RECORD_OWNERSHIP_ALIASES)

    def test_fixture_has_25_records(self):
        self.assertEqual(len(self.contacts), 25)

    def test_portal_score_in_range(self):
        portal = _get(self.result, RESULT_PORTAL_ALIASES)
        self.assertGreater(portal, 0)
        self.assertLess(portal, 100)

    def test_per_record_scores_are_0_to_100(self):
        records = self._records()
        self.assertEqual(len(records), len(self.contacts))
        for record in records:
            score = self._record_score(record)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_dirty_records_score_lower_than_clean_records(self):
        records = self._records()
        by_email = {}
        for record in records:
            by_email.setdefault(self._record_email(record), []).append(record)

        # Junk/invalid emails from the fixture — clearly dirty by contract.
        dirty_emails = ["test@test.com", "asdf@asdf"]
        dirty_scores = [self._record_score(r) for e in dirty_emails for r in by_email.get(e, [])]
        self.assertTrue(dirty_scores, "expected to find the dirty fixture records by email")

        # Fully-populated, recent, owned records — clearly clean by contract.
        clean_emails = [
            "meera.krishnan@apexpetrochem.com",
            "vivaan.chauhan@pinnacleautoparts.com",
            "yusuf.qureshi@titanindustrialgases.com",
        ]
        clean_scores = [self._record_score(r) for e in clean_emails for r in by_email.get(e, [])]
        self.assertTrue(clean_scores, "expected to find the clean fixture records by email")

        self.assertLess(max(dirty_scores), min(clean_scores))

    def test_dupe_pass_finds_at_least_two_clusters(self):
        dupes = _get(self.result, RESULT_DUPES_ALIASES)
        self.assertGreaterEqual(len(dupes), 2)

    def test_ownerless_records_get_zero_ownership(self):
        records = self._records()
        by_email = {}
        for record in records:
            by_email.setdefault(self._record_email(record), []).append(record)

        ownerless_emails = [
            "tanvi.saxena@orionvalveworks.com",
            "harish.menon@sterlingcastparts.com",
            "zoya.ansari@bluecrestenergy.com",
        ]
        found = False
        for email in ownerless_emails:
            for record in by_email.get(email, []):
                found = True
                self.assertEqual(self._record_ownership(record), 0)
        self.assertTrue(found, "expected to find the ownerless fixture records by email")


class CalibrationTests(unittest.TestCase):
    """Pin the scoring calibration rules (unreachability cap, placeholder
    emails, portal dupe penalty) so they cannot silently regress."""

    @classmethod
    def setUpClass(cls):
        cls.engine = _import_with_retry("gtmos.audit.engine")

    def test_placeholder_email_is_invalid(self):
        for email in ("test@test.com", "asdf@example.com", "test@domain.com"):
            self.assertFalse(self.engine._valid_email(email), email)
        self.assertTrue(self.engine._valid_email("jane.doe@acmerobotics.com"))

    def test_unreachable_record_capped(self):
        dead = {
            "email": "test@test.com", "phone": None, "firstname": "Groomed",
            "lastname": "Unreachable", "company": "Acme", "jobtitle": "VP",
            "lifecyclestage": "lead", "owner": "rep@acme.com",
            "last_activity_date": "2026-07-01T00:00:00Z", "createdate": "2026-01-01T00:00:00Z",
        }
        score = self.engine.score_record(dead, 0)
        self.assertLessEqual(score.overall, self.engine.UNREACHABLE_CAP)

    def test_reachable_by_phone_not_capped(self):
        reachable = {
            "email": "test@test.com", "phone": "+1 415 555 0123", "firstname": "Groomed",
            "lastname": "Reachable", "company": "Acme", "jobtitle": "VP",
            "lifecyclestage": "lead", "owner": "rep@acme.com",
            "last_activity_date": "2026-07-01T00:00:00Z", "createdate": "2026-01-01T00:00:00Z",
        }
        score = self.engine.score_record(reachable, 0)
        self.assertGreater(score.overall, self.engine.UNREACHABLE_CAP)

    def test_portal_dupe_penalty_applied(self):
        with open(FIXTURE_PATH, encoding="utf-8") as fh:
            contacts = json.load(fh)
        result = self.engine.run_engine(contacts)
        self.assertGreater(result.dupe_penalty, 0, "dirty fixture has dupes; penalty must bite")
        self.assertAlmostEqual(
            result.portal_score, max(0.0, result.record_mean - result.dupe_penalty), places=6
        )
        self.assertLess(result.portal_score, result.record_mean)


if __name__ == "__main__":
    unittest.main()
