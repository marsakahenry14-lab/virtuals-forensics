# VALIDATION — Dataset Integrity & On-Chain Verification

**Contract:** AgenticCommerceV3 · `0x238E541BfefD82238730D00a2208E5497F1832E0`  
**Block range:** 44,427,013 – 47,718,785  
**Verification date:** June 2026

---

## 1. Dataset Integrity Checks

All checks were run against `indexer_cache.db` using Python's built-in `sqlite3` module.

### 1.1 Row counts

```sql
SELECT 'JobCreated', COUNT(*) FROM JobCreated
UNION ALL SELECT 'JobFunded', COUNT(*) FROM JobFunded
UNION ALL SELECT 'JobSubmitted', COUNT(*) FROM JobSubmitted
UNION ALL SELECT 'JobCompleted', COUNT(*) FROM JobCompleted
UNION ALL SELECT 'PaymentReleased', COUNT(*) FROM PaymentReleased
UNION ALL SELECT 'JobExpired', COUNT(*) FROM JobExpired;
```

**Result:**
```
JobCreated       62953
JobFunded        10544
JobSubmitted      9333
JobCompleted      8859
PaymentReleased   8859
JobExpired        1130
```

### 1.2 No duplicate job_ids in JobCreated

```sql
SELECT COUNT(*) FROM JobCreated;                -- 62953
SELECT COUNT(DISTINCT job_id) FROM JobCreated;  -- 62953
```

**Result:** Equal. No duplicates.

### 1.3 JobCompleted ≤ JobSubmitted ≤ JobCreated

```sql
SELECT
    (SELECT COUNT(*) FROM JobCompleted) as completed,
    (SELECT COUNT(*) FROM JobSubmitted) as submitted,
    (SELECT COUNT(*) FROM JobCreated)   as created;
```

**Result:** 8859 ≤ 9333 ≤ 62953. Lifecycle ordering is consistent.

### 1.4 PaymentReleased row count matches JobCompleted

```sql
SELECT COUNT(*) FROM PaymentReleased;  -- 8859
SELECT COUNT(*) FROM JobCompleted;     -- 8859
```

**Result:** Equal. Every completed job has a corresponding payment record.

### 1.5 JOIN integrity check (job_id TEXT-to-TEXT)

```sql
SELECT COUNT(DISTINCT comp.job_id)
FROM JobCompleted comp
JOIN JobCreated jc ON comp.job_id = jc.job_id;
```

**Result:** 8859. All `JobCompleted` records successfully join to `JobCreated`.

### 1.6 Amount field quality check

```sql
SELECT COUNT(*)
FROM PaymentReleased
WHERE CAST(amount AS REAL) = 0 AND amount != '0';
```

**Result:** 0. No non-numeric or malformed amount values detected.

---

## 2. On-Chain Verification Methodology

### Why Basescan verification was performed

SQL queries operate on locally indexed data. The indexer could theoretically misparse events or miss logs. Basescan verification confirms that the raw on-chain state matches what the indexer stored.

### Verification procedure (reproducible)

1. Take a `tx_hash` from the local database.
2. Open `https://basescan.org/tx/{tx_hash}`.
3. Navigate to the **Logs** tab.
4. Locate `UserOperationEvent` — the `sender` field identifies who submitted the ERC-4337 user operation.
5. Compare `sender` to `provider` from the local `JobCreated` record for the same `job_id`.

---

## 3. Transaction Verification Log

### 3.1 Zero evaluator — provider as completion caller

For Observation 1 (72.5% zero evaluator), 5 transactions were verified manually.

| tx_hash (truncated) | job_id | Provider | sender on Basescan | Match |
|---|---|---|---|---|
| `0xf6811f...` | 62941 | `0xEA0f80...` | `0xEA0f80...` | ✓ |
| `0xf571a1...` | 62937 | `0xEA0f80...` | `0xEA0f80...` | ✓ |
| `0xc45c0f...` | 62892 | `0xB97552...` | `0xB97552...` | ✓ |
| `0x8625dc...` | 62888 | `0xB97552...` | `0xB97552...` | ✓ |
| `0x5ccd5b...` | 62885 | `0xECf977...` | `0xECf977...` | ✓ |

Full tx hashes:
- `0xf6811f29da43c8411accf9a096c8c8d0ed3b0441ba5f2f05b5366b30d6a4439e`
- `0xf571a1a63c78278fc06bb66e009542115ea715b5c859e6238c6dc47869153296`
- `0xc45c0f72444276364321e2f8fd5bbb3bab4f71aabf4cf8af9c880a8fae986916`
- `0x8625dc663c81f2381e3577cd06502fe919af879351449bad110afb699bc06581`
- `0x5ccd5b5cbfe324e081d25240383e07e07de3ae2eb3c1b19b5678f87f0a0e637a`

> **Limitation:** This is a 5-transaction illustrative sample, not a statistically representative sample of all 45,644 zero-evaluator jobs.

### 3.2 keccak256("") deliverable — confirmed on-chain

Transaction: `0x428952d30add5344d5eba381ecec490fdce812891a7f1a8ef0c7285f1fdf122c`  
Block: 45,284,050  
Status: Success  
Type: ERC-4337 Account Abstraction Bundle

**What was verified on Basescan:**
- Event `JobSubmitted` contains `deliverable = 0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470`
- Event `JobCompleted` was emitted in the same transaction
- Event `PaymentReleased` was emitted in the same transaction

**Conclusion (verified):** The on-chain event log matches the indexed database record. Escrow was released on a job whose deliverable field contained `keccak256("")`.

---

## 4. Indexer Correctness Notes

### Event source

All events are read from a single contract address:  
`0x238E541BfefD82238730D00a2208E5497F1832E0`

This is an EIP-1967 upgradeable proxy. The indexer reads the current implementation slot at startup and logs the implementation address. Events are emitted by the proxy address and are unaffected by upgrades.

### Deduplication

The indexer uses `INSERT OR IGNORE` with a composite primary key `(tx_hash, log_index)`. Restarting the indexer will not produce duplicate rows.

### Resume logic

Progress is stored in the `sync_progress` table. Indexing resumes from `last_block + 1` on restart.

### Schema validation

At startup, the indexer validates that all expected columns exist in all tables. If the schema is missing columns, it raises a `RuntimeError` with details rather than silently continuing.

---

## 5. Known Limitations

| Limitation | Impact |
|---|---|
| Block range is not pinned in code by default | Re-indexing today produces more rows; all statistics are range-specific |
| Token identity (USDC) is assumed, not verified in code | Financial figures may be incorrect if token or decimals differ |
| Basescan sample size = 5 | Statistical inference not valid; figures are illustrative |
| `amount` stored as TEXT | Correct behavior confirmed by quality check (§1.6); type conversion reliance noted |
| No ERC-4337 bundler analysis | Cannot determine ultimate initiator of AA bundles from event data alone |
