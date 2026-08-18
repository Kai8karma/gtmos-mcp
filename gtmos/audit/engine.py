"""Deterministic CRM record scoring. Pure Python — no LLM, no network.

Canonical contact fields (all optional / None-able strings unless noted):
    email, firstname, lastname, company, jobtitle, phone,
    lifecyclestage, last_activity_date (ISO 8601 string or None),
    owner, createdate
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

JUNK_VALUES = {"test", "asdf", "n/a", "na", "unknown", "none", "-", "null", "n.a.", "xx", "xxx", ""}

DISPOSABLE_DOMAIN_MARKERS = (
    "mailinator", "tempmail", "temp-mail", "guerrillamail", "yopmail",
    "trashmail", "10minutemail", "throwaway", "fakeinbox", "getnada",
    "discard.email", "sharklasers",
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+$")

ADVANCED_LIFECYCLE_STAGES = {"customer", "opportunity"}

# a record you cannot reach cannot score above this, whatever else it has going
UNREACHABLE_CAP = 45.0

COMPLETENESS_WEIGHTS = {
    "email": 30,
    "company": 20,
    "jobtitle": 15,
    "lastname": 10,
    "phone": 10,
    "owner": 15,
}

RECORD_DIMENSION_WEIGHTS = {
    "completeness": 0.30,
    "validity": 0.25,
    "freshness": 0.25,
    "ownership": 0.10,
    "consistency": 0.10,
}


def is_junk(value: str | None) -> bool:
    """True if value is missing or a known junk placeholder."""
    if value is None:
        return True
    return value.strip().lower() in JUNK_VALUES


def _present(contact: dict, key: str) -> bool:
    return not is_junk(contact.get(key))


def _valid_email(email: str) -> bool:
    if not EMAIL_RE.match(email):
        return False
    local, domain = email.rsplit("@", 1)
    domain = domain.lower()
    if "." not in domain:
        return False
    # placeholder addresses (test@test.com, asdf@example.com) are format-valid but dead
    if local.lower() in JUNK_VALUES or domain.split(".")[0] in JUNK_VALUES | {"example", "test", "email", "domain"}:
        return False
    return not any(marker in domain for marker in DISPOSABLE_DOMAIN_MARKERS)


def _valid_phone(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone)
    return 7 <= len(digits) <= 15


@dataclass
class RecordScore:
    index: int
    email: str | None
    completeness: float
    validity: float
    freshness: float
    ownership: float
    consistency: float
    overall: float
    worst_dimension: str


@dataclass
class DupeCluster:
    kind: str  # "exact_email" | "fuzzy_name_company"
    key: str
    indices: list[int] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    records: list[RecordScore]
    portal_score: float
    dimension_averages: dict[str, float]
    dupe_clusters: list[DupeCluster]
    total_records: int
    record_mean: float = 0.0
    dupe_penalty: float = 0.0
    duped_record_share: float = 0.0
    dead_share: float = 0.0      # records at/below UNREACHABLE_CAP
    at_risk_share: float = 0.0   # records below 70


def score_completeness(contact: dict) -> float:
    total = 0
    for field_name, weight in COMPLETENESS_WEIGHTS.items():
        if _present(contact, field_name):
            total += weight
    return float(total)


def score_validity(contact: dict) -> float:
    weighted_sum = 0.0
    weight_total = 0.0

    email = contact.get("email")
    if not is_junk(email):
        weighted_sum += 50.0 * (100.0 if _valid_email(email.strip()) else 0.0)
        weight_total += 50.0

    phone = contact.get("phone")
    if not is_junk(phone):
        weighted_sum += 25.0 * (100.0 if _valid_phone(phone) else 0.0)
        weight_total += 25.0

    junk_check_fields = ["firstname", "lastname", "company", "jobtitle", "lifecyclestage", "owner"]
    populated = [f for f in junk_check_fields if contact.get(f) is not None and contact.get(f).strip() != ""]
    if populated:
        junk_count = sum(1 for f in populated if is_junk(contact.get(f)))
        junk_score = 100.0 * (1 - junk_count / len(populated))
        weighted_sum += 25.0 * junk_score
        weight_total += 25.0

    if weight_total == 0:
        return 0.0
    return weighted_sum / weight_total


def score_freshness(contact: dict, now: datetime | None = None) -> float:
    raw = contact.get("last_activity_date")
    if not raw:
        return 0.0
    try:
        last_activity = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    days = (reference - last_activity).days
    if days <= 30:
        return 100.0
    if days <= 90:
        return 80.0
    if days <= 180:
        return 55.0
    if days <= 365:
        return 30.0
    return 10.0


def score_ownership(contact: dict) -> float:
    return 100.0 if _present(contact, "owner") else 0.0


def score_consistency(contact: dict, now: datetime | None = None) -> float:
    stage = contact.get("lifecyclestage")
    if is_junk(stage):
        return 0.0
    stage_norm = stage.strip().lower()
    if stage_norm in ADVANCED_LIFECYCLE_STAGES:
        raw = contact.get("last_activity_date")
        if not raw:
            return 20.0
        try:
            last_activity = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return 20.0
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        days = (reference - last_activity).days
        if days > 365:
            return 20.0
    return 100.0


def score_record(contact: dict, index: int, now: datetime | None = None) -> RecordScore:
    dims = {
        "completeness": score_completeness(contact),
        "validity": score_validity(contact),
        "freshness": score_freshness(contact, now),
        "ownership": score_ownership(contact),
        "consistency": score_consistency(contact, now),
    }
    overall = sum(dims[name] * weight for name, weight in RECORD_DIMENSION_WEIGHTS.items())
    # unreachability cap: no working email AND no working phone = commercially
    # dead record, regardless of how complete or consistent its fields are
    email, phone = contact.get("email"), contact.get("phone")
    reachable = (not is_junk(email) and _valid_email(email.strip())) or (
        not is_junk(phone) and _valid_phone(phone)
    )
    if not reachable:
        overall = min(overall, UNREACHABLE_CAP)
    worst_dimension = min(dims, key=dims.get)
    return RecordScore(
        index=index,
        email=contact.get("email"),
        completeness=dims["completeness"],
        validity=dims["validity"],
        freshness=dims["freshness"],
        ownership=dims["ownership"],
        consistency=dims["consistency"],
        overall=overall,
        worst_dimension=worst_dimension,
    )


def _normalize_company(company: str) -> str:
    name = company.strip().lower()
    for suffix in (" inc", " inc.", " llc", " llc.", " ltd", " ltd.", " corp", " corp.", " co", " co."):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def find_dupes(contacts: list[dict]) -> list[DupeCluster]:
    clusters: list[DupeCluster] = []

    by_email: dict[str, list[int]] = {}
    for i, c in enumerate(contacts):
        email = c.get("email")
        if is_junk(email):
            continue
        key = email.strip().lower()
        by_email.setdefault(key, []).append(i)
    for key, indices in by_email.items():
        if len(indices) > 1:
            clusters.append(
                DupeCluster(
                    kind="exact_email",
                    key=key,
                    indices=indices,
                    emails=[contacts[i].get("email") for i in indices],
                )
            )

    by_name_company: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(contacts):
        lastname = c.get("lastname")
        company = c.get("company")
        if is_junk(lastname) or is_junk(company):
            continue
        key = (lastname.strip().lower(), _normalize_company(company))
        by_name_company.setdefault(key, []).append(i)
    for key, indices in by_name_company.items():
        if len(indices) > 1:
            clusters.append(
                DupeCluster(
                    kind="fuzzy_name_company",
                    key=f"{key[0]} / {key[1]}",
                    indices=indices,
                    emails=[contacts[i].get("email") for i in indices],
                )
            )

    return clusters


def run_engine(contacts: list[dict], now: datetime | None = None) -> AuditResult:
    records = [score_record(c, i, now) for i, c in enumerate(contacts)]
    total = len(records)

    if total == 0:
        return AuditResult(records=[], portal_score=0.0, dimension_averages={d: 0.0 for d in RECORD_DIMENSION_WEIGHTS}, dupe_clusters=[], total_records=0)

    record_mean = sum(r.overall for r in records) / total
    dimension_averages = {
        name: sum(getattr(r, name) for r in records) / total for name in RECORD_DIMENSION_WEIGHTS
    }
    dupe_clusters = find_dupes(contacts)

    # dupes are portal-level rot invisible to per-record scores: penalize the
    # portal by the share of records sitting in dupe clusters, capped at 10 pts
    duped_indices = {i for c in dupe_clusters for i in c.indices}
    duped_share = len(duped_indices) / total
    dupe_penalty = min(10.0, duped_share * 25.0)
    portal_score = max(0.0, record_mean - dupe_penalty)

    return AuditResult(
        records=records,
        portal_score=portal_score,
        dimension_averages=dimension_averages,
        dupe_clusters=dupe_clusters,
        total_records=total,
        record_mean=record_mean,
        dupe_penalty=dupe_penalty,
        duped_record_share=duped_share,
        dead_share=sum(1 for r in records if r.overall <= UNREACHABLE_CAP) / total,
        at_risk_share=sum(1 for r in records if r.overall < 70.0) / total,
    )
