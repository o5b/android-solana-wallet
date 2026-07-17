"""NFT gallery data layer.

Reuses the balance.py metadata machinery (Metaplex + Token-2022 parsing,
off-chain JSON fetch) to collect an address's NFTs across networks, without
the slow per-token paths that the balance screen needs but a gallery does not
(transfer-cost estimation, raw image-byte downloads). Images are returned as
URLs (IPFS `ipfs://` links are rewritten to an HTTPS gateway) so the UI can
load them directly.

NFT detection heuristic (same as Phantom/Solflare): an SPL/Token-2022 holding
with ``decimals == 0`` and a non-zero balance. Master-edition prints and
semi-fungible 1/1 collections both satisfy this.
"""

import asyncio

from .balance import get_sol_spl_balance

# Public IPFS HTTP gateways; the first segment of `ipfs://<cid>/...` is the CID.
IPFS_GATEWAY = "https://ipfs.io/ipfs/"


def _normalize_image_url(url):
    """Rewrite an NFT image URL into something a browser/Flet Image can fetch.

    Handles ``ipfs://<cid>``, ``ipfs://ipfs/<cid>`` and bare ``ipfs/<cid>``,
    leaving Arweave (``https://arweave.net/...``) and plain https URLs intact.
    Returns '' for falsy/empty input.
    """
    if not url or not isinstance(url, str):
        return ""
    raw = url.strip()
    low = raw.lower()
    if low.startswith("ipfs://"):
        rest = raw[len("ipfs://"):]
    elif low.startswith("ipfs/"):
        rest = raw[len("ipfs/"):]
    else:
        return raw
    # Some URIs look like `ipfs://ipfs/<cid>` (double prefix).
    if rest.lower().startswith("ipfs/"):
        rest = rest[len("ipfs/"):]
    if not rest:
        return ""
    return IPFS_GATEWAY + rest


def _normalize_attributes(raw):
    """Normalize the Metaplex ``attributes`` array to ``[{trait_type, value}]``.

    Accepts the standard ``[{"trait_type": "...", "value": "..."}]`` shape as
    well as ``[{"trait_type": "...", "value": {"...": "..."}}]`` (some
    collections nest objects). Always returns a list of dicts with stringified
    values; never raises.
    """
    out = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            trait = item.get("trait_type") or item.get("trait") or ""
            value = item.get("value")
            if isinstance(value, (dict, list)):
                # Flatten nested values into "k: v" lines.
                if isinstance(value, dict):
                    value = ", ".join(f"{k}: {v}" for k, v in value.items())
                else:
                    value = ", ".join(str(v) for v in value)
            out.append({"trait_type": str(trait), "value": "" if value is None else str(value)})
    return out


def _collection_name(meta, token):
    """Best-effort collection label from metadata JSON + on-chain token data."""
    coll = meta.get("collection") if isinstance(meta, dict) else None
    if isinstance(coll, dict):
        if coll.get("name"):
            return str(coll["name"])
        if coll.get("family"):
            return str(coll["family"])
    if isinstance(coll, str) and coll:
        return coll
    # Fall back to on-chain symbol/name fields parsed from the PDA.
    for key in ("symbol_metaplex", "symbol_2022"):
        val = token.get(key)
        if val:
            return str(val)
    return meta.get("symbol") or ""


def is_nft_token(token: dict) -> bool:
    """True when a ``get_sol_spl_balance`` token record represents an NFT."""
    if not isinstance(token, dict):
        return False
    if token.get("decimals") != 0:
        return False
    amount = token.get("amount", 0)
    try:
        return float(amount) >= 1
    except (TypeError, ValueError):
        return False


def _build_nft(token: dict, network: str):
    """Convert a balance token record into a gallery NFT record."""
    meta = token.get("metadata_from_uri") or {}

    name = (meta.get("name") or token.get("name_metaplex")
            or token.get("name_2022") or "Unnamed NFT")
    symbol = meta.get("symbol") or token.get("symbol_metaplex") or token.get("symbol_2022") or ""

    image = _normalize_image_url(meta.get("image") or meta.get("image_url"))
    # Some collections put the primary media under properties.files[0].uri.
    if not image:
        files = meta.get("properties", {}).get("files") if isinstance(meta, dict) else None
        if isinstance(files, list) and files:
            first = files[0]
            if isinstance(first, dict):
                image = _normalize_image_url(first.get("uri") or first.get("url"))

    uri = token.get("uri_metaplex") or token.get("uri_2022") or meta.get("uri") or ""

    return {
        "mint": token.get("mint"),
        "network": network,
        "amount": token.get("amount", 1),
        "decimals": 0,
        "program_id": token.get("program_id"),
        "owner": token.get("owner"),
        "name": str(name) if name else "Unnamed NFT",
        "symbol": str(symbol) if symbol else "",
        "collection": _collection_name(meta, token),
        "image": image,
        "uri": str(uri) if uri else "",
        "description": str(meta.get("description") or "") if meta.get("description") else "",
        "attributes": _normalize_attributes(meta.get("attributes")),
        "external_url": str(meta.get("external_url") or "") if meta.get("external_url") else "",
        "metadata_from_uri": meta,
    }


async def get_nfts(address: str, networks: list) -> list:
    """Collect NFTs held by ``address`` across the given RPC ``networks``.

    Returns a flat list of NFT records (see ``_build_nft``). Fetching reuses
    ``get_sol_spl_balance`` with ``include_transfer_cost=False`` and
    ``include_image_bytes=False`` so galleries with many NFTs stay fast (no
    per-mint priority-fee RPC calls, no raw image downloads). Never raises — a
    failing network is skipped and an empty list is returned on hard failure.
    """
    nfts = []
    try:
        result = await get_sol_spl_balance(
            address,
            networks,
            include_transfer_cost=False,
            include_image_bytes=False,
        )
    except Exception as er:
        print(f"get_nfts: balance fetch failed: {er}")
        return []

    for r in result or []:
        network = r.get("network")
        for token in r.get("spl", []) or []:
            try:
                if not is_nft_token(token):
                    continue
                nfts.append(_build_nft(token, network))
            except Exception as er:
                print(f"get_nfts: skip token {token.get('mint')}: {er}")
    return nfts


def enrich_nft_image_urls(nfts: list, fetch_missing: bool = False) -> list:
    """Return the NFT list with normalized image URLs.

    When ``fetch_missing`` is True, NFTs without a resolved image URL have
    their off-chain metadata re-fetched (concurrently) in an attempt to recover
    a usable image (e.g. when the on-chain URI was parsed but JSON not yet
    loaded). This is best-effort and never raises.
    """
    if not fetch_missing:
        for n in nfts:
            n["image"] = _normalize_image_url(n.get("image"))
        return nfts

    from .balance import get_spl_token_data_from_uri

    async def _refetch(n):
        if n.get("image"):
            return
        uri = n.get("uri")
        if not uri:
            return
        try:
            meta = await get_spl_token_data_from_uri(uri)
        except Exception:
            meta = None
        if isinstance(meta, dict):
            n["image"] = _normalize_image_url(meta.get("image") or meta.get("image_url"))
            if not n.get("attributes"):
                n["attributes"] = _normalize_attributes(meta.get("attributes"))

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        # No running loop — caller is sync; just normalize what we have.
        for n in nfts:
            n["image"] = _normalize_image_url(n.get("image"))
        return nfts

    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.gather(*[_refetch(n) for n in nfts]))
    except Exception as er:
        print(f"enrich_nft_image_urls: refetch failed: {er}")
    return nfts
