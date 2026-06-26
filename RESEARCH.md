# ACP Mainnet Dataset — Reproducible Research

**Contract:** AgenticCommerceV3 · `0x238E541BfefD82238730D00a2208E5497F1832E0`  
**Chain:** Base Mainnet (chain id 8453)  
**Indexed block range:** 44,427,013 – 47,718,785  
**Dataset:** 62,953 JobCreated events + full lifecycle  
**Date:** June 2026

> All statements in this document are separated into **Observed** (SQL-verifiable), **Verified** (confirmed on Basescan), **Hypothesis** (plausible explanation, not confirmed), and **Limitation** (known gap in methodology).

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
| PaymentReleased | 8,859 | job_id, provider, amount |
| JobExpired | 1,130 | job_id |

### Schema (SQLite)

```sql
CREATE TABLE JobCreated (
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    tx_index INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    client TEXT NOT NULL,
    provider TEXT NOT NULL,
    evaluator TEXT NOT NULL,
    expired_at TEXT NOT NULL,
    hook TEXT NOT NULL,
    PRIMARY KEY (tx_hash, log_index)
);

CREATE TABLE JobSubmitted (
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    tx_index INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    deliverable TEXT NOT NULL,  -- stored as hex string (bytes32)
    PRIMARY KEY (tx_hash, log_index)
);
-- JobCompleted, JobExpired, PaymentReleased follow identical structure
```

### Integrity check

```sql
SELECT COUNT(*) FROM JobCreated;             -- 62953
SELECT COUNT(DISTINCT job_id) FROM JobCreated; -- 62953
```

**Observed:** No duplicate `job_id` values. One row per job creation event.

### How to reproduce

```bash
cp .env.example .env
# Edit .env: set BASE_RPC_URL, START_BLOCK=44427013, END_BLOCK=47718785
python indexer.py   # takes ~2–3 hours on a paid RPC endpoint
```

**Limitation:** Re-running the indexer today (beyond block 47,718,785) will produce additional rows. All statistics in this document are specific to the pinned block range.

---

## 2. Observation 1 — Zero Address in Evaluator Field

### Background

The `createJob()` function accepts any Ethereum address as the `evaluator` parameter, including `0x0000000000000000000000000000000000000000` (the zero address). ERC-8183, of which this contract is a production predecessor, explicitly states that each implementation must solve evaluator trust independently.

### SQL query

```sql
SELECT COUNT(*) as zero_evaluator_jobs
FROM JobCreated
WHERE evaluator = '0x0000000000000000000000000000000000000000';
```

**Observed:** 45,644 jobs — 45,644 / 62,953 = **72.5%** of all jobs in the dataset.

### What happens at completion

When the `evaluator` field is the zero address, an external caller triggers the `complete()` function. To identify who that caller is, 5 transactions were examined manually on Basescan.

**Verification methodology:** For each transaction, locate the `UserOperationEvent` log and compare the `sender` field to the `provider` address stored in `JobCreated`.

### Basescan verification — 5 illustrative transactions

> **Limitation:** These 5 transactions were not selected by random sampling. They are illustrative examples. Statistical inference requires a larger, randomly selected sample.

**Tx 1:** `0xf6811f29da43c8411accf9a096c8c8d0ed3b0441ba5f2f05b5366b30d6a4439e`
- Job ID: 62941
- Provider (from JobCreated): `0xEA0f80AAC331a0eEE486EE67D61F9f4aD7085Ee8`
- Evaluator: `0x000...000`
- UserOperationEvent.sender: `0xEA0f80AAC331a0eEE486EE67D61F9f4aD7085Ee8`
- **Verified:** sender == provider

**Tx 2:** `0xf571a1a63c78278fc06bb66e009542115ea715b5c859e6238c6dc47869153296`
- Job ID: 62937 · Provider: `0xEA0f80AAC331a0eEE486EE67D61F9f4aD7085Ee8`
- **Verified:** sender == provider

**Tx 3:** `0xc45c0f72444276364321e2f8fd5bbb3bab4f71aabf4cf8af9c880a8fae986916`
- Job ID: 62892 · Provider: `0xB97552998e7EE94eF2A260FDc25529eD93e4902B`
- Payment: 370,500 USDC units ($0.37)
- **Verified:** sender == provider

**Tx 4:** `0x8625dc663c81f2381e3577cd06502fe919af879351449bad110afb699bc06581`
- Job ID: 62888 · Provider: `0xB97552998e7EE94eF2A260FDc25529eD93e4902B`
- Payment: 370,500 USDC units
- **Verified:** sender == provider

