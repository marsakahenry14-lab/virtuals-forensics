import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

DEFAULT_DB_PATH = "indexer_cache.db"
PINNED_START_BLOCK = 44427013
PINNED_END_BLOCK = 47718785
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
EMPTY_DELIVERABLE_HASH = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
USDC_DIVISOR = Decimal("1000000")
EVENT_TABLES = [
    "JobCreated",
    "JobFunded",
    "JobSubmitted",
    "JobCompleted",
    "JobRejected",
    "JobExpired",
    "PaymentReleased",
    "EvaluatorFeePaid",
    "NewMemo",
    "MemoSigned",
    "JobPhaseUpdated",
]


def _connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table_name,))
    return cursor.fetchone() is not None


def _safe_count(cursor: sqlite3.Cursor, table_name: str) -> int:
    if not _table_exists(cursor, table_name):
        return 0
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    row = cursor.fetchone()
    return int(row[0] or 0)


def _safe_fetchone(cursor: sqlite3.Cursor, query: str, params: tuple = ()) -> tuple:
    cursor.execute(query, params)
    row = cursor.fetchone()
    return row if row is not None else tuple()


def _pct(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((count / total) * 100, 2)


def _pct_text(count: int, total: int) -> str:
    return f"{_pct(count, total):.2f}"


def _format_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _format_int(value: int) -> str:
    return f"{value:,}"


def _sum_amounts(cursor: sqlite3.Cursor, query: str, params: tuple = ()) -> Decimal:
    total = Decimal(0)
    cursor.execute(query, params)
    for (amount_str,) in cursor.fetchall():
        if not amount_str:
            continue
        try:
            total += Decimal(str(amount_str))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return total


def _get_evaluator_breakdown(cursor: sqlite3.Cursor, cohort_table: str = "") -> dict:
    join_clause = ""
    if cohort_table:
        join_clause = f"JOIN {cohort_table} cohort ON cohort.job_id = jc.job_id"

    total_query = f"""
    SELECT COUNT(DISTINCT jc.job_id)
    FROM JobCreated jc
    {join_clause}
    """
    cursor.execute(total_query)
    total_jobs = int(cursor.fetchone()[0] or 0)

    def count_where(condition: str, params: tuple = ()) -> int:
        query = f"""
        SELECT COUNT(DISTINCT jc.job_id)
        FROM JobCreated jc
        {join_clause}
        WHERE {condition}
        """
        cursor.execute(query, params)
        return int(cursor.fetchone()[0] or 0)

    zero_count = count_where("LOWER(jc.evaluator) = ?", (ZERO_ADDRESS.lower(),))
    self_count = count_where(
        "LOWER(jc.client) = LOWER(jc.evaluator) AND LOWER(jc.evaluator) != ?",
        (ZERO_ADDRESS.lower(),),
    )
    independent_count = count_where(
        "LOWER(jc.evaluator) != ? AND LOWER(jc.evaluator) != LOWER(jc.client)",
        (ZERO_ADDRESS.lower(),),
    )

    return {
        "total_jobs": total_jobs,
        "zero_evaluator_count": zero_count,
        "zero_evaluator_pct": _pct(zero_count, total_jobs),
        "self_evaluator_nonzero_count": self_count,
        "self_evaluator_nonzero_pct": _pct(self_count, total_jobs),
        "independent_evaluator_count": independent_count,
        "independent_evaluator_pct": _pct(independent_count, total_jobs),
    }


def _get_event_funnel_from_cursor(cursor: sqlite3.Cursor) -> dict:
    return {
        "JobCreated": _safe_count(cursor, "JobCreated"),
        "JobFunded": _safe_count(cursor, "JobFunded"),
        "JobSubmitted": _safe_count(cursor, "JobSubmitted"),
        "JobCompleted": _safe_count(cursor, "JobCompleted"),
        "PaymentReleased": _safe_count(cursor, "PaymentReleased"),
        "JobExpired": _safe_count(cursor, "JobExpired"),
    }


def get_event_funnel(db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = None
    try:
        conn = _connect(db_path)
        return _get_event_funnel_from_cursor(conn.cursor())
    except Exception:
        return {
            "JobCreated": 0,
            "JobFunded": 0,
            "JobSubmitted": 0,
            "JobCompleted": 0,
            "PaymentReleased": 0,
            "JobExpired": 0,
        }
    finally:
        if conn:
            conn.close()


def _get_top_client_provider_pairs_from_cursor(cursor: sqlite3.Cursor, limit: int = 10) -> list:
    if not _table_exists(cursor, "JobCreated"):
        return []

    query = f"""
    SELECT jc.client, jc.provider, COUNT(*) AS created_count,
           COUNT(DISTINCT comp.job_id) AS completed_count
    FROM JobCreated jc
    LEFT JOIN JobCompleted comp ON jc.job_id = comp.job_id
    GROUP BY jc.client, jc.provider
    ORDER BY created_count DESC
    LIMIT {int(limit)}
    """
    cursor.execute(query)
    rows = cursor.fetchall()

    result = []
    for client, provider, created_count, completed_count in rows:
        result.append(
            {
                "client": client,
                "provider": provider,
                "created_count": int(created_count or 0),
                "completed_count": int(completed_count or 0),
            }
        )
    return result


def get_top_client_provider_pairs(db_path: str = DEFAULT_DB_PATH) -> list:
    conn = None
    try:
        conn = _connect(db_path)
        return _get_top_client_provider_pairs_from_cursor(conn.cursor())
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _get_empty_deliverables_from_cursor(cursor: sqlite3.Cursor) -> dict:
    result = {
        "total_empty_submitted": 0,
        "completed_with_empty": 0,
        "expired_with_empty": 0,
    }
    if not _table_exists(cursor, "JobSubmitted"):
        return result

    query = """
    SELECT COUNT(*) AS total_empty_submitted,
           COUNT(DISTINCT comp.job_id) AS completed_with_empty,
           COUNT(DISTINCT exp.job_id) AS expired_with_empty
    FROM JobSubmitted s
    LEFT JOIN JobCompleted comp ON s.job_id = comp.job_id
    LEFT JOIN JobExpired exp ON s.job_id = exp.job_id
    WHERE LOWER(s.deliverable) = ?
    """
    cursor.execute(query, (EMPTY_DELIVERABLE_HASH.lower(),))
    row = cursor.fetchone()

    if row:
        result["total_empty_submitted"] = int(row[0] or 0)
        result["completed_with_empty"] = int(row[1] or 0)
        result["expired_with_empty"] = int(row[2] or 0)
    return result


def get_empty_deliverables(db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = None
    try:
        conn = _connect(db_path)
        return _get_empty_deliverables_from_cursor(conn.cursor())
    except Exception:
        return {
            "total_empty_submitted": 0,
            "completed_with_empty": 0,
            "expired_with_empty": 0,
        }
    finally:
        if conn:
            conn.close()


def _get_evaluator_behavior_from_cursor(cursor: sqlite3.Cursor) -> list:
    if not _table_exists(cursor, "JobCreated"):
        return []

    has_job_rejected = _table_exists(cursor, "JobRejected")
    if has_job_rejected:
        query = """
        SELECT
            jc.evaluator,
            COUNT(DISTINCT comp.job_id) + COUNT(DISTINCT rej.job_id) AS total_evaluated,
            COUNT(DISTINCT comp.job_id) AS approved,
            COUNT(DISTINCT rej.job_id) AS rejected,
            COUNT(DISTINCT CASE WHEN LOWER(s.deliverable) = ? THEN comp.job_id END) AS empty_approved
        FROM JobCreated jc
        LEFT JOIN JobCompleted comp ON jc.job_id = comp.job_id AND LOWER(comp.evaluator) = LOWER(jc.evaluator)
        LEFT JOIN JobRejected rej ON jc.job_id = rej.job_id AND LOWER(rej.rejector) = LOWER(jc.evaluator)
        LEFT JOIN JobSubmitted s ON jc.job_id = s.job_id
        GROUP BY jc.evaluator
        HAVING total_evaluated >= 3
        ORDER BY total_evaluated DESC
        LIMIT 10
        """
        cursor.execute(query, (EMPTY_DELIVERABLE_HASH.lower(),))
    else:
        query = """
        SELECT
            jc.evaluator,
            COUNT(DISTINCT comp.job_id) AS total_evaluated,
            COUNT(DISTINCT comp.job_id) AS approved,
            0 AS rejected,
            COUNT(DISTINCT CASE WHEN LOWER(s.deliverable) = ? THEN comp.job_id END) AS empty_approved
        FROM JobCreated jc
        LEFT JOIN JobCompleted comp ON jc.job_id = comp.job_id AND LOWER(comp.evaluator) = LOWER(jc.evaluator)
        LEFT JOIN JobSubmitted s ON jc.job_id = s.job_id
        GROUP BY jc.evaluator
        HAVING total_evaluated >= 3
        ORDER BY total_evaluated DESC
        LIMIT 10
        """
        cursor.execute(query, (EMPTY_DELIVERABLE_HASH.lower(),))

    result = []
    for evaluator, total_evaluated, approved, rejected, empty_approved in cursor.fetchall():
        total_evaluated = int(total_evaluated or 0)
        approved = int(approved or 0)
        rejected = int(rejected or 0)
        empty_approved = int(empty_approved or 0)
        result.append(
            {
                "evaluator": evaluator,
                "total_evaluated": total_evaluated,
                "approved": approved,
                "rejected": rejected,
                "approval_rate_pct": _pct(approved, total_evaluated),
                "empty_approved": empty_approved,
            }
        )
    return result


def get_evaluator_behavior(db_path: str = DEFAULT_DB_PATH) -> list:
    conn = None
    try:
        conn = _connect(db_path)
        return _get_evaluator_behavior_from_cursor(conn.cursor())
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _get_structural_observations_from_cursor(cursor: sqlite3.Cursor) -> dict:
    created_breakdown = _get_evaluator_breakdown(cursor)

    cursor.execute(
        """
        SELECT COUNT(DISTINCT client), COUNT(DISTINCT job_id)
        FROM JobCreated
        WHERE LOWER(client) = LOWER(evaluator)
          AND LOWER(evaluator) != ?
        """,
        (ZERO_ADDRESS.lower(),),
    )
    unique_self_evaluators, self_eval_jobs = cursor.fetchone() or (0, 0)

    total_amount = Decimal(0)
    self_amount = Decimal(0)
    if _table_exists(cursor, "PaymentReleased"):
        total_amount = _sum_amounts(cursor, "SELECT amount FROM PaymentReleased")
        self_amount = _sum_amounts(
            cursor,
            """
            SELECT pr.amount
            FROM PaymentReleased pr
            JOIN JobCreated jc ON pr.job_id = jc.job_id
            WHERE LOWER(jc.client) = LOWER(jc.evaluator)
              AND LOWER(jc.evaluator) != ?
            """,
            (ZERO_ADDRESS.lower(),),
        )

    total_usdc_volume = total_amount / USDC_DIVISOR if total_amount else Decimal(0)
    self_eval_usdc_volume = self_amount / USDC_DIVISOR if self_amount else Decimal(0)

    return {
        "zero_evaluator_jobs": created_breakdown["zero_evaluator_count"],
        "zero_evaluator_percentage": created_breakdown["zero_evaluator_pct"],
        "unique_self_evaluators": int(unique_self_evaluators or 0),
        "self_eval_jobs": int(self_eval_jobs or 0),
        "independent_evaluator_jobs": created_breakdown["independent_evaluator_count"],
        "independent_evaluator_percentage": created_breakdown["independent_evaluator_pct"],
        "total_usdc_volume": _format_decimal(total_usdc_volume),
        "self_eval_usdc_volume": _format_decimal(self_eval_usdc_volume),
    }


def get_structural_observations(db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = None
    try:
        conn = _connect(db_path)
        return _get_structural_observations_from_cursor(conn.cursor())
    except Exception:
        return {
            "zero_evaluator_jobs": 0,
            "zero_evaluator_percentage": 0.0,
            "unique_self_evaluators": 0,
            "self_eval_jobs": 0,
            "independent_evaluator_jobs": 0,
            "independent_evaluator_percentage": 0.0,
            "total_usdc_volume": "0.00",
            "self_eval_usdc_volume": "0.00",
        }
    finally:
        if conn:
            conn.close()


def _get_top_clients_from_cursor(cursor: sqlite3.Cursor, limit: int = 3) -> list:
    if not _table_exists(cursor, "JobCreated"):
        return []

    total_jobs = _safe_count(cursor, "JobCreated")
    cursor.execute(
        f"""
        SELECT client, COUNT(*) AS total_created
        FROM JobCreated
        GROUP BY client
        ORDER BY total_created DESC
        LIMIT {int(limit)}
        """
    )
    rows = cursor.fetchall()
    result = []
    for client, total_created in rows:
        total_created = int(total_created or 0)
        result.append(
            {
                "client": client,
                "total_created": total_created,
                "pct": _pct(total_created, total_jobs),
            }
        )
    return result


def _get_duplicate_tx_log_pair_count(cursor: sqlite3.Cursor) -> int:
    duplicate_query = """
    SELECT COUNT(*) FROM (
        SELECT tx_hash, log_index
        FROM (
            SELECT tx_hash, log_index FROM JobCreated
            UNION ALL SELECT tx_hash, log_index FROM JobFunded
            UNION ALL SELECT tx_hash, log_index FROM JobSubmitted
            UNION ALL SELECT tx_hash, log_index FROM JobCompleted
            UNION ALL SELECT tx_hash, log_index FROM JobRejected
            UNION ALL SELECT tx_hash, log_index FROM JobExpired
            UNION ALL SELECT tx_hash, log_index FROM PaymentReleased
            UNION ALL SELECT tx_hash, log_index FROM EvaluatorFeePaid
            UNION ALL SELECT tx_hash, log_index FROM NewMemo
            UNION ALL SELECT tx_hash, log_index FROM MemoSigned
            UNION ALL SELECT tx_hash, log_index FROM JobPhaseUpdated
        )
        GROUP BY tx_hash, log_index
        HAVING COUNT(*) > 1
    )
    """
    cursor.execute(duplicate_query)
    row = cursor.fetchone()
    return int(row[0] or 0)


def get_report_metrics(db_path: str = DEFAULT_DB_PATH) -> dict:
    with _connect(db_path) as conn:
        cursor = conn.cursor()

        event_counts = {table: _safe_count(cursor, table) for table in EVENT_TABLES}
        funnel = _get_event_funnel_from_cursor(cursor)
        empty_deliverables = _get_empty_deliverables_from_cursor(cursor)
        evaluator_behavior = _get_evaluator_behavior_from_cursor(cursor)
        structural_observations = _get_structural_observations_from_cursor(cursor)
        top_pairs = _get_top_client_provider_pairs_from_cursor(cursor)
        top_clients = _get_top_clients_from_cursor(cursor)

        created_breakdown = _get_evaluator_breakdown(cursor)
        funded_breakdown = _get_evaluator_breakdown(cursor, "JobFunded")
        completed_breakdown = _get_evaluator_breakdown(cursor, "JobCompleted")

        cursor.execute("SELECT COUNT(DISTINCT job_id) FROM JobCreated")
        distinct_job_ids = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM JobCompleted comp
            LEFT JOIN PaymentReleased pay ON pay.job_id = comp.job_id
            WHERE pay.job_id IS NULL
            """
        )
        completed_without_payment = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM PaymentReleased pay
            LEFT JOIN JobCompleted comp ON comp.job_id = pay.job_id
            WHERE comp.job_id IS NULL
            """
        )
        payment_without_completed = int(cursor.fetchone()[0] or 0)

        observed_event_min_block = None
        observed_event_max_block = None
        for table_name in EVENT_TABLES:
            if not _table_exists(cursor, table_name):
                continue
            cursor.execute(f"SELECT MIN(block_number), MAX(block_number) FROM {table_name}")
            min_block, max_block = cursor.fetchone()
            if min_block is not None:
                observed_event_min_block = (
                    min_block
                    if observed_event_min_block is None
                    else min(observed_event_min_block, min_block)
                )
            if max_block is not None:
                observed_event_max_block = (
                    max_block
                    if observed_event_max_block is None
                    else max(observed_event_max_block, max_block)
                )

        total_payment_units = _sum_amounts(cursor, "SELECT amount FROM PaymentReleased")
        total_usdc_volume = total_payment_units / USDC_DIVISOR if total_payment_units else Decimal(0)
        payment_count = event_counts["PaymentReleased"]
        average_payment_usdc = (
            (total_usdc_volume / Decimal(payment_count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if payment_count
            else Decimal(0)
        )

        top_two_share_pct = 0.0
        if top_clients:
            top_two_share_pct = round(sum(client["pct"] for client in top_clients[:2]), 2)

        contract_address = "0x238E541BfefD82238730D00a2208E5497F1832E0"

        template_values = {
            "contract_name": "AgenticCommerceV3",
            "contract_address": contract_address,
            "chain_name": "Base Mainnet",
            "chain_id": "8453",
            "base_public_rpc_url": "https://mainnet.base.org",
            "recommended_rpc_provider": "Alchemy",
            "alchemy_free_tier_cu": "30M CU/month",
            "alchemy_free_tier_price": "$0",
            "basescan_contract_url": f"https://basescan.org/address/{contract_address}",
            "indexed_block_min": _format_int(PINNED_START_BLOCK),
            "indexed_block_max": _format_int(PINNED_END_BLOCK),
            "indexed_block_min_raw": str(PINNED_START_BLOCK),
            "indexed_block_max_raw": str(PINNED_END_BLOCK),
            "observed_event_min": _format_int(int(observed_event_min_block or 0)),
            "observed_event_max": _format_int(int(observed_event_max_block or 0)),
            "jobcreated_rows": _format_int(event_counts["JobCreated"]),
            "jobfunded_rows": _format_int(event_counts["JobFunded"]),
            "jobsubmitted_rows": _format_int(event_counts["JobSubmitted"]),
            "jobcompleted_rows": _format_int(event_counts["JobCompleted"]),
            "jobrejected_rows": _format_int(event_counts["JobRejected"]),
            "jobexpired_rows": _format_int(event_counts["JobExpired"]),
            "paymentreleased_rows": _format_int(event_counts["PaymentReleased"]),
            "evaluatorfeepaid_rows": _format_int(event_counts["EvaluatorFeePaid"]),
            "newmemo_rows": _format_int(event_counts["NewMemo"]),
            "memosigned_rows": _format_int(event_counts["MemoSigned"]),
            "jobphaseupdated_rows": _format_int(event_counts["JobPhaseUpdated"]),
            "total_jobs": _format_int(created_breakdown["total_jobs"]),
            "distinct_jobcreated_job_ids": _format_int(distinct_job_ids),
            "zero_evaluator_count": _format_int(created_breakdown["zero_evaluator_count"]),
            "zero_evaluator_pct": _pct_text(
                created_breakdown["zero_evaluator_count"],
                created_breakdown["total_jobs"],
            ),
            "self_evaluator_nonzero_count": _format_int(created_breakdown["self_evaluator_nonzero_count"]),
            "self_evaluator_nonzero_pct": _pct_text(
                created_breakdown["self_evaluator_nonzero_count"],
                created_breakdown["total_jobs"],
            ),
            "independent_evaluator_count": _format_int(created_breakdown["independent_evaluator_count"]),
            "independent_evaluator_pct": _pct_text(
                created_breakdown["independent_evaluator_count"],
                created_breakdown["total_jobs"],
            ),
            "funded_jobs_total": _format_int(funded_breakdown["total_jobs"]),
            "funded_zero_evaluator_count": _format_int(funded_breakdown["zero_evaluator_count"]),
            "funded_zero_evaluator_pct": _pct_text(
                funded_breakdown["zero_evaluator_count"],
                funded_breakdown["total_jobs"],
            ),
            "funded_self_evaluator_nonzero_count": _format_int(funded_breakdown["self_evaluator_nonzero_count"]),
            "funded_self_evaluator_nonzero_pct": _pct_text(
                funded_breakdown["self_evaluator_nonzero_count"],
                funded_breakdown["total_jobs"],
            ),
            "funded_independent_evaluator_count": _format_int(funded_breakdown["independent_evaluator_count"]),
            "funded_independent_evaluator_pct": _pct_text(
                funded_breakdown["independent_evaluator_count"],
                funded_breakdown["total_jobs"],
            ),
            "completed_jobs_total": _format_int(completed_breakdown["total_jobs"]),
            "completed_zero_evaluator_count": _format_int(completed_breakdown["zero_evaluator_count"]),
            "completed_zero_evaluator_pct": _pct_text(
                completed_breakdown["zero_evaluator_count"],
                completed_breakdown["total_jobs"],
            ),
            "completed_self_evaluator_nonzero_count": _format_int(completed_breakdown["self_evaluator_nonzero_count"]),
            "completed_self_evaluator_nonzero_pct": _pct_text(
                completed_breakdown["self_evaluator_nonzero_count"],
                completed_breakdown["total_jobs"],
            ),
            "completed_independent_evaluator_count": _format_int(completed_breakdown["independent_evaluator_count"]),
            "completed_independent_evaluator_pct": _pct_text(
                completed_breakdown["independent_evaluator_count"],
                completed_breakdown["total_jobs"],
            ),
            "unique_self_evaluators": _format_int(structural_observations["unique_self_evaluators"]),
            "total_usdc_volume": structural_observations["total_usdc_volume"],
            "self_eval_usdc_volume": structural_observations["self_eval_usdc_volume"],
            "average_payment_usdc": _format_decimal(average_payment_usdc),
            "empty_deliverable_hash": EMPTY_DELIVERABLE_HASH,
            "empty_deliverables_total": _format_int(empty_deliverables["total_empty_submitted"]),
            "empty_deliverables_completed": _format_int(empty_deliverables["completed_with_empty"]),
            "empty_deliverables_completed_pct": _pct_text(
                empty_deliverables["completed_with_empty"],
                empty_deliverables["total_empty_submitted"],
            ),
            "empty_deliverables_expired": _format_int(empty_deliverables["expired_with_empty"]),
            "empty_deliverables_expired_pct": _pct_text(
                empty_deliverables["expired_with_empty"],
                empty_deliverables["total_empty_submitted"],
            ),
            "top_client_one_address": top_clients[0]["client"] if len(top_clients) > 0 else "",
            "top_client_one_jobs": _format_int(top_clients[0]["total_created"]) if len(top_clients) > 0 else "0",
            "top_client_one_pct": f"{top_clients[0]['pct']:.2f}" if len(top_clients) > 0 else "0.00",
            "top_client_two_address": top_clients[1]["client"] if len(top_clients) > 1 else "",
            "top_client_two_jobs": _format_int(top_clients[1]["total_created"]) if len(top_clients) > 1 else "0",
            "top_client_two_pct": f"{top_clients[1]['pct']:.2f}" if len(top_clients) > 1 else "0.00",
            "top_two_clients_pct": f"{top_two_share_pct:.2f}",
            "top_pair_client": top_pairs[0]["client"] if top_pairs else "",
            "top_pair_provider": top_pairs[0]["provider"] if top_pairs else "",
            "top_pair_created_count": _format_int(top_pairs[0]["created_count"]) if top_pairs else "0",
            "top_pair_completed_count": _format_int(top_pairs[0]["completed_count"]) if top_pairs else "0",
            "completed_without_payment_count": _format_int(completed_without_payment),
            "payment_without_completed_count": _format_int(payment_without_completed),
            "duplicate_tx_log_pair_count": _format_int(_get_duplicate_tx_log_pair_count(cursor)),
            "lifecycle_completed_count": _format_int(event_counts["JobCompleted"]),
            "lifecycle_submitted_count": _format_int(event_counts["JobSubmitted"]),
            "lifecycle_created_count": _format_int(event_counts["JobCreated"]),
        }

        # INVARIANT: the three evaluator buckets must partition all created jobs.
        _total = created_breakdown["total_jobs"]
        _zero = created_breakdown["zero_evaluator_count"]
        _self = created_breakdown["self_evaluator_nonzero_count"]
        _indep = created_breakdown["independent_evaluator_count"]
        if _zero + _self + _indep != _total:
            raise AssertionError(
                f"Evaluator breakdown invariant violated: zero({_zero}) + self({_self}) "
                f"+ independent({_indep}) != total({_total}). Likely NULL evaluator rows "
                f"or a case-mismatch not covered by LOWER()."
            )

        return {
            "event_counts": event_counts,
            "funnel": funnel,
            "top_pairs": top_pairs,
            "top_clients": top_clients,
            "empty_deliverables": empty_deliverables,
            "evaluator_behavior": evaluator_behavior,
            "structural_observations": structural_observations,
            "created_breakdown": created_breakdown,
            "funded_breakdown": funded_breakdown,
            "completed_breakdown": completed_breakdown,
            "validation": {
                "distinct_jobcreated_job_ids": distinct_job_ids,
                "completed_without_payment_count": completed_without_payment,
                "payment_without_completed_count": payment_without_completed,
                "duplicate_tx_log_pair_count": _get_duplicate_tx_log_pair_count(cursor),
                "indexed_block_min": PINNED_START_BLOCK,
                "indexed_block_max": PINNED_END_BLOCK,
                "observed_event_min": int(observed_event_min_block or 0),
                "observed_event_max": int(observed_event_max_block or 0),
            },
            "template_values": template_values,
        }
