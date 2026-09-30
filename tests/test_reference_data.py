"""Reference data and the indicators it unblocks.

The dangerous failure this feature can have is not a missed match. It is reporting clean
screening to a bank that never loaded a list. Most of these tests exist to hold that line.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import reference as ref
from services.analytics_service.app.detection.features import (
    COMPUTABLE, NEEDS_EXTERNAL_DATA, NEEDS_REFERENCE_DATA,
)
from services.analytics_service.app.detection.graph_features import (
    hour_deviation, hour_profiles,
)


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.reference_entries WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_lists WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _load(client, token_for, tid, kind="sanctions", version="UNSC 2026-07-31",
          entries=None, role="risk_manager", activate=True):
    return client.post(f"/analytics/{tid}/reference/{kind}", headers=token_for(role),
                       json={"version": version, "source": "UNSC consolidated",
                             "activate": activate,
                             "entries": entries or [
                                 {"key": "Mohammed Ali Hassan",
                                  "attributes": {"ref": "QDi.001"}},
                                 {"key": "Ravi Traders Private Limited",
                                  "attributes": {"ref": "IN-042"}}]})


# ----------------------------------------------- the absent-list invariant
def test_an_unloaded_list_leaves_the_indicator_unmeasured_not_clean(analytics_client,
                                                                    token_for, tid):
    """Reporting "no sanctions matches" to a bank with no list is the worst outcome here."""
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    assert screening["availability"]["sanctions"].loaded is False
    # None, not 0.0. A caller that treated this as a score would report clean screening.
    assert ref.screen_name(screening, "Mohammed Ali Hassan") is None
    assert ref.screen_collateral(screening, "ASSET-1") is None


def test_an_empty_list_is_refused(analytics_client, token_for, tid):
    """An empty list going active would report clean screening across the whole tenant."""
    r = analytics_client.post(f"/analytics/{tid}/reference/sanctions",
                              headers=token_for("risk_manager"),
                              json={"version": "v0", "entries": []})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_list"


def test_once_loaded_the_indicator_becomes_measurable(analytics_client, token_for, tid):
    assert _load(analytics_client, token_for, tid).status_code == 200
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    assert screening["availability"]["sanctions"].loaded is True
    hit = ref.screen_name(screening, "MOHAMMED ALI HASSAN")
    assert hit is not None and hit[0] == 1.0
    # A non-match now scores zero, which is a real measurement rather than an absence.
    clean = ref.screen_name(screening, "Completely Different Person")
    assert clean is not None and clean[0] == 0.0


# ----------------------------------------------- matching behaviour
@pytest.mark.parametrize("a,b,expect", [
    ("Mohammed Ali Hassan", "Hassan Mohammed Ali", 1.0),      # reordered
    ("M/S Ravi Traders Pvt Ltd", "Ravi Traders", 1.0),        # corporate noise
    ("Rāvi Traders", "Ravi Traders", 1.0),                    # accents
    ("Ravi Kumar", "Suresh Kumar", 0.0),                      # one common token only
    ("Ravi Traders", "", 0.0),
])
def test_name_matching_handles_real_list_shapes(a, b, expect):
    assert ref.name_score(a, b) == expect


def test_a_single_shared_common_surname_is_not_a_match():
    """Otherwise every Kumar in the book matches every designated Kumar."""
    assert ref.name_score("Ravi Kumar Sharma", "Anil Kumar Verma") == 0.0


# ----------------------------------------------- versioning
def test_loading_a_new_version_supersedes_the_old_one(analytics_client, token_for, tid):
    _load(analytics_client, token_for, tid, version="v1")
    _load(analytics_client, token_for, tid, version="v2",
          entries=[{"key": "Someone Else", "attributes": {}}])
    st = analytics_client.get(f"/analytics/{tid}/reference",
                              headers=token_for("risk_manager")).json()
    active = [v for v in st["versions"] if v["active"]]
    assert len(active) == 1, "more than one version of a list was active"
    assert active[0]["version"] == "v2"
    # The superseded version is retained: an alert raised last month has to be
    # explainable against the list that actually matched it.
    assert {v["version"] for v in st["versions"]} == {"v1", "v2"}


def test_a_version_can_be_rolled_back(analytics_client, token_for, tid):
    first = _load(analytics_client, token_for, tid, version="v1").json()
    _load(analytics_client, token_for, tid, version="v2",
          entries=[{"key": "Someone Else", "attributes": {}}])
    r = analytics_client.post(
        f"/analytics/{tid}/reference/sanctions/activate/{first['id']}",
        headers=token_for("risk_manager"))
    assert r.status_code == 200
    st = analytics_client.get(f"/analytics/{tid}/reference",
                              headers=token_for("risk_manager")).json()
    assert [v["version"] for v in st["versions"] if v["active"]] == ["v1"]


def test_reloading_the_same_version_is_refused(analytics_client, token_for, tid):
    _load(analytics_client, token_for, tid, version="v1")
    r = _load(analytics_client, token_for, tid, version="v1")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "version_exists"


def test_the_load_is_checksummed(analytics_client, token_for, tid):
    """So an inspection can prove which file was screened against."""
    body = _load(analytics_client, token_for, tid).json()
    assert len(body["checksum"]) == 64
    assert body["entry_count"] == 2


def test_an_analyst_may_not_load_a_screening_list(analytics_client, token_for, tid):
    assert _load(analytics_client, token_for, tid, role="analyst").status_code == 403


def test_an_unknown_list_kind_is_refused(analytics_client, token_for, tid):
    r = _load(analytics_client, token_for, tid, kind="horoscopes")
    assert r.status_code == 400


# ----------------------------------------------- the dormant register
def test_a_blocked_indicator_says_which_list_is_missing(analytics_client, token_for,
                                                        tid):
    """"CPT-02 never fired" and "CPT-02 cannot fire" are different findings."""
    d = analytics_client.get(f"/analytics/{tid}/dormant-rules",
                             headers=token_for("supervisor")).json()
    if not d.get("available"):
        pytest.skip("rule catalogue unavailable in this environment")
    blocked = {r["rule_id"]: r.get("blocked_by") for r in d["dormant"]}
    if "CPT-02" in blocked:
        assert blocked["CPT-02"], "CPT-02 is dormant but does not say why"
        assert "sanctions" in blocked["CPT-02"].lower()
    # A rule that is dormant for ordinary reasons must not claim to be blocked.
    for rid, why in blocked.items():
        if rid not in NEEDS_REFERENCE_DATA:
            assert not why, f"{rid} wrongly reported as blocked by reference data"


def test_the_reference_status_lists_what_each_kind_feeds(analytics_client, token_for,
                                                         tid):
    st = analytics_client.get(f"/analytics/{tid}/reference",
                              headers=token_for("supervisor")).json()
    kinds = {k["kind"]: k for k in st["kinds"]}
    assert "CPT-02" in kinds["sanctions"]["feeds"]
    assert "CPT-03" in kinds["cersai_charges"]["feeds"]
    assert kinds["sanctions"]["loaded"] is False
    assert kinds["sanctions"]["why_unavailable"]


# --------------------------------------------------------- BR-509: CFR as a source
def test_cfr_is_a_loadable_reference_kind_feeding_cpt_02(analytics_client, token_for,
                                                          tid):
    """No route change was needed for this - the whole mechanism is data-driven off
    reference_model.LIST_KINDS, same as sanctions/PEP/CERSAI."""
    st = analytics_client.get(f"/analytics/{tid}/reference",
                              headers=token_for("supervisor")).json()
    kinds = {k["kind"]: k for k in st["kinds"]}
    assert "cfr" in kinds
    assert "CPT-02" in kinds["cfr"]["feeds"]
    assert kinds["cfr"]["loaded"] is False
    assert kinds["cfr"]["why_unavailable"]


def test_cfr_stays_honestly_unloaded_until_rbi_grants_access(analytics_client,
                                                              token_for, tid):
    """RBI does not publish CFR as a bulk feed the way sanctions lists are - this must
    read as 'unmeasurable', not 'no CFR hits found'."""
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    assert screening["availability"]["cfr"].loaded is False
    assert "no" in screening["availability"]["cfr"].why_unavailable.lower()


def test_a_cfr_hit_is_screened_through_the_same_path_as_sanctions(analytics_client,
                                                                   token_for, tid):
    """Loading only CFR (no sanctions/PEP/negative list) still makes CPT-02
    measurable and still surfaces the match - the indicator blends every name-matched
    source, and CFR is now one of them."""
    r = _load(analytics_client, token_for, tid, kind="cfr", version="CFR-2026-08-01",
             entries=[{"key": "Ramesh Kumar Enterprises",
                       "attributes": {"reported_by": "Other Bank Ltd."}}])
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    assert screening["availability"]["cfr"].loaded is True

    hit = ref.screen_name(screening, "Ramesh Kumar Enterprises")
    assert hit is not None
    score, meta = hit
    assert score >= 0.85
    assert meta["list"] == "cfr"


# ----------------------------------------------- the two that needed no feed
def test_lay03_and_chn03_are_no_longer_declared_as_needing_external_data():
    """Both were mislabelled: a cycle is in our own graph, and an hour is on the row."""
    assert "LAY-03" in COMPUTABLE and "CHN-03" in COMPUTABLE
    assert "LAY-03" not in NEEDS_EXTERNAL_DATA
    assert "CHN-03" not in NEEDS_EXTERNAL_DATA
    # What genuinely still needs source-system records the platform cannot yet take.
    # The loan-conduct indicators moved to NEEDS_CBS_FEED when BR-211 gave them an
    # intake path - they are no longer "cannot be measured", but "waiting for a feed",
    # and the difference is what a bank can act on.
    from services.analytics_service.app.detection.features import NEEDS_CBS_FEED

    # CHN-04/CHN-05 (device-posture / behavioural-biometric scores) joined this set with
    # the AI/ML roadmap Phase 1 catalogue additions (BRD OD-06/s19) - measured the moment
    # a transaction carries the score, unmeasurable today because no tenant has that
    # provider integration wired up yet, the same honest gap CHN-02 is already in.
    assert set(NEEDS_EXTERNAL_DATA) == {
        "SME-03", "CHN-02", "CHN-04", "CHN-05", "TBM-01", "TBM-02", "TBM-03"}
    # CBS-04 (cheque returns) and CBS-05 (overdraft/cash-credit breach) joined this set
    # the same way BEH-02/03 and CBS-01/02/03 did: neither is visible on a payment rail,
    # both close a real gap against RBI's own 2016 EWS annexure (item 2, cheque bouncing,
    # for CBS-04 - see docs/rbi_ews_mapping.py).
    assert set(NEEDS_CBS_FEED) == {
        "BEH-02", "BEH-03", "CBS-01", "CBS-02", "CBS-03", "CBS-04", "CBS-05"}
    assert all(NEEDS_EXTERNAL_DATA.values()), "an indicator is unmeasurable with no reason"
    assert all(NEEDS_CBS_FEED.values()), "a feed-backed indicator names no feed"


def test_lay03_compares_below_its_threshold_not_above():
    """Its own reason says "within 3 hops", and within is at most.

    On the gte default this fired on 82% of accounts, because nearly every account
    eventually sits on some long cycle. A tight 2-3 hop round trip is the layering shape;
    a five-hop one is ordinary commerce.
    """
    from services.config_service.app.ews_catalogue import comparator_for

    assert comparator_for("LAY-03") == "lte"


def test_an_account_with_no_cycle_produces_no_lay03_observation():
    """"No cycle" is not "a cycle of length zero".

    LAY-03 reads "value returned to origin *within* 3 hops". Emitting 0 for an account
    with no circular flow would fire the rule on the entire bank the moment anyone set
    the comparator to lte - which is exactly what the rule's own wording implies.
    """
    from services.analytics_service.app.detection.features import AccountContext, observe

    txn = {"ts": datetime.now(timezone.utc), "amount_paise": 1000, "rail": "UPI",
           "debtor_account": "AC-NO-CYCLE", "creditor_account": "AC-OTHER",
           "device_id": ""}
    out = observe(txn, AccountContext(), {}, txn["ts"], cycles={})
    assert "LAY-03" not in out, "an account with no cycle was given an observation"

    on_cycle = observe(txn, AccountContext(), {}, txn["ts"],
                       cycles={"AC-NO-CYCLE": 3})
    assert on_cycle["LAY-03"] == 3.0


def test_the_hour_profile_treats_midnight_as_circular():
    """23:00 and 01:00 are two hours apart. Treating the hour as a plain number makes
    every late-night account look permanently anomalous."""
    # An account that transacts around midnight.
    profile = (23.5, 1.0, 40)
    near = hour_deviation(profile, datetime(2026, 8, 3, 0, 30, tzinfo=timezone.utc))
    far = hour_deviation(profile, datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc))
    assert near < 1.5, f"01:00 read as {near} sigma from a midnight account"
    assert far > 5, f"noon read as only {far} sigma from a midnight account"


def test_an_account_with_too_little_history_has_no_hour_profile(tid):
    """Better no profile than a profile with a variance so wide it never fires."""
    db = SessionLocal()
    try:
        prof = hour_profiles(db, tid, ["AC-DOES-NOT-EXIST"],
                             datetime.now(timezone.utc))
    finally:
        db.close()
    assert prof == {}
    assert hour_deviation(None, datetime.now(timezone.utc)) is None


def test_normalise_is_applied_identically_to_both_sides():
    assert ref.normalise("M/S  Rāvi   Traders, Pvt. Ltd.") == "M S RAVI TRADERS PVT LTD"


def test_every_quantitative_indicator_declares_how_it_is_measured():
    """A rule in none of the three sets is silently unmeasured.

    It reports as dormant, which reads as "configured but never fired" rather than
    "cannot fire at all", and it inflates any coverage figure derived from the
    catalogue. Six indicators - the trade-finance and core-banking families - were in
    exactly that state, and the published EWS coverage number was wrong because of it.
    """
    from services.analytics_service.app.detection.features import DECLARED
    from services.config_service.app.ews_catalogue import _RULES, QUALITATIVE_FAMILIES

    quantitative = {r[0] for r in _RULES if r[1] not in QUALITATIVE_FAMILIES}
    undeclared = sorted(quantitative - DECLARED)
    assert not undeclared, (
        "these indicators are neither computable nor declared unmeasurable, so they "
        f"report as dormant with no reason: {undeclared}")


def test_no_indicator_claims_to_be_both_measurable_and_not():
    from services.analytics_service.app.detection.features import (
        COMPUTABLE, NEEDS_EXTERNAL_DATA, NEEDS_REFERENCE_DATA)

    overlap = (set(COMPUTABLE) & set(NEEDS_EXTERNAL_DATA)) | \
              (set(COMPUTABLE) & set(NEEDS_REFERENCE_DATA)) | \
              (set(NEEDS_EXTERNAL_DATA) & set(NEEDS_REFERENCE_DATA))
    assert not overlap, f"contradictory declarations: {sorted(overlap)}"
