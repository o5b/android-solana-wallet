import csv
import io

from solana.history_csv import CSV_COLUMNS, transaction_history_to_csv


def main():
    exported = transaction_history_to_csv([
        ("devnet", [{
            "signature": "sol-only",
            "block_time": 0,
            "tx_type": "SOL Transfer",
            "success": True,
            "sol_change": "0.001",
            "fee": "0.000005",
            "slot": 123,
            "version": "legacy",
            "compute_units": 150,
            "spl_changes": [],
        }]),
        ("mainnet-beta", [{
            "signature": "token-change",
            "block_time": 1,
            "tx_type": "Token Transfer",
            "success": False,
            "sol_change": 0,
            "fee": 0.000005,
            "slot": 456,
            "version": 0,
            "compute_units": 900,
            "spl_changes": [
                {"mint": "MintOne", "symbol": "TOK", "change": 2},
                {"mint": "MintTwo", "change": -3},
            ],
        }]),
    ])

    rows = list(csv.DictReader(io.StringIO(exported)))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert len(rows) == 3
    assert rows[0]["timestamp_utc"] == "1970-01-01T00:00:00Z"
    assert rows[0]["token_mint"] == ""
    assert rows[1]["network"] == "mainnet-beta"
    assert rows[1]["token_symbol"] == "TOK"
    assert rows[2]["token_mint"] == "MintTwo"
    assert rows[2]["token_symbol"] == ""
    assert rows[2]["status"] == "Failed"
    print("history CSV tests passed")


if __name__ == "__main__":
    main()