**Tx 5:** `0x5ccd5b5cbfe324e081d25240383e07e07de3ae2eb3c1b19b5678f87f0a0e637a`
- Job ID: 62885 · Provider: `0xECf9773B50F01f3A97b087A6EcDf12A71AFC558C`
- Payment: 95,000 USDC units
- **Verified:** sender == provider

**Observed (5/5 sample):** In all 5 examined transactions, the address calling `complete()` matches the `provider` address from `JobCreated`.

### Fact / Hypothesis / Limitation table

| Statement | Classification |
|---|---|
| 45,644 jobs were created with `evaluator = 0x000...000` | **Observed** (SQL, reproducible) |
| In 5/5 sampled transactions, `complete()` was called by the provider | **Verified** (Basescan) |
| Provider self-completion is the dominant behavior at scale | **Hypothesis** — 5-tx sample is insufficient for statistical confidence |
| This behavior is undocumented in the protocol | **Limitation** — no systematic review of all documentation was performed |
| This constitutes a vulnerability or abuse | **Out of scope** — intent cannot be determined from on-chain data |

### Implication for ERC-8183 implementors

This pattern is structurally prevented by adding `require(evaluator != address(0))` at job creation. Neither this contract nor the ERC-8183 draft (as of June 2026) mandates this check.

---

## 3. Observation 2 — Client Address Matches Evaluator Address

### Background

The protocol does not enforce that the `evaluator` must differ from the `client`. This means a single address can occupy both roles simultaneously.

### SQL queries

```sql
-- Jobs where client == evaluator (excluding zero address)
SELECT COUNT(DISTINCT client) as unique_addresses
FROM JobCreated
WHERE client = evaluator
  AND evaluator != '0x0000000000000000000000000000000000000000';
```

**Observed:** 129 unique addresses where `client == evaluator` (non-zero).

```sql
-- Top client-evaluator pairs by volume
SELECT jc.client, jc.provider, COUNT(*) as jobs
FROM JobCreated jc
WHERE jc.client = jc.evaluator
  AND jc.evaluator != '0x0000000000000000000000000000000000000000'
GROUP BY jc.client, jc.provider
ORDER BY jobs DESC
LIMIT 5;
```

**Observed (top result):**
```
0xd77443a6D3E071c609AB027D5016c86BEbf4ef69 → 0x2D71D98345CF06B4F26294465406DF707697c4EF  175 jobs
0xd77443a6D3E071c609AB027D5016c86BEbf4ef69 → 0x3Cc44B0Ab1735cc3Df1B2194E2B4AC0AB099B25a  136 jobs
```

### Case study — address `0xd77443a6D3E071c609AB027D5016c86BEbf4ef69`

This address appears as both `client` and `evaluator` on 311 jobs (175 + 136).  
The same address is also the `evaluator` on `JobCompleted` events for those jobs.

**Evaluator behavior for this address (SQL-derived):**
- Total jobs completed as evaluator: 237
- Of those: 226 (95.3%) had `deliverable = keccak256("")` (see Observation 3)
- Approval rate: 100%

| Statement | Classification |
|---|---|
| 129 addresses where `client == evaluator` (non-zero) | **Observed** (SQL) |
| `0xd77443...` is client and evaluator on the same jobs | **Observed** (SQL) |
| 226/237 completions by `0xd77443...` used `keccak256("")` deliverable | **Observed** (SQL + join) |
| This represents intentional self-dealing | **Out of scope** — intent unknown |

---

## 4. Observation 3 — Repeated Deliverable Hash Matching keccak256("")

### Background

`keccak256("") = 0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470`

This is the hash of an empty byte string. When stored as the `deliverable` field in a `JobSubmitted` event, it indicates the provider submitted no content.

### SQL query

```sql
SELECT 
    COUNT(*) as total_empty_submitted,
    COUNT(comp.job_id) as completed_with_empty,
    COUNT(exp.job_id) as expired_with_empty
FROM JobSubmitted s
LEFT JOIN JobCompleted comp ON s.job_id = comp.job_id
LEFT JOIN JobExpired exp ON s.job_id = exp.job_id
WHERE s.deliverable = 
    '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470';
```

**Observed:**
```
total_empty_submitted:  398
completed_with_empty:   392  (98.5%)
expired_with_empty:       6
```

### On-chain verification

Transaction: `0x428952d30add5344d5eba381ecec490fdce812891a7f1a8ef0c7285f1fdf122c`  
Block: 45,284,050 · Status: Success · Type: ERC-4337 Account Abstraction Bundle

