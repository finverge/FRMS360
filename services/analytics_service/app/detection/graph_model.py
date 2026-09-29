"""AI/ML roadmap Phase 3 - the GNN itself (BRD OD-06/§19; HLD AD-15/§16).

Shared between ``scripts/train_graph_ring_score_model.py`` (fits it) and
``scripts/refresh_ring_scores.py`` (runs it periodically to populate
``analytics.account_ring_score``) - one encoder definition, not two, for the same reason
``cp_common.observations`` is one implementation shared by both scoring lanes: a model
reconstructed from a mismatched class definition would load without error and produce
silently wrong embeddings.

**Why GraphSAGE, not a GCN.** An account graph gains new nodes constantly - GraphSAGE is
inductive (its aggregation is defined over a node's neighbourhood, not baked into a fixed
adjacency matrix at training time), so it scores an account it has never seen without
retraining. A transductive GCN cannot do that at all.

**Why self-supervised, not classification.** No tenant has labelled fraud rings to train
against - the same situation Phase 2's velocity model was in. The encoder is trained on a
link-prediction objective instead (real edges should score higher than random pairs under
a dot-product decoder) - a standard, label-free way to make an embedding space that
reflects graph structure, per the original GraphSAGE paper (Hamilton et al., 2017).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import torch
import torch.nn.functional as F
from sqlalchemy import text
from sqlalchemy.orm import Session
from torch_geometric.nn import SAGEConv
from torch_geometric.utils import negative_sampling

from cp_common.observations import CTR_PAISE, SMALL_CREDIT_PAISE

#: Seed features per node, fixed order - the encoder is trained and run against exactly
#: this vector. The first five are Phase 2's own feature_vector() formulas (never a
#: second definition of the same measurement); the last three are topology Phase 2 never
#: needed - a GNN with no signal about degree or flow size cannot use its neighbourhood
#: aggregation for anything.
NODE_FEATURE_NAMES = (
    "value_vs_baseline", "distinct_counterparties", "outflow_ratio",
    "small_credit_count", "sub_ctr_count",
    "log_degree", "log_in_paise", "log_out_paise",
)
EMBEDDING_DIM = 16


class RingScoreEncoder(torch.nn.Module):
    """Two SAGEConv layers, ReLU between them - deliberately shallow. A payment ring is
    a local structure (an account and its immediate counterparties); more layers mean
    more over-smoothing before there is any more signal to gain from it."""

    def __init__(self, in_dim: int = len(NODE_FEATURE_NAMES), hidden_dim: int = 32,
                out_dim: int = EMBEDDING_DIM):
        super().__init__()
        self.conv1 = SAGEConv(in_dim, hidden_dim)
        self.conv2 = SAGEConv(hidden_dim, out_dim)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        h = F.relu(self.conv1(x, edge_index))
        return self.conv2(h, edge_index)


@dataclass
class TenantGraph:
    accounts: list[str]                # index -> account number
    x: torch.Tensor                    # [N, len(NODE_FEATURE_NAMES)]
    edge_index: torch.Tensor           # [2, E], both directions (undirected for GraphSAGE)


def build_tenant_graph(db: Session, tenant_id: str, now: datetime,
                       *, window_days: int = 90) -> TenantGraph:
    """The tenant's whole transaction graph over the window, not one case's
    neighbourhood - training needs the full structure a rule-based LAY alert may not
    have flagged yet, which is the entire point of adding a second detector."""
    since = now - timedelta(days=window_days)
    p = {"t": tenant_id, "since": since}

    edges = db.execute(text(
        "SELECT DISTINCT debtor_account AS src, creditor_account AS dst "
        "FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND ts >= :since AND debtor_account <> creditor_account"),
        p).all()

    accounts = sorted({a for src, dst in edges for a in (src, dst)})
    if not accounts:
        return TenantGraph(accounts=[], x=torch.zeros((0, len(NODE_FEATURE_NAMES))),
                           edge_index=torch.zeros((2, 0), dtype=torch.long))
    idx = {a: i for i, a in enumerate(accounts)}

    # Same aggregates features.load_context() computes, over the whole window rather
    # than a live batch - reused formulas, not a third definition.
    baseline = {r["a"]: float(r["m"] or 0) for r in db.execute(text(
        "SELECT debtor_account AS a, AVG(amount_paise) AS m "
        "FROM analytics.fact_transaction WHERE tenant_id = :t AND ts >= :since "
        "GROUP BY debtor_account"), p).mappings()}
    # Same CTR_PAISE/SMALL_CREDIT_PAISE thresholds cp_common.observations uses for
    # SME-01/the small-credit rules - imported, not retyped, so this graph never
    # quietly measures "structuring" or "small credit" by a different definition than
    # the rules engine does.
    flow = {r["a"]: r for r in db.execute(text(
        "SELECT debtor_account AS a, COALESCE(SUM(amount_paise), 0) AS out_paise, "
        "       COUNT(*) FILTER (WHERE amount_paise >= :ctr_lo AND amount_paise < :ctr) "
        "         AS sub_ctr "
        "FROM analytics.fact_transaction WHERE tenant_id = :t AND ts >= :since "
        "GROUP BY debtor_account"),
        {**p, "ctr": CTR_PAISE, "ctr_lo": int(CTR_PAISE * 0.9)}).mappings()}
    inflow = {r["a"]: r for r in db.execute(text(
        "SELECT creditor_account AS a, COALESCE(SUM(amount_paise), 0) AS in_paise, "
        "       COUNT(*) FILTER (WHERE amount_paise <= :small) AS small_credits "
        "FROM analytics.fact_transaction WHERE tenant_id = :t AND ts >= :since "
        "GROUP BY creditor_account"),
        {**p, "small": SMALL_CREDIT_PAISE}).mappings()}
    degree: dict[str, int] = {}
    for src, dst in edges:
        degree[src] = degree.get(src, 0) + 1
        degree[dst] = degree.get(dst, 0) + 1
    counterparties: dict[str, set] = {}
    for src, dst in edges:
        counterparties.setdefault(src, set()).add(dst)
        counterparties.setdefault(dst, set()).add(src)

    rows = []
    for a in accounts:
        base = baseline.get(a, 0.0)
        f, i = flow.get(a, {}), inflow.get(a, {})
        out_p, in_p = float(f.get("out_paise") or 0), float(i.get("in_paise") or 0)
        rows.append([
            (out_p / base) if base > 0 else 0.0,          # a proxy for value_vs_baseline
            float(len(counterparties.get(a, ()))),
            (out_p / in_p) if in_p > 0 else 0.0,
            float(i.get("small_credits") or 0),
            float(f.get("sub_ctr") or 0),
            math.log1p(degree.get(a, 0)),
            math.log1p(in_p),
            math.log1p(out_p),
        ])
    x = torch.tensor(rows, dtype=torch.float32)

    src_idx = [idx[s] for s, d in edges] + [idx[d] for s, d in edges]
    dst_idx = [idx[d] for s, d in edges] + [idx[s] for s, d in edges]
    edge_index = torch.tensor([src_idx, dst_idx], dtype=torch.long)

    return TenantGraph(accounts=accounts, x=x, edge_index=edge_index)


def train_encoder(graph: TenantGraph, *, epochs: int = 200, lr: float = 0.01,
                  seed: int = 42) -> RingScoreEncoder:
    """Link-prediction objective: a real edge's endpoints should dot-product higher
    than a random pair's. No labels, same reasoning as Phase 2's IsolationForest -
    unsupervised is the only honest option until a tenant has confirmed ring outcomes
    to train against."""
    torch.manual_seed(seed)
    model = RingScoreEncoder(in_dim=graph.x.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    for _ in range(epochs):
        opt.zero_grad()
        z = model(graph.x, graph.edge_index)
        pos_score = (z[graph.edge_index[0]] * z[graph.edge_index[1]]).sum(dim=-1)
        neg_edge_index = negative_sampling(
            graph.edge_index, num_nodes=graph.x.shape[0],
            num_neg_samples=graph.edge_index.shape[1])
        neg_score = (z[neg_edge_index[0]] * z[neg_edge_index[1]]).sum(dim=-1)
        loss = F.binary_cross_entropy_with_logits(
            torch.cat([pos_score, neg_score]),
            torch.cat([torch.ones_like(pos_score), torch.zeros_like(neg_score)]))
        loss.backward()
        opt.step()

    model.eval()
    return model


def embed(model: RingScoreEncoder, graph: TenantGraph) -> dict[str, list[float]]:
    """Account -> embedding, for whatever the caller does next (fitting an
    IsolationForest at training time; scoring against one at refresh time)."""
    if not graph.accounts:
        return {}
    with torch.no_grad():
        z = model(graph.x, graph.edge_index)
    return {a: z[i].tolist() for i, a in enumerate(graph.accounts)}
