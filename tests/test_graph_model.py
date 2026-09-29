"""AI/ML roadmap Phase 3 - the GNN itself (BRD OD-06/§19; HLD AD-15/§16).

Deterministic plumbing tests, not model-quality tests. Whether a trained GraphSAGE
separates a real fraud ring from ordinary traffic is a first-working-fit, not-yet-
validated question (same caveat scripts/train_graph_ring_score_model.py states outright)
- not something a fast unit test can honestly assert either way. What these check is
that the graph gets built correctly, the encoder runs and trains without error, and its
output has the shape everything downstream expects.
"""
from datetime import datetime, timedelta, timezone

import pytest
import torch

from services.analytics_service.app.detection import graph_model as gm

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _project(db, tid, rows):
    """Insert directly into fact_transaction - graph_model reads from the analytical
    store, not the ingestion queue, so this is the correct fixture level (same
    shortcut test_reconciliation.py and others already take)."""
    from services.analytics_service.app.models import FactTransaction
    for r in rows:
        db.add(FactTransaction(
            txn_id=r["txn_id"], tenant_id=tid, ts=r["ts"], rail="UPI",
            amount_paise=r["amount_paise"], debtor_account=r["debtor_account"],
            creditor_account=r["creditor_account"], branch="", region="west",
            product="savings", customer_segment="retail", channel="mobile",
            device_id="", ip_addr="", status="settled", source="test"))
    db.commit()


@pytest.fixture
def graph_tid(tid):
    """A dedicated slice of the shared tenant, tagged and cleaned up - build_tenant_graph
    has no txn_id filter of its own (it needs the tenant's whole graph, by design), so
    this fixture's rows must be distinguishable and removed afterwards or they leak into
    every other test that also builds this tenant's graph."""
    yield tid
    from sqlalchemy import text
    from cp_common.db import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM analytics.fact_transaction "
            "WHERE tenant_id = :t AND txn_id LIKE 'GM-%'"), {"t": tid})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------- build_tenant_graph
def test_build_tenant_graph_produces_the_declared_feature_width(graph_tid):
    """The shared test tenant already carries demo-corpus traffic within any recent
    window, so this asserts the tagged accounts are *included*, not that the graph is
    exactly them - build_tenant_graph deliberately has no txn_id filter of its own; it
    needs the tenant's whole graph, by design."""
    from cp_common.db import SessionLocal
    db = SessionLocal()
    try:
        _project(db, graph_tid, [
            {"txn_id": "GM-1", "ts": NOW, "amount_paise": 10000,
             "debtor_account": "GM-A", "creditor_account": "GM-B"},
            {"txn_id": "GM-2", "ts": NOW, "amount_paise": 20000,
             "debtor_account": "GM-B", "creditor_account": "GM-C"},
        ])
        graph = gm.build_tenant_graph(db, graph_tid, NOW, window_days=1)
    finally:
        db.close()
    assert {"GM-A", "GM-B", "GM-C"} <= set(graph.accounts)
    assert graph.x.shape[1] == len(gm.NODE_FEATURE_NAMES)
    assert graph.x.shape[0] == len(graph.accounts)
    # Both directions per edge (GraphSAGE aggregates over an undirected neighbourhood) -
    # at least the two tagged edges must be represented, however much other demo
    # traffic sits in the same window.
    assert graph.edge_index.shape[1] >= 4


def test_build_tenant_graph_is_empty_for_a_tenant_with_no_transactions(graph_tid):
    from cp_common.db import SessionLocal
    db = SessionLocal()
    try:
        graph = gm.build_tenant_graph(db, "no-such-tenant-at-all", NOW, window_days=1)
    finally:
        db.close()
    assert graph.accounts == []
    assert graph.x.shape == (0, len(gm.NODE_FEATURE_NAMES))
    assert graph.edge_index.shape == (2, 0)


def test_build_tenant_graph_excludes_transactions_outside_the_window(graph_tid):
    from cp_common.db import SessionLocal
    db = SessionLocal()
    try:
        _project(db, graph_tid, [
            {"txn_id": "GM-OLD", "ts": NOW - timedelta(days=30), "amount_paise": 10000,
             "debtor_account": "GM-OLD-A", "creditor_account": "GM-OLD-B"},
        ])
        graph = gm.build_tenant_graph(db, graph_tid, NOW, window_days=1)
    finally:
        db.close()
    assert "GM-OLD-A" not in graph.accounts


# ---------------------------------------------------------- encoder / train / embed
def test_encoder_forward_pass_shape():
    encoder = gm.RingScoreEncoder(in_dim=len(gm.NODE_FEATURE_NAMES))
    x = torch.randn(5, len(gm.NODE_FEATURE_NAMES))
    edge_index = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 4]], dtype=torch.long)
    out = encoder(x, edge_index)
    assert out.shape == (5, gm.EMBEDDING_DIM)


def test_train_encoder_runs_to_completion_and_embed_covers_every_account():
    graph = gm.TenantGraph(
        accounts=["A", "B", "C", "D"],
        x=torch.randn(4, len(gm.NODE_FEATURE_NAMES)),
        edge_index=torch.tensor([[0, 1, 2, 0], [1, 2, 3, 3]], dtype=torch.long))
    encoder = gm.train_encoder(graph, epochs=5)  # a handful - this is a plumbing test
    embeddings = gm.embed(encoder, graph)
    assert set(embeddings) == {"A", "B", "C", "D"}
    for vec in embeddings.values():
        assert len(vec) == gm.EMBEDDING_DIM


def test_embed_on_an_empty_graph_returns_empty_not_an_error():
    encoder = gm.RingScoreEncoder(in_dim=len(gm.NODE_FEATURE_NAMES))
    empty = gm.TenantGraph(accounts=[], x=torch.zeros((0, len(gm.NODE_FEATURE_NAMES))),
                           edge_index=torch.zeros((2, 0), dtype=torch.long))
    assert gm.embed(encoder, empty) == {}


def test_encoder_state_dict_round_trips():
    """What scripts/train_graph_ring_score_model.py saves and model_scoring.load()
    reconstructs must actually match - a shape mismatch here would surface as a cryptic
    RuntimeError deep in load_state_dict at scoring time instead."""
    original = gm.RingScoreEncoder(in_dim=len(gm.NODE_FEATURE_NAMES))
    rebuilt = gm.RingScoreEncoder(in_dim=len(gm.NODE_FEATURE_NAMES))
    rebuilt.load_state_dict(original.state_dict())

    x = torch.randn(3, len(gm.NODE_FEATURE_NAMES))
    edge_index = torch.tensor([[0, 1], [1, 2]], dtype=torch.long)
    original.eval()
    rebuilt.eval()
    with torch.no_grad():
        assert torch.allclose(original(x, edge_index), rebuilt(x, edge_index))
