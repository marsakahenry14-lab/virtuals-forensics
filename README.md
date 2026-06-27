# ACP Mainnet Dataset & Forensic Indexer

Reproducible on-chain dataset for **AgenticCommerceV3** — the production predecessor of ERC-8183 —  
running on Base Mainnet.

**Contract:** `0x238E541BfefD82238730D00a2208E5497F1832E0`  
**Chain:** Base Mainnet (chain id 8453)  
**Indexed block range:** 44,427,013 – 47,718,785  
**Dataset:** 62,953 `JobCreated` events + full job lifecycle

No LLM inference. No assumptions. Raw on-chain events, SQL queries, verifiable transaction hashes.

---

## What This Is

This repository contains:

1. **`indexer.py`** — a deterministic Python indexer that reads all AgenticCommerceV3 events from Base Mainnet via `eth_getLogs` and stores them in a local SQLite database.
2. **`metrics.py`** — a set of SQL-based analytical functions that compute structural observations about protocol usage patterns.
3. **`api.py`** — a FastAPI server exposing the metrics as a JSON endpoint.
4. **`RESEARCH.md`** — reproducible research documentation with all SQL queries, results, and on-chain verification steps.
5. **`VALIDATION.md`** — dataset integrity checks and Basescan verification methodology.

The primary asset is the **reproducible dataset and methodology**, not any individual observation.

---

## Dataset Summary

| Table | Rows | Fields |
|---|---|---|
| JobCreated | 62,953 | job_id, client, provider, evaluator, expired_at, hook |
| JobFunded | 10,544 | job_id, client, amount |
| JobSubmitted | 9,333 | job_id, provider, deliverable (bytes32) |
| JobCompleted | 8,859 | job_id, evaluator, reason |
| PaymentReleased | 8,859 | job_id, provider, amount |
| JobExpired | 1,130 | job_id |

All counts are reproducible by running the indexer against the same block range.

---

## Quick Start

```bash
git clone https://github.com/marsakahenry14-lab/virtuals-forensics
cd virtuals-forensics
pip install -r requirements.txt

# Re-index from scratch (takes ~2–3 hours on public RPC)
cp .env.example .env
python indexer.py

# Or start the metrics API directly (requires indexer_cache.db)
python api.py
```

Then query all metrics:

```bash
curl http://localhost:8000/api/v1/metrics | python3 -m json.tool
```

Interactive API docs available at `http://localhost:8000/docs`.

## Interactive Queries

Use `query.py` for ad-hoc SQL without quoting hell:

```bash
# Count jobs
python query.py "SELECT COUNT(*) FROM JobCreated"

# Top clients
python query.py "SELECT client, COUNT(*) as cnt FROM JobCreated GROUP BY client ORDER BY cnt DESC LIMIT 5"

# List all tables
python query.py
```

Works on Windows PowerShell, CMD, Linux, Mac — no quoting issues.

---

## Environment

Copy `.env.example` to `.env` and set your RPC URL:

```
BASE_RPC_URL=https://mainnet.base.org
START_BLOCK=44427013
END_BLOCK=47718785
```

> **Note:** Using a paid RPC endpoint (Alchemy, QuickNode, Infura) is strongly recommended.  
> Public RPC endpoints rate-limit aggressively and may produce incomplete data.  
> Expected indexing time: 2–3 hours on a paid endpoint.

---

## API Endpoints

`GET /api/v1/metrics` — all structural observations as JSON:

```json
{
  "funnel": {
    "JobCreated": 62953,
    "JobFunded": 10544,
    "JobSubmitted": 9333,
    "JobCompleted": 8859,
    "PaymentReleased": 8859,
    "JobExpired": 1130
  },
  "top_pairs": [...],
  "empty_deliverables": {
    "total_empty_submitted": 398,
    "completed_with_empty": 392,
    "expired_with_empty": 6
  },
  "evaluator_behavior": [...],
  "structural_observations": {
    "zero_evaluator_jobs": 45644,
    "zero_evaluator_percentage": 72.5,
    "unique_self_evaluators": 212,
    "self_eval_jobs": 17299,
    "total_usdc_volume": 353.21,
    "self_eval_usdc_volume": 268.71
  }
}
```

---

## Reproducibility

All observations in `RESEARCH.md` are reproducible using:

1. The indexer (`indexer.py`) against the pinned block range.
2. The SQL queries documented in `RESEARCH.md`.
3. Manual verification on [Basescan](https://basescan.org) using the transaction hashes listed in `VALIDATION.md`.

Dataset integrity check:
```sql
SELECT COUNT(*) FROM JobCreated;             -- 62953
SELECT COUNT(DISTINCT job_id) FROM JobCreated; -- 62953
```

No duplicate `job_id` values. One row per job creation event.

---

## Limitations

- **Block range is fixed.** The dataset covers blocks 44,427,013 – 47,718,785. Re-running the indexer today will produce a larger dataset.
- **Token assumption.** `PaymentReleased.amount` is assumed to be USDC (6 decimals) based on the contract's payment token. This is not verified in code.
- **Basescan sample.** On-chain verification was performed on 5 illustrative transactions, not a statistically representative sample.
- **Intent is unknown.** No observation in this dataset implies intent, fraud, or protocol violation. All statements are empirical.

---

## Stack

```
Python 3.11+
web3.py          # event indexing via eth_getLogs
sqlite3          # storage (Python stdlib)
FastAPI          # metrics API
uvicorn          # ASGI server
tenacity         # retry logic for RPC calls
```

---

## Related

- [ERC-8183 specification](https://eips.ethereum.org/EIPS/eip-8183)
- [RESEARCH.md](./RESEARCH.md) — full observations with SQL and on-chain verification
- [VALIDATION.md](./VALIDATION.md) — dataset integrity and Basescan verification log

---

## License

MIT — see [LICENSE](./LICENSE).
