# ACP Mainnet Dataset — Reproducible Research

**Contract:** AgenticCommerceV3 · `0x238E541BfefD82238730D00a2208E5497F1832E0`  
**Chain:** Base Mainnet (chain id 8453)  
**Indexed range (pinned):** 44,427,013 - 47,718,785; **observed event span:** 44,429,969 - 47,715,550
**Dataset:** 62,953 JobCreated events + full lifecycle

> All statements in this document are separated into **Observed** (SQL-verifiable), **Verified** (confirmed on Basescan), **Hypothesis** (plausible explanation, not confirmed), and **Limitation** (known gap in methodology).

## Key Findings

- **Independent evaluation is effectively absent:** of 62,953 jobs created, only 10 (0.02%)
  name an evaluator that is neither the zero address nor the client. 45,644 (72.50%) have a
  zero-address evaluator; 17,299 (27.48%) set client == evaluator.
- **Extreme concentration:** the single top client→provider pair accounts for 21,937 jobs,
  and the top client alone created 43,858 (69.67%); the top two clients created 87.38%.
- **Value flows through self-evaluated jobs:** of $353.21 total released USDC, $268.71 (~76%)
  went to jobs where client == evaluator.
- **Funded-but-no-work:** 1,232 jobs were rejected before any deliverable was submitted, and
  1,130 expired — funds escrowed without a resulting deliverable.

These are structural, SQL-reproducible observations. They describe on-chain behavior and do
not imply intent, fraud, or protocol violation.

---

## 1. Data Collection

### What was collected

Events indexed from AgenticCommerceV3 using `eth_getLogs` via web3.py:

| Table | Rows | Description |
|---|---|---|
| JobCreated | 62,953 | job_id, client, provider, evaluator, expired_at, hook |
| JobFunded | 10,544 | job_id, client, amount |
| JobSubmitted | 9,333 | job_id, provider, deliverable (bytes32) |
| JobCompleted | 8,859 | job_id, evaluator, reason |
| JobRejected | 1,411 | job_id, rejector, reason |
| JobExpired | 1,130 | job_id |
| PaymentReleased | 8,859 | job_id, provider, amount |

### Lifecycle branching (why Rejected + Completed can exceed Submitted)

**Observed:** Of 1,411 `JobRejected` events, 1,232 (87.3%) have no prior `JobSubmitted`
event — these jobs were rejected before any deliverable was submitted. The `JobRejected`
and `JobCompleted` sets are disjoint (0 overlap). The lifecycle branches at `JobFunded`:
one path is `Submitted → Completed`, the other is `Rejected` / `Expired`. These are parallel
paths, not sequential stages, so `Completed + Rejected` is not expected to equal `Submitted`.

Verification queries:

```sql
-- disjoint: rejected jobs that are also completed (expect 0)
SELECT COUNT(*) FROM JobRejected r JOIN JobCompleted c ON r.job_id = c.job_id;

-- rejected before any submission (expect 1,232)
SELECT COUNT(*) FROM JobRejected r
LEFT JOIN JobSubmitted s ON r.job_id = s.job_id
WHERE s.job_id IS NULL;
```

### Integrity check

```sql
SELECT COUNT(*) FROM JobCreated;                -- 62,953
SELECT COUNT(DISTINCT job_id) FROM JobCreated;  -- 62,953
```

**Observed:** No duplicate `job_id` values. One row per job creation event.

### How to reproduce

```bash
cp .env.example .env
# Edit .env: set BASE_RPC_URL, START_BLOCK=44427013, END_BLOCK=47718785
python indexer.py
python generate_report.py
```

**Limitation:** Re-running the indexer beyond block 47,718,785 will produce additional rows. All statistics in this document are specific to the pinned block range; the observed event span in this snapshot is 44,429,969 - 47,715,550.

---

## 2. Observation 1 — Evaluator Structure

### SQL query