**Verified on Basescan:**
- `JobSubmitted` log: `deliverable = 0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470`
- `JobCompleted` emitted in the same transaction
- `PaymentReleased` emitted in the same transaction

| Statement | Classification |
|---|---|
| 398 `JobSubmitted` events contain `deliverable = keccak256("")` | **Observed** (SQL) |
| 392 of those resulted in `JobCompleted` | **Observed** (SQL join) |
| Escrow was released on the verified transaction | **Verified** (Basescan) |
| Providers deliberately submitted empty content | **Out of scope** — reason unknown |

---

## 5. Observation 4 — Activity Concentration

### SQL query

```sql
SELECT 
    client,
    COUNT(*) as total_created,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM JobCreated), 1) as pct
FROM JobCreated
GROUP BY client
ORDER BY total_created DESC
LIMIT 3;
```

**Observed:**
```
0x22F70dAf4426Fe47D2ef4BE54C3ba7653Be01491  43,858  69.7%
0x4e7C9Cec0C188C4f38f089E7843d750b7C3FAB46  11,146  17.7%
(all remaining addresses)                    7,949  12.6%
```

Two addresses account for 87.4% of all job creation in this dataset.

### Top client-provider pairs

```sql
SELECT client, provider, COUNT(*) as jobs
FROM JobCreated
GROUP BY client, provider
ORDER BY jobs DESC
LIMIT 3;
```

**Observed:**
```
0x22F70... → 0xD6A509...   21,937 jobs
0x22F70... → 0xDF5e15...   21,921 jobs
0x4e7C9C... → 0x389952...  11,146 jobs
```

### Evaluator field for `0x22F70...`

```sql
SELECT evaluator, COUNT(*)
FROM JobCreated
WHERE client = '0x22F70dAf4426Fe47D2ef4BE54C3ba7653Be01491'
GROUP BY evaluator;
```

**Observed:** All 43,858 jobs from this address use `evaluator = 0x000...000`.

| Statement | Classification |
|---|---|
| `0x22F70...` created 43,858 / 62,953 jobs | **Observed** (SQL) |
| All 43,858 use zero evaluator | **Observed** (SQL) |
| `0x22F70...` used exactly 2 provider addresses | **Observed** (SQL) |
| This represents automated or bot activity | **Hypothesis** — no confirmation |
| This is wash trading | **Out of scope** — intent unknown |

---

## 6. Financial Scale

`PaymentReleased.amount` is stored as an integer (wei-equivalent). Assuming USDC (6 decimal places) as the payment token:

```sql
-- Total payments released across dataset
SELECT SUM(CAST(amount AS REAL)) / 1e6 as total_usdc
FROM PaymentReleased;
```

**Observed:** ~$625 USDC across 8,859 payment events.

```sql
-- Payments released on jobs where client == evaluator
SELECT COUNT(*) as payments, SUM(CAST(pr.amount AS REAL)) / 1e6 as usdc
FROM PaymentReleased pr
JOIN JobCreated jc ON pr.job_id = jc.job_id
WHERE jc.client = jc.evaluator;
```

**Observed:** 3,058 payment events, ~$268 USDC.

**Limitation:** The token identity (USDC) has not been verified against the contract's actual payment token configuration. If the token is not USDC or uses different decimals, these figures are incorrect.

---

## 7. Implications for ERC-8183 Implementors

ERC-8183 states that evaluator trust must be solved per-implementation. This dataset represents the largest available on-chain record of AgenticCommerceV3 usage and illustrates two structural patterns that emerge without explicit enforcement:

**Pattern A — Zero evaluator (72.5% of jobs in this dataset):**  
No independent review occurs. A single `require(evaluator != address(0))` at job creation eliminates this pattern.

**Pattern B — Client-as-evaluator (129 addresses in this dataset):**  
No independent review occurs. A single `require(evaluator != client)` at job creation eliminates this pattern.

Neither constraint exists in the current production contract.

---

## Appendix — Running All Queries

Without `sqlite3` CLI (Windows):

```bash
python -c "
import sqlite3
c = sqlite3.connect('indexer_cache.db')
# paste any query here
print(c.execute('SELECT COUNT(*) FROM JobCreated').fetchone())
"
```

Or via the API:
```bash
python api.py
curl http://localhost:8000/api/v1/metrics
```

All source code and queries: [https://github.com/marsakahenry14-lab/virtuals-forensics](https://github.com/marsakahenry14-lab/virtuals-forensics)
