import time
import os
import sqlite3
import logging
from typing import Any, Dict, List, Optional, Tuple
import argparse

from dotenv import load_dotenv
from requests.exceptions import RequestException
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from web3 import Web3

load_dotenv()

try:
    from web3.middleware.proof_of_authority import ExtraDataToPOAMiddleware as _poa_middleware
except ImportError:
    try:
        from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware
    except ImportError:
        from web3.middleware import geth_poa_middleware as _poa_middleware

DEFAULT_RPC_URL = "https://mainnet.base.org"
PROXY_ADDRESS = "0x238E541BfefD82238730D00a2208E5497F1832E0"
DEFAULT_START_BLOCK = 44427013

DB_PATH = "indexer_cache.db"
BATCH_SIZE = 10000
SLEEP_BETWEEN_BATCHES_S = 0.05

EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_IMPLEMENTATION_SLOT_INT = int(EIP1967_IMPLEMENTATION_SLOT, 16)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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

UNIFIED_ABI: List[Dict[str, Any]] = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "client", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "provider", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "evaluator", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "expiredAt", "type": "uint256"},
            {"indexed": False, "internalType": "address", "name": "hook", "type": "address"},
        ],
        "name": "JobCreated",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "client", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "JobFunded",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "provider", "type": "address"},
            {"indexed": False, "internalType": "bytes32", "name": "deliverable", "type": "bytes32"},
        ],
        "name": "JobSubmitted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "evaluator", "type": "address"},
            {"indexed": False, "internalType": "bytes32", "name": "reason", "type": "bytes32"},
        ],
        "name": "JobCompleted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "rejector", "type": "address"},
            {"indexed": False, "internalType": "bytes32", "name": "reason", "type": "bytes32"},
        ],
        "name": "JobRejected",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [{"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"}],
        "name": "JobExpired",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "provider", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "PaymentReleased",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "evaluator", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "EvaluatorFeePaid",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": False, "internalType": "uint8", "name": "oldPhase", "type": "uint8"},
            {"indexed": False, "internalType": "uint8", "name": "phase", "type": "uint8"},
        ],
        "name": "JobPhaseUpdated",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "uint256", "name": "memoId", "type": "uint256"},
            {"indexed": False, "internalType": "bool", "name": "isApproved", "type": "bool"},
            {"indexed": False, "internalType": "string", "name": "reason", "type": "string"},
        ],
        "name": "MemoSigned",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "jobId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "memoId", "type": "uint256"},
            {"indexed": False, "internalType": "string", "name": "content", "type": "string"},
        ],
        "name": "NewMemo",
        "type": "event",
    },
]


