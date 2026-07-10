import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict

from metrics import get_report_metrics


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "indexer_cache.db"
REPORT_DIR = ROOT / "report"
TEMPLATES_DIR = REPORT_DIR / "templates"
METRICS_OUTPUT_PATH = REPORT_DIR / "metrics_output.json"

ROOT_DOCS = {
    "README.md.tmpl": ROOT / "README.md",
    "RESEARCH.md.tmpl": ROOT / "RESEARCH.md",
    "VALIDATION.md.tmpl": ROOT / "VALIDATION.md",
}

PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _verify_db_read_only(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        connection.execute("SELECT name FROM sqlite_master LIMIT 1")
    finally:
        connection.close()


def _load_template(template_path: Path) -> str:
    return template_path.read_text(encoding="utf-8")


def _render_template(template_text: str, values: Dict[str, str]) -> str:
    missing_keys = []

    def replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            missing_keys.append(key)
            return match.group(0)
        return str(values[key])

    rendered = PLACEHOLDER_RE.sub(replace, template_text)
    if missing_keys:
        raise ValueError(
            "Unresolved placeholders: "
            + ", ".join(sorted(set(missing_keys)))
            + ". metrics.py must provide every key used in the template."
        )
    return rendered


def _write_json(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _render_docs(template_values: Dict[str, str]) -> None:
    for template_name, output_path in ROOT_DOCS.items():
        template_path = TEMPLATES_DIR / template_name
        if not template_path.exists():
            continue

        rendered = _render_template(_load_template(template_path), template_values)
        output_path.write_text(rendered, encoding="utf-8")


def main() -> int:
    try:
        _verify_db_read_only(DB_PATH)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Report generation skipped. Root docs were not modified.", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Error: unable to open database read-only: {exc}", file=sys.stderr)
        print("Report generation skipped. Root docs were not modified.", file=sys.stderr)
        return 1

    report_metrics = get_report_metrics(str(DB_PATH))
    template_values = dict(report_metrics["template_values"])
    _write_json(METRICS_OUTPUT_PATH, report_metrics)
    _render_docs(template_values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
