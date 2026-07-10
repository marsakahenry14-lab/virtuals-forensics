## §0. Contract Address Provenance

**Contract:** `0x238E541BfefD82238730D00a2208E5497F1832E0` (AgenticCommerceV3)
**Network:** Base Mainnet (chain id 8453)
**Provenance:** Identified via Basescan contract-creator tracing.
- **Creator:** Virtuals Protocol deployment (verified on Basescan).
- **Proxy type:** ERC-1967 upgradeable proxy.
- **Implementation:** `0x8e86FbEf4a4c927561cb6447cEd77ffFbf3B77BC`
- **Basescan:** https://basescan.org/address/0x238E541BfefD82238730D00a2208E5497F1832E0
- **Block range:** 44,429,969-47,715,550. Pinned manually via
  START_BLOCK/END_BLOCK; the observed event span is reported by the indexer integrity check.
- **ABI source:** Basescan "Contract" tab for the implementation.

---

# VALIDATION — Dataset Integrity & On-Chain Verification

**Contract:** AgenticCommerceV3 · `0x238E541BfefD82238730D00a2208E5497F1832E0`  
**Block range:** 44,429,969 - 47,715,550

---

## 1. Dataset Integrity Checks

All checks are designed to run against `indexer_cache.db` using Python's built-in `sqlite3` module.

### 1.1 Row counts

```sql
SELECT 'JobCreated', COUNT(*) FROM JobCreated
UNION ALL SELECT 'JobFunded', COUNT(*) FROM JobFunded
UNION ALL SELECT 'JobSubmitted', COUNT(*) FROM JobSubmitted
UNION ALL SELECT 'JobCompleted', COUNT(*) FROM JobCompleted
UNION ALL SELECT 'JobRejected', COUNT(*) FROM JobRejected
UNION ALL SELECT 'JobExpired', COUNT(*) FROM JobExpired
UNION ALL SELECT 'PaymentReleased', COUNT(*) FROM PaymentReleased;
```

**Expected report values:**

```text
JobCreated       62,953
JobFunded        10,544
JobSubmitted     9,333
JobCompleted     8,859
JobRejected      1,411
JobExpired       1,130
PaymentReleased  8,859
```

### 1.2 No duplicate `job_id` values in `JobCreated`

```sql
SELECT COUNT(*) FROM JobCreated;                -- 62,953
SELECT COUNT(DISTINCT job_id) FROM JobCreated;  -- 62,953
```

**Result target:** Counts should match.

### 1.3 Lifecycle ordering

```sql
SELECT
    (SELECT COUNT(*) FROM JobCompleted) AS completed,
    (SELECT COUNT(*) FROM JobSubmitted) AS submitted,
    (SELECT COUNT(*) FROM JobCreated) AS created;
```

**Expected ordering:** `8,859 <= 9,333 <= 62,953`

### 1.4 PaymentReleased row count matches JobCompleted

```sql
SELECT COUNT(*) FROM PaymentReleased;  -- 8,859
SELECT COUNT(*) FROM JobCompleted;     -- 8,859
```

**Result target:** Counts should match.

### 1.5 Join integrity checks

```sql
SELECT COUNT(*)
FROM JobCompleted comp
LEFT JOIN PaymentReleased pay ON pay.job_id = comp.job_id
WHERE pay.job_id IS NULL;

SELECT COUNT(*)
FROM PaymentReleased pay
LEFT JOIN JobCompleted comp ON comp.job_id = pay.job_id
WHERE comp.job_id IS NULL;
```

**Observed in generated metrics:**
- Completed without payment: 0
- Payment without completed: 0

### 1.6 Duplicate event identity check

```sql
SELECT tx_hash, log_index, COUNT(*)
FROM (
    SELECT tx_hash, log_index FROM JobCreated
    UNION ALL SELECT tx_hash, log_index FROM JobFunded
    UNION ALL SELECT tx_hash, log_index FROM JobSubmitted
    UNION ALL SELECT tx_hash, log_index FROM JobCompleted
    UNION ALL SELECT tx_hash, log_index FROM JobRejected
    UNION ALL SELECT tx_hash, log_index FROM JobExpired
    UNION ALL SELECT tx_hash, log_index FROM PaymentReleased
)
GROUP BY tx_hash, log_index
HAVING COUNT(*) > 1;
```

**Observed in generated metrics:** 0 duplicate `(tx_hash, log_index)` pairs.

---

## 2. On-Chain Verification Methodology

### Why Basescan verification is included

SQL queries operate on locally indexed data. Manual verification on Basescan confirms that decoded events in the local cache align with the public chain explorer.

### Verification procedure

1. Take a `tx_hash` from the local database.
2. Open [Basescan contract page](https://basescan.org/address/0x238E541BfefD82238730D00a2208E5497F1832E0) or navigate directly to the transaction page.
3. Inspect the transaction logs.
4. Compare the emitted event fields to the local SQLite rows for the same `job_id`.

---

## 3. Regeneration Flow

Use the following sequence when rebuilding the docs:

```bash
python indexer.py
python generate_report.py
```

`generate_report.py` reads `indexer_cache.db` in read-only mode, writes `report/metrics_output.json`, and renders the root markdown documents from `report/templates/*.md.tmpl`.

---

## 4. Notes

- Contract reference: [0x238E541BfefD82238730D00a2208E5497F1832E0](https://basescan.org/address/0x238E541BfefD82238730D00a2208E5497F1832E0)
- Chain: Base Mainnet (8453)
- Indexed block range: 44,429,969 - 47,715,550