def _to_hex(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value if value.startswith("0x") else ("0x" + value)
    return Web3.to_hex(value)


def _to_checksum_address(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str) and value == "":
        return ""
    return Web3.to_checksum_address(value)


def _to_uint_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return str(value)


def _is_rate_limit_or_timeout(exc: BaseException) -> bool:
    msg = _extract_error_text(exc)

    if isinstance(exc, (RequestException, TimeoutError, ConnectionError)):
        return True

    if "429" in msg:
        return True
    if "rate limit" in msg or ("rate" in msg and "limit" in msg):
        return True
    if "too many request" in msg:
        return True
    if "timeout" in msg or "timed out" in msg:
        return True

    if isinstance(exc, ValueError) and exc.args:
        payload = exc.args[0]
        if isinstance(payload, dict):
            code = payload.get("code")
            message = str(payload.get("message", "")).lower()
            if code == 429 or "429" in message:
                return True
            if "rate" in message and "limit" in message:
                return True
            if "timeout" in message or "timed out" in message:
                return True
            if code in (-32005, -32016):
                return True

    return False


def _extract_error_text(exc: BaseException) -> str:
    parts = [str(exc).lower()]

    if isinstance(exc, ValueError) and exc.args:
        payload = exc.args[0]
        if isinstance(payload, dict):
            code = payload.get("code")
            message = payload.get("message", "")
            data = payload.get("data", "")
            if code is not None:
                parts.append(str(code).lower())
            if message:
                parts.append(str(message).lower())
            if data:
                parts.append(str(data).lower())

    return " | ".join(part for part in parts if part)


def _should_split_range(exc: BaseException) -> bool:
    msg = _extract_error_text(exc)
    markers = (
        "413",
        "payload too large",
        "more than",
        "limit exceeded",
        "range is too large",
        "query returned more than",
        "-32005",
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "connection closed",
    )
    return any(marker in msg for marker in markers)


def _parse_optional_int_env(name: str) -> Optional[int]:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value == "":
        return None
    try:
        return int(raw_value)
    except ValueError as exc:
        raise SystemExit(f"Environment variable {name} must be an integer, got {raw_value!r}.") from exc


def _resolve_optional_int(cli_value: Optional[int], env_name: str, default: Optional[int]) -> Optional[int]:
    if cli_value is not None:
        return cli_value
    env_value = _parse_optional_int_env(env_name)
    if env_value is not None:
        return env_value
    return default


class BaseEventIndexer:
    def __init__(
        self,
        rpc_url: str,
        proxy_address: str,
        db_path: str,
        start_block: int,
        expected_jobcreated: Optional[int] = None,
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 45}))
        self.w3.middleware_onion.inject(_poa_middleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"RPC is not reachable: {rpc_url}")

        self.proxy_address = Web3.to_checksum_address(proxy_address)
        self.contract = self.w3.eth.contract(address=self.proxy_address, abi=UNIFIED_ABI)
        self.db_path = db_path
        self.start_block = int(start_block)
        self.expected_jobcreated = expected_jobcreated

        self._init_db()
        self._validate_db_schema()
        impl = self._read_current_implementation()
        logger.info(f"Proxy implementation: {impl}")

    def _validate_db_schema(self) -> None:
        expected: Dict[str, List[str]] = {
            "sync_progress": ["id", "last_block"],
            "JobCreated": [
                "tx_hash",
                "log_index",
                "block_number",
                "tx_index",
                "job_id",
                "client",
                "provider",
                "evaluator",
                "expired_at",
                "hook",
            ],
            "JobFunded": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "client", "amount"],
            "JobSubmitted": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "provider", "deliverable"],
            "JobCompleted": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "evaluator", "reason"],
            "JobRejected": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "rejector", "reason"],
            "JobExpired": ["tx_hash", "log_index", "block_number", "tx_index", "job_id"],
            "PaymentReleased": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "provider", "amount"],
            "EvaluatorFeePaid": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "evaluator", "amount"],
            "NewMemo": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "sender", "memo_id", "content"],
            "MemoSigned": ["tx_hash", "log_index", "block_number", "tx_index", "memo_id", "is_approved", "reason"],
            "JobPhaseUpdated": ["tx_hash", "log_index", "block_number", "tx_index", "job_id", "old_phase", "phase"],
        }

        missing: Dict[str, List[str]] = {}
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            for table_name, expected_cols in expected.items():
                cur.execute(f"PRAGMA table_info({table_name})")
                existing_cols = [row[1] for row in cur.fetchall()]
                existing_set = set(existing_cols)
                missing_cols = [c for c in expected_cols if c not in existing_set]
                if missing_cols:
                    missing[table_name] = missing_cols

        if missing:
            details = "; ".join([f"{t}: missing {cols}" for t, cols in missing.items()])
            raise RuntimeError(
                "SQLite schema mismatch detected for indexer_cache.db. "
                "This indexer will not modify the existing DB schema. "
                "Create a manual copy of the database if you need to inspect or rebuild it safely. "
                f"Details: {details}"
            )

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_progress (
                    id INTEGER PRIMARY KEY,
                    last_block INTEGER NOT NULL
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobCreated (
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
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobFunded (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    client TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobSubmitted (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    deliverable TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobCompleted (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    evaluator TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobRejected (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    rejector TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobExpired (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS PaymentReleased (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS EvaluatorFeePaid (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    evaluator TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS NewMemo (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    memo_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS MemoSigned (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    memo_id TEXT NOT NULL,
                    is_approved INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS JobPhaseUpdated (
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    block_number INTEGER NOT NULL,
                    tx_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    old_phase INTEGER NOT NULL,
                    phase INTEGER NOT NULL,
                    PRIMARY KEY (tx_hash, log_index)
                )
                """
            )

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCreated_job_id ON JobCreated(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCreated_client ON JobCreated(client)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCreated_provider ON JobCreated(provider)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCreated_evaluator ON JobCreated(evaluator)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCreated_client_provider ON JobCreated(client, provider)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobFunded_job_id ON JobFunded(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobFunded_client ON JobFunded(client)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobSubmitted_job_id ON JobSubmitted(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobSubmitted_provider ON JobSubmitted(provider)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCompleted_job_id ON JobCompleted(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobCompleted_evaluator ON JobCompleted(evaluator)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobRejected_job_id ON JobRejected(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobRejected_rejector ON JobRejected(rejector)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobExpired_job_id ON JobExpired(job_id)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_PaymentReleased_job_id ON PaymentReleased(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_PaymentReleased_provider ON PaymentReleased(provider)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_EvaluatorFeePaid_job_id ON EvaluatorFeePaid(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_EvaluatorFeePaid_evaluator ON EvaluatorFeePaid(evaluator)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_NewMemo_job_id ON NewMemo(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_NewMemo_sender ON NewMemo(sender)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_NewMemo_memo_id ON NewMemo(memo_id)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_MemoSigned_memo_id ON MemoSigned(memo_id)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_JobPhaseUpdated_job_id ON JobPhaseUpdated(job_id)")

            conn.commit()

    def _read_current_implementation(self) -> str:
        raw = self.w3.eth.get_storage_at(self.proxy_address, EIP1967_IMPLEMENTATION_SLOT_INT)
        impl = "0x" + raw[-20:].hex()
        return Web3.to_checksum_address(impl)

    def _get_resume_block(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_block FROM sync_progress WHERE id = 1")
            row = cursor.fetchone()
            if not row:
                return self.start_block
            last_block = int(row[0])
            if last_block < self.start_block:
                return self.start_block
            return last_block + 1

    def _set_last_processed_block(self, block_number: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sync_progress (id, last_block) VALUES (1, ?)",
                (int(block_number),),
            )
            conn.commit()

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(8),
        retry=retry_if_exception(_is_rate_limit_or_timeout),
        reraise=True,
    )
    def _fetch_event_logs(self, event_name: str, from_block: int, to_block: int) -> List[Any]:
        event_cls = getattr(self.contract.events, event_name)
        return event_cls.get_logs(from_block=from_block, to_block=to_block)

    def _fetch_with_payload_fallback(self, event_name: str, from_block: int, to_block: int) -> List[Any]:
        try:
            return self._fetch_event_logs(event_name, from_block, to_block)
        except Exception as e:
            if not _should_split_range(e):
                raise
            if from_block >= to_block:
                raise RuntimeError(
                    f"RPC failed even after recursive fallback for {event_name} at single block {from_block}: {e}"
                ) from e
            logger.warning(f"Splitting {event_name} range {from_block}..{to_block} after RPC error: {e}")

        mid = (from_block + to_block) // 2
        left = self._fetch_with_payload_fallback(event_name, from_block, mid)
        right = self._fetch_with_payload_fallback(event_name, mid + 1, to_block)
        return left + right

    def _event_count_and_block_span(self, cursor: sqlite3.Cursor, table_name: str) -> Tuple[int, Optional[int], Optional[int]]:
        cursor.execute(f"SELECT COUNT(*), MIN(block_number), MAX(block_number) FROM {table_name}")
        count, min_block, max_block = cursor.fetchone()
        return int(count or 0), min_block, max_block

    def _find_range_gaps(
        self,
        expected_from_block: int,
        expected_to_block: int,
        processed_ranges: List[Tuple[int, int]],
    ) -> List[Tuple[int, int]]:
        if not processed_ranges:
            return []

        normalized = sorted(processed_ranges)
        gaps: List[Tuple[int, int]] = []
        cursor_block = expected_from_block
        for start, end in normalized:
            if start > cursor_block:
                gaps.append((cursor_block, start - 1))
            cursor_block = max(cursor_block, end + 1)
        if cursor_block <= expected_to_block:
            gaps.append((cursor_block, expected_to_block))
        return gaps

    def _count_duplicate_tx_log_pairs(self, cursor: sqlite3.Cursor) -> int:
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

    def _print_integrity_report(
        self,
        expected_from_block: Optional[int],
        expected_to_block: Optional[int],
        processed_ranges: List[Tuple[int, int]],
    ) -> None:
        logger.info("=== INTEGRITY REPORT ===")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            overall_min: Optional[int] = None
            overall_max: Optional[int] = None
            for table_name in EVENT_TABLES:
                count, min_block, max_block = self._event_count_and_block_span(cursor, table_name)
                logger.info(
                    f"{table_name}: rows={count}, min_block={min_block if min_block is not None else 'n/a'}, "
                    f"max_block={max_block if max_block is not None else 'n/a'}"
                )
                if min_block is not None:
                    overall_min = min(min_block, overall_min) if overall_min is not None else min_block
                if max_block is not None:
                    overall_max = max(max_block, overall_max) if overall_max is not None else max_block

            logger.info(
                "overall_block_coverage: min_block=%s, max_block=%s",
                overall_min if overall_min is not None else "n/a",
                overall_max if overall_max is not None else "n/a",
            )

            duplicate_pairs = self._count_duplicate_tx_log_pairs(cursor)
            logger.info("duplicate_(tx_hash,log_index)_pairs=%s", duplicate_pairs)

            if expected_from_block is not None and expected_to_block is not None and processed_ranges:
                gaps = self._find_range_gaps(expected_from_block, expected_to_block, processed_ranges)
                if gaps:
                    logger.error("processed_block_range_gaps=%s", gaps)
                else:
                    logger.info("processed_block_range_gaps=none")
            else:
                logger.info("processed_block_range_gaps=not evaluated in this invocation")

    def _assert_expected_jobcreated(self) -> None:
        if self.expected_jobcreated is None:
            return
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM JobCreated")
            actual_count = int(cursor.fetchone()[0] or 0)
        if actual_count != self.expected_jobcreated:
            raise RuntimeError(
                f"EXPECTED_JOBCREATED mismatch: expected {self.expected_jobcreated}, got {actual_count}. "
                "This usually indicates an incomplete or inconsistent dataset."
            )
        logger.info("EXPECTED_JOBCREATED matched: %s", actual_count)

    def _save_event_rows(self, event_name: str, logs: List[Any]) -> int:
        if not logs:
            return 0

        rows_written = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for log in logs:
                args = log["args"]

                tx_hash = _to_hex(log["transactionHash"])
                log_index = int(log["logIndex"])
                block_number = int(log["blockNumber"])
                tx_index = int(log["transactionIndex"])

                if event_name == "JobCreated":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobCreated
                        (tx_hash, log_index, block_number, tx_index, job_id, client, provider, evaluator, expired_at, hook)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["client"]),
                            _to_checksum_address(args["provider"]),
                            _to_checksum_address(args["evaluator"]),
                            _to_uint_str(args["expiredAt"]),
                            _to_checksum_address(args["hook"]),
                        ),
                    )
                elif event_name == "JobFunded":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobFunded
                        (tx_hash, log_index, block_number, tx_index, job_id, client, amount)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["client"]),
                            _to_uint_str(args["amount"]),
                        ),
                    )
                elif event_name == "JobSubmitted":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobSubmitted
                        (tx_hash, log_index, block_number, tx_index, job_id, provider, deliverable)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["provider"]),
                            _to_hex(args["deliverable"]),
                        ),
                    )
                elif event_name == "JobCompleted":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobCompleted
                        (tx_hash, log_index, block_number, tx_index, job_id, evaluator, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["evaluator"]),
                            _to_hex(args["reason"]),
                        ),
                    )
                elif event_name == "JobRejected":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobRejected
                        (tx_hash, log_index, block_number, tx_index, job_id, rejector, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["rejector"]),
                            _to_hex(args["reason"]),
                        ),
                    )
                elif event_name == "JobExpired":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobExpired
                        (tx_hash, log_index, block_number, tx_index, job_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                        ),
                    )
                elif event_name == "PaymentReleased":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO PaymentReleased
                        (tx_hash, log_index, block_number, tx_index, job_id, provider, amount)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["provider"]),
                            _to_uint_str(args["amount"]),
                        ),
                    )
                elif event_name == "EvaluatorFeePaid":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO EvaluatorFeePaid
                        (tx_hash, log_index, block_number, tx_index, job_id, evaluator, amount)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["evaluator"]),
                            _to_uint_str(args["amount"]),
                        ),
                    )
                elif event_name == "NewMemo":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO NewMemo
                        (tx_hash, log_index, block_number, tx_index, job_id, sender, memo_id, content)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            _to_checksum_address(args["sender"]),
                            _to_uint_str(args["memoId"]),
                            str(args["content"]),
                        ),
                    )
                elif event_name == "MemoSigned":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO MemoSigned
                        (tx_hash, log_index, block_number, tx_index, memo_id, is_approved, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["memoId"]),
                            1 if bool(args["isApproved"]) else 0,
                            str(args["reason"]),
                        ),
                    )
                elif event_name == "JobPhaseUpdated":
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO JobPhaseUpdated
                        (tx_hash, log_index, block_number, tx_index, job_id, old_phase, phase)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tx_hash,
                            log_index,
                            block_number,
                            tx_index,
                            _to_uint_str(args["jobId"]),
                            int(args["oldPhase"]),
                            int(args["phase"]),
                        ),
                    )
                else:
                    continue

                rows_written += cursor.rowcount
            conn.commit()

        return rows_written

    def run(
        self,
        batch_size: int = BATCH_SIZE,
        sleep_s: float = SLEEP_BETWEEN_BATCHES_S,
        end_block: Optional[int] = None,
    ) -> None:
        batch_size = int(batch_size)
        if batch_size <= 0:
            raise ValueError("--batch-size must be a positive integer")

        latest_block = int(self.w3.eth.block_number)
        target_block = min(int(end_block), latest_block) if end_block else latest_block
        from_block = self._get_resume_block()

        if from_block > target_block:
            logger.info(f"Nothing to index. from_block={from_block} target_block={target_block} latest_block={latest_block}")
            self._print_integrity_report(None, None, [])
            self._assert_expected_jobcreated()
            return

        logger.info(f"Indexing {from_block}..{target_block}")
        processed_ranges: List[Tuple[int, int]] = []

        batch_start = from_block
        while batch_start <= target_block:
            batch_end = min(batch_start + batch_size - 1, target_block)
            logger.info(f"Batch {batch_start}..{batch_end}")

            for event_name in EVENT_TABLES:
                logs = self._fetch_with_payload_fallback(event_name, batch_start, batch_end)
                if logs:
                    written = self._save_event_rows(event_name, logs)
                    logger.info(f"{event_name}: {len(logs)} logs, {written} inserted")

            self._set_last_processed_block(batch_end)
            processed_ranges.append((batch_start, batch_end))
            time.sleep(float(sleep_s))
            batch_start = batch_end + 1

        logger.info("Indexing complete.")
        self._print_integrity_report(from_block, target_block, processed_ranges)
        self._assert_expected_jobcreated()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--proxy", default=PROXY_ADDRESS)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--start-block", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_BATCHES_S)
    parser.add_argument("--end-block", type=int, default=None)
    parser.add_argument("--unbounded", action="store_true")
    args = parser.parse_args()

    rpc_url = args.rpc_url or os.getenv("BASE_RPC_URL") or DEFAULT_RPC_URL
    start_block = _resolve_optional_int(args.start_block, "START_BLOCK", DEFAULT_START_BLOCK)
    end_block = _resolve_optional_int(args.end_block, "END_BLOCK", None)
    expected_jobcreated = _resolve_optional_int(None, "EXPECTED_JOBCREATED", None)

    if end_block is None and not args.unbounded:
        raise SystemExit(
            "REFUSING TO RUN AN UNBOUNDED INDEX.\n"
            "Set END_BLOCK in .env, pass --end-block explicitly, or use --unbounded if you intentionally want a moving target."
        )

    indexer = BaseEventIndexer(
        rpc_url=rpc_url,
        proxy_address=args.proxy,
        db_path=args.db,
        start_block=start_block,
        expected_jobcreated=expected_jobcreated,
    )
    indexer.run(batch_size=args.batch_size, sleep_s=args.sleep, end_block=end_block)
