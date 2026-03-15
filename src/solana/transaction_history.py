import httpx
import pprint
import asyncio

from solana.balance import get_account_info, get_metadata_pda, decode_metadata, parse_metadata_metaplex, parse_metadata_2022_program_id

# Добавляем async
async def get_transaction_history(wallet_address: str, network_url: str, limit: int = 10):
    try:
        headers = {"Content-Type": "application/json"}

        # 1. Fetch recent signatures
        payload_sigs = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                wallet_address,
                {"limit": limit}
            ]
        }

        # Используем AsyncClient как асинхронный контекстный менеджер
        async with httpx.AsyncClient() as client:
            sig_response = await client.post(network_url, json=payload_sigs, headers=headers)
            if sig_response.status_code != 200:
                return {"error": f"Failed to get signatures: {sig_response.status_code}"}

            sig_data = sig_response.json()
            # pprint.pp(f'sig_data: {sig_data}')
            if "error" in sig_data:
                return {"error": sig_data["error"]["message"]}

            signatures = [item["signature"] for item in sig_data.get("result", [])]
            if not signatures:
                return {"result": []}

            # 2. Формируем список payload для всех транзакций
            payload_txs = [
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "getTransaction",
                    "params": [
                        sig,
                        {"encoding": "json", "maxSupportedTransactionVersion": 0}
                    ]
                }
                for i, sig in enumerate(signatures)
            ]

            # Вспомогательная асинхронная функция для одного запроса
            async def fetch_tx(payload):
                resp = await client.post(network_url, json=payload, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                return None

            # 3. Запускаем все запросы транзакций ПАРАЛЛЕЛЬНО!
            # asyncio.gather выполнит их одновременно и вернет результаты в том же порядке
            tx_responses = await asyncio.gather(*(fetch_tx(payload) for payload in payload_txs))

            parsed_txs = []

            for i, tx_res in enumerate(tx_responses):
                if not tx_res or "error" in tx_res:
                    continue

                tx = tx_res.get("result")
                if not tx:
                    continue

                meta = tx.get("meta", {})

                acc_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

                wallet_index = -1
                for idx, key in enumerate(acc_keys):
                    key_pubkey = key if isinstance(key, str) else key.get("pubkey")
                    if key_pubkey == wallet_address:
                        wallet_index = idx
                        break

                sol_change = 0
                if wallet_index != -1 and meta:
                    pre_bals = meta.get("preBalances", [])
                    post_bals = meta.get("postBalances", [])
                    if wallet_index < len(pre_bals) and wallet_index < len(post_bals):
                        sol_change = (post_bals[wallet_index] - pre_bals[wallet_index]) / (10**9)

                # --- ЛОГИКА ДЛЯ SPL-ТОКЕНОВ ---
                pre_token_balances = meta.get("preTokenBalances", [])
                post_token_balances = meta.get("postTokenBalances", [])

                token_changes = {}

                # Собираем балансы ДО транзакции для нашего кошелька
                for ptb in pre_token_balances:
                    if ptb.get("owner") == wallet_address:
                        mint = ptb.get("mint")
                        amount = ptb.get("uiTokenAmount", {}).get("uiAmount", 0) or 0
                        token_changes[mint] = {"pre": amount, "post": 0}

                # Собираем балансы ПОСЛЕ транзакции для нашего кошелька
                for ptb in post_token_balances:
                    if ptb.get("owner") == wallet_address:
                        mint = ptb.get("mint")
                        amount = ptb.get("uiTokenAmount", {}).get("uiAmount", 0) or 0
                        if mint not in token_changes:
                            token_changes[mint] = {"pre": 0, "post": amount}
                        else:
                            token_changes[mint]["post"] = amount

                # Высчитываем разницу (поступление или трата)
                spl_transfers = []
                for mint, balances in token_changes.items():
                    diff = balances["post"] - balances["pre"]
                    if diff != 0:
                        spl_transfers.append({
                            "mint": mint,
                            "change": diff
                        })
                # --- КОНЕЦ ЛОГИКИ SPL ---

                block_time = tx.get("blockTime")

                # Извлекаем новые данные для детального вида
                slot = tx.get("slot")
                version = tx.get("version", "legacy")
                compute_units = meta.get("computeUnitsConsumed", 0)
                logs = meta.get("logMessages", [])

                # Простая эвристика для определения типа транзакции по логам
                tx_type = "Interaction"
                logs_joined = " ".join(logs) if logs else ""

                if "Instruction: InitializeMint" in logs_joined:
                    tx_type = "Token Mint"
                elif "Instruction: TransferChecked" in logs_joined or "Instruction: Transfer" in logs_joined:
                    tx_type = "Token Transfer"
                elif sol_change != 0 and not spl_transfers:
                    tx_type = "SOL Transfer"

                parsed_txs.append({
                    "signature": signatures[i],
                    "block_time": block_time,
                    "sol_change": sol_change,
                    "spl_changes": spl_transfers,
                    "success": meta.get("err") is None,
                    "fee": meta.get("fee", 0) / (10**9),
                    "slot": slot,
                    "version": version,
                    "compute_units": compute_units,
                    "logs": logs,
                    "tx_type": tx_type
                })

            # --- НОВОЕ: Получение символов токенов ---
            unique_mints = set()
            for tx_data in parsed_txs:
                for spl in tx_data.get("spl_changes", []):
                    unique_mints.add(spl["mint"])

            async def fetch_symbol(mint_address):
                try:
                    # 1. Пробуем получить метаданные Metaplex
                    metadata_pda = get_metadata_pda(mint_address)
                    acc_info = await get_account_info(metadata_pda, network_url)
                    if acc_info and 'data' in acc_info:
                        decoded = decode_metadata(acc_info['data'])
                        if decoded:
                            meta = parse_metadata_metaplex(decoded)
                            if meta.get("symbol"):
                                return mint_address, meta["symbol"]

                    # 2. Если не вышло, пробуем Token 2022
                    acc_info_2022 = await get_account_info(mint_address, network_url)
                    if acc_info_2022 and 'data' in acc_info_2022:
                        decoded_2022 = decode_metadata(acc_info_2022['data'])
                        if decoded_2022:
                            meta_2022 = parse_metadata_2022_program_id(decoded_2022)
                            if meta_2022.get("symbol"):
                                return mint_address, meta_2022["symbol"]
                except Exception:
                    pass
                return mint_address, None

            # Запускаем все запросы на символы параллельно
            if unique_mints:
                symbols_results = await asyncio.gather(*(fetch_symbol(m) for m in unique_mints))
                symbols_cache = {mint: sym for mint, sym in symbols_results if sym}
            else:
                symbols_cache = {}

            # Добавляем найденные символы в нашу историю
            for tx_data in parsed_txs:
                for spl in tx_data.get("spl_changes", []):
                    spl["symbol"] = symbols_cache.get(spl["mint"])
            # --- КОНЕЦ НОВОГО БЛОКА ---

            return {"result": parsed_txs}

    except Exception as e:
        return {"error": str(e)}
