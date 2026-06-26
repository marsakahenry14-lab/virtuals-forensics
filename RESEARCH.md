# ACP Trust & Flow Analyzer — Reproducible Research

**Contract:** AgenticCommerceV3 · `0x238E541BfefD82238730D00a2208E5497F1832E0`  
**Chain:** Base Mainnet (chain id 8453)  
**Dataset:** 62,953 JobCreated events + full lifecycle  
**Date:** June 2026  

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
    deliverable TEXT NOT NULL,
    PRIMARY KEY (tx_hash, log_index)
);
```

### Integrity check

```sql
SELECT COUNT(*) FROM JobCreated;            -- 62953
SELECT COUNT(DISTINCT job_id) FROM JobCreated; -- 62953
```

## 2. Finding 1 — Zero Evaluator Enables Provider Self-Completion

### What is zero evaluator
The `createJob()` function accepts any address as evaluator, including  
`0x0000000000000000000000000000000000000000` (the zero address).

### SQL query
```sql
SELECT COUNT(*) as zero_evaluator_jobs
FROM JobCreated
WHERE evaluator = '0x0000000000000000000000000000000000000000';
```
Result: 45,644 Percentage of total: 45,644 / 62,953 = 72.5%

## 3. Finding 2 — Client-as-Evaluator Self-Evaluation

### SQL query
```sql
SELECT 
    jc.client,
    jc.provider,
    jc.evaluator,
    COUNT(*) as jobs
FROM JobCreated jc
WHERE jc.client = jc.evaluator
GROUP BY jc.client, jc.provider
ORDER BY jobs DESC
LIMIT 10;
```

### Total self-evaluating addresses
```sql
SELECT COUNT(DISTINCT client) as unique_self_evaluators
FROM JobCreated
WHERE client = evaluator
  AND evaluator != '0x0000000000000000000000000000000000000000';
```
Result: 129

## 4. Finding 3 — Empty Deliverable Approved by Evaluator

### Background
`keccak256("") = 0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470`

This is the hash of an empty string.

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
Result:
total_empty_submitted: 398
completed_with_empty:  392   (98.5%)
expired_with_empty:    6

## 5. Finding 4 — Ecosystem Activity Concentration

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
Result:
0x22F70dAf4426Fe47D2ef4BE54C3ba7653Be01491  43858  69.7%
0x4e7C9Cec0C188C4f38f089E7843d750b7C3FAB46  11146  17.7%

## 6. Financial Scale
USDC on Base uses 6 decimal places.

```sql
SELECT SUM(CAST(amount AS REAL)) / 1e6 as total_usdc
FROM PaymentReleased;
```
Result: ~$625 USDC (full dataset)

Self-evaluation payments:
```sql
SELECT 
    COUNT(*) as self_eval_payments,
    SUM(CAST(pr.amount AS REAL)) / 1e6 as total_usdc
FROM PaymentReleased pr
JOIN JobCreated jc ON pr.job_id = jc.job_id
WHERE jc.client = jc.evaluator;
```
Result: 3,058 payments, ~$268 USDC
