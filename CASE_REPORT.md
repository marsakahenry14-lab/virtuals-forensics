# Case Report: Systematic Evaluator Bypass in AgenticCommerceV3 (Virtuals ACP)

**Investigator:** Marsel Sultanov — Independent on-chain forensics
**Target:** AgenticCommerceV3 escrow — `0x238E541BfefD82238730D00a2208E5497F1832E0`
**Chain:** Base Mainnet (chain id 8453)
**Scope:** 62,953 jobs · blocks 44,427,013 – 47,718,785
**Method:** deterministic event indexer + SQL + Basescan verification — fully reproducible
**Data:** https://github.com/marsakahenry14-lab/virtuals-forensics (`RESEARCH.md`, `VALIDATION.md`)

---

## Summary

AgenticCommerceV3 is Virtuals Protocol's on-chain agent-commerce escrow — the production predecessor to ERC-8183. It releases payment to a provider once a job's deliverable is approved by an *evaluator*.

Across all 62,953 jobs on Base Mainnet, independent evaluation is effectively absent: in 99.98% of jobs the evaluator is either unset or is the client itself. In 392 cases a deliverable that was provably empty was approved and paid anyway.

The on-chain record shows a structural pattern in which the party paying for work also controls whether that work is accepted. This report documents the behavior and its scale. It does **not** assert intent, identity, or fraud — those cannot be established from on-chain data alone.

---

## Finding 1 — Independent evaluation is effectively absent

Of 62,953 `JobCreated` events:

- **45,644 (72.50%)** — evaluator = zero address (no evaluator set)
- **17,299 (27.48%)** — evaluator = client (the payer approves its own job)
- **10 (0.02%)** — evaluator is an independent third party

The approver is the payer, or no one, in virtually every job. 212 distinct addresses appear as both `client` and `evaluator`.

## Finding 2 — Provably empty deliverables were approved and paid

The submitted deliverable is a `bytes32` hash. 398 jobs submitted `0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470` — `keccak256("")`, the hash of an empty string, i.e. no deliverable content.

- **392 of 398 (98.49%)** were approved (`JobCompleted`) and escrow was released.

Work that verifiably contained nothing was accepted and paid in 98% of the cases where it was submitted.

## Finding 3 — Concentration

- Top client `0x22F70dAf4426Fe47D2ef4BE54C3ba7653Be01491` — **43,858 jobs (69.67%)**
- Top client→provider pair `0x22F70dAf…01491` → `0xD6A5093213…B0261` — **21,937 jobs**
- Top two clients — **87.38%** of all jobs

Activity is dominated by a single actor operating both sides of a client→provider relationship at scale.

## Value

**$353.21** total USDC released; **$268.71 (~76%)** flowed through jobs where client == evaluator.

> **Materiality (stated plainly):** the dollar amounts here are small — this is a low-value production deployment. The significance is the *structural pattern and its scale*, not financial loss. The same pattern in a funded agent-reputation system (e.g. live ERC-8004 deployments) is where it becomes consequential.

---

## Why it matters

Agent-commerce and agent-reputation standards (ERC-8004, ERC-8183) delegate "evaluator trust" to each implementation. This dataset is an empirical picture of what *unsolved* looks like in production: no enforced role separation, self-approval as the norm, and empty work paid out. It is a concrete, reproducible reference case for anyone designing or auditing on-chain agent trust.

## Reproducibility

Indexer, SQL queries, and Basescan verification steps are in the repository. Every figure regenerates from the pinned block range (44,427,013 – 47,718,785). Contract on Basescan: `https://basescan.org/address/0x238E541BfefD82238730D00a2208E5497F1832E0`

---

*Optional strongest example — verify before including:* if the single-address pattern (one address approving 226 of 237 empty deliverables) is reproducible, add its SQL to `RESEARCH.md` and cite it here. It is the sharpest single illustration of the finding, but only if it regenerates from the data.
