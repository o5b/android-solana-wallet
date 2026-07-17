"""CSV export helpers for normalized transaction-history records."""

import csv
import io
from datetime import datetime, timezone
from typing import Any, Iterable


CSV_COLUMNS = (
    "network",
    "timestamp_utc",
    "signature",
    "type",
    "status",
    "sol_change",
    "fee_sol",
    "token_mint",
    "token_symbol",
    "token_change",
    "slot",
    "version",
    "compute_units",
)


def _timestamp_utc(block_time: Any) -> str:
    if block_time is None:
        return ""
    try:
        return datetime.fromtimestamp(int(block_time), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def transaction_history_to_csv(network_history: Iterable[tuple[str, Iterable[dict[str, Any]]]]) -> str:
    """Return RFC-compliant CSV with one row per SOL transaction or SPL delta."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()

    for network, transactions in network_history:
        for transaction in transactions:
            base_row = {
                "network": network,
                "timestamp_utc": _timestamp_utc(transaction.get("block_time")),
                "signature": transaction.get("signature", ""),
                "type": transaction.get("tx_type", ""),
                "status": "Success" if transaction.get("success") else "Failed",
                "sol_change": transaction.get("sol_change", 0),
                "fee_sol": transaction.get("fee", 0),
                "slot": transaction.get("slot", ""),
                "version": transaction.get("version", ""),
                "compute_units": transaction.get("compute_units", ""),
            }
            spl_changes = transaction.get("spl_changes") or []
            if not spl_changes:
                writer.writerow(base_row)
                continue

            for spl_change in spl_changes:
                writer.writerow({
                    **base_row,
                    "token_mint": spl_change.get("mint", ""),
                    "token_symbol": spl_change.get("symbol", ""),
                    "token_change": spl_change.get("change", 0),
                })

    return output.getvalue()
