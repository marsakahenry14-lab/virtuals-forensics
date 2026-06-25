import time
import os
import sqlite3
import logging
from typing import Any, Dict, List, Optional, Tuple
import argparse

from requests.exceptions import RequestException
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from web3 import Web3

try:
    from web3.middleware.proof_of_authority import ExtraDataToPOAMiddleware as _poa_middleware
except ImportError:
    try:
        from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware
    except ImportError:
        from web3.middleware import geth_poa_middleware as _poa_middleware

RPC_URL = "https://mainnet.base.org"
PROXY_ADDRESS = "0x238E541BfefD82238730D00a2208E5497F1832E0"
START_BLOCK = 44427013

DB_PATH = "indexer_cache.db"
BATCH_SIZE = 50000
SLEEP_BETWEEN_BATCHES_S = 0.05

EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_IMPLEMENTATION_SLOT_INT = int(EIP1967_IMPLEMENTATION_SLOT, 16)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
    if isinstance(exc, (RequestException, TimeoutError, ConnectionError)):
        return True

    msg = str(exc).lower()
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


def _is_payload_too_large(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "413" in msg or "payload too large" in msg


class BaseEventIndexer:
    def __init__(self, rpc_url: str, proxy_address: str, db_path: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 45}))
        self.w3.middleware_onion.inject(_poa_middleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"RPC is not reachable: {rpc_url}")

        self.proxy_address = Web3.to_checksum_address(proxy_address)
        self.contract = self.w3.eth.contract(address=self.proxy_address, abi=UNIFIED_ABI)
        self.db_path = db_path

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
                "Delete the DB file or run with --reset-db to recreate tables. "
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
                return START_BLOCK
            last_block = int(row[0])
            if last_block < START_BLOCK:
                return START_BLOCK
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
            if not _is_payload_too_large(e):
                raise

        mid = (from_block + to_block) // 2
        if mid < from_block or mid >= to_block:
            raise
        left = self._fetch_with_payload_fallback(event_name, from_block, mid)
        right = self._fetch_with_payload_fallback(event_name, mid + 1, to_block)
        return left + right

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

    def run(self, batch_size: int = BATCH_SIZE, sleep_s: float = SLEEP_BETWEEN_BATCHES_S) -> None:
        batch_size = int(batch_size)
        if batch_size <= 0:
            raise ValueError("--batch-size must be a positive integer")

        latest_block = int(self.w3.eth.block_number)
        from_block = self._get_resume_block()

        if from_block > latest_block:
            logger.info(f"Nothing to index. from_block={from_block} latest_block={latest_block}")
            return

        logger.info(f"Indexing {from_block}..{latest_block}")

        events = [
            "JobCreated",
            "JobFunded",
            "JobSubmitted",
            "JobCompleted",
            "JobExpired",
            "PaymentReleased",
            "EvaluatorFeePaid",
            "NewMemo",
            "MemoSigned",
            "JobPhaseUpdated",
        ]

        batch_start = from_block
        while batch_start <= latest_block:
            batch_end = min(batch_start + batch_size - 1, latest_block)
            logger.info(f"Batch {batch_start}..{batch_end}")

            for event_name in events:
                logs = self._fetch_with_payload_fallback(event_name, batch_start, batch_end)
                if logs:
                    written = self._save_event_rows(event_name, logs)
                    logger.info(f"{event_name}: {len(logs)} logs, {written} inserted")

            self._set_last_processed_block(batch_end)
            time.sleep(float(sleep_s))
            batch_start = batch_end + 1

        logger.info("Indexing complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=RPC_URL)
    parser.add_argument("--proxy", default=PROXY_ADDRESS)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_BATCHES_S)
    parser.add_argument("--reset-db", action="store_true")
    args = parser.parse_args()

    if args.reset_db and os.path.exists(args.db):
        os.remove(args.db)

    indexer = BaseEventIndexer(args.rpc_url, args.proxy, args.db)
    indexer.run(batch_size=args.batch_size, sleep_s=args.sleep)