```sql
SELECT
    SUM(CASE WHEN LOWER(evaluator) = LOWER('0x0000000000000000000000000000000000000000') THEN 1 ELSE 0 END) AS zero_evaluator_jobs,
    SUM(CASE WHEN LOWER(client) = LOWER(evaluator) AND LOWER(evaluator) != LOWER('0x0000000000000000000000000000000000000000') THEN 1 ELSE 0 END) AS self_evaluator_jobs,
    SUM(CASE WHEN LOWER(evaluator) != LOWER('0x0000000000000000000000000000000000000000') AND LOWER(client) != LOWER(evaluator) THEN 1 ELSE 0 END) AS independent_evaluator_jobs
FROM JobCreated;
```

**Observed:**
- Zero evaluator jobs: 45,644 (72.50%)
- Client equals evaluator: 17,299 (27.48%)
- Independent evaluator jobs: 10 (0.02%)

### Interpretation

The dataset shows that evaluator trust is not structurally enforced in the contract itself. Whether that is acceptable depends on the intended protocol design, but the role distribution is measurable and reproducible from the raw logs.

---

## 3. Observation 2 — Self-Evaluator Address Reuse

### SQL query

```sql
SELECT COUNT(DISTINCT client) AS unique_addresses
FROM JobCreated
WHERE LOWER(client) = LOWER(evaluator)
  AND LOWER(evaluator) != LOWER('0x0000000000000000000000000000000000000000');
```

**Observed:** 212 unique non-zero addresses appear as both `client` and `evaluator`.

### Top client-provider pair

```sql
SELECT client, provider, COUNT(*) AS jobs
FROM JobCreated
GROUP BY client, provider
ORDER BY jobs DESC
LIMIT 1;
```

**Observed:** Top pair `0x22F70dAf4426Fe47D2ef4BE54C3ba7653Be01491 -> 0xD6A5093213d0e940a887ee5327c60aF7e53B0261` appears on 21,937 jobs, with 1,940 completed jobs.

---

## 4. Observation 3 — Empty Deliverable Hash

### Reference hash

`keccak256("") = 0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470`

### SQL query

```sql
SELECT
    COUNT(*) AS total_empty_submitted,
    COUNT(DISTINCT comp.job_id) AS completed_with_empty,
    COUNT(DISTINCT exp.job_id) AS expired_with_empty
FROM JobSubmitted s
LEFT JOIN JobCompleted comp ON s.job_id = comp.job_id
LEFT JOIN JobExpired exp ON s.job_id = exp.job_id
WHERE LOWER(s.deliverable) = LOWER('0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470');
```

**Observed:**
- Empty deliverables submitted: 398
- Empty deliverables completed: 392 (98.49%)
- Empty deliverables expired: 6 (1.51%)

---

## 5. Observation 4 — Activity Concentration

### Top clients

```sql
SELECT
    client,
    COUNT(*) AS total_created
FROM JobCreated
GROUP BY client
ORDER BY total_created DESC
LIMIT 2;
```

**Observed:**
- Top client one: `0x22F70dAf4426Fe47D2ef4BE54C3ba7653Be01491` with 43,858 jobs (69.67%)
- Top client two: `0x4e7C9Cec0C188C4f38f089E7843d750b7C3FAB46` with 11,146 jobs (17.71%)
- Combined top-two share: 87.38%

---

## 6. Financial Scale

### SQL query

```sql
SELECT SUM(CAST(amount AS REAL)) / 1e6 AS total_usdc
FROM PaymentReleased;
```

**Observed:**
- Total payment volume: 353.21
- Self-evaluator volume: 268.71
- Average payment size: 0.04

**Limitation:** Amounts are normalized assuming 6 decimals. Confirm token configuration separately if exact denomination matters.

---

## 7. Implications for Implementors

The dataset illustrates a repeatable governance and trust pattern:

- Zero-evaluator jobs remain possible when `evaluator` is unset.
- Client-evaluator overlap remains possible when the contract does not require role separation.
- Empty deliverables can still progress through the lifecycle when no stronger validation layer exists.

These are structural observations about the on-chain data, not claims about intent.

---

## Appendix — Basescan Reference

Contract reference: [0x238E541BfefD82238730D00a2208E5497F1832E0](https://basescan.org/address/0x238E541BfefD82238730D00a2208E5497F1832E0)
