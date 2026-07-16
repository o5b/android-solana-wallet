"""Address-poisoning detection (anti-phishing).

A self-contained, transport/UI-agnostic module that decides whether a *recipient
address the user is about to send to* looks suspicious against a set of
"known" addresses (the user's address book + their own wallets + addresses seen
in their transaction history).

Address poisoning attack model
------------------------------
An attacker watches the chain, sees the victim send to a frequent recipient R,
then airdrops a tiny amount FROM an address R' that *visually resembles* R
(same first/last few characters, or R' is R with zero-width / homoglyph chars
injected) TO the victim. The victim later copies R' out of their own history
(by accident, because it looks like the real R) and sends real funds to the
attacker.

Defenses implemented here
-------------------------
1. **Hidden-character detection** — zero-width / format / BOM characters and any
   non-base58 character in the raw input. A real Solana address is pure base58;
   *any* other byte is an immediate red flag (often an injected invisible char
   meant to make two addresses render identically).
2. **Look-alike detection** against known addresses using:
   - common prefix / suffix length (classic poisoning shares the head and tail),
   - Levenshtein edit distance (typo-squat / single-char swaps),
   - a combined suspicion score → severity (danger / warning).

Nothing here touches the network, storage, or UI, so it is fully unit-testable.
The result is a plain dict consumed by `src/main.py` to render a live banner and
a blocking confirmation gate before transfers.
"""

# The Solana / Bitcoin base58 alphabet (excludes 0, O, I, l to avoid confusion).
BASE58_ALPHABET = frozenset(
    "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
)

# Invisible / format characters that render as nothing (or as a base58 glyph)
# but are NOT part of a valid address. Injecting one of these between two
# visually identical addresses is the cheapest poisoning trick.
HIDDEN_CHARS = {
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x200E,  # LEFT-TO-RIGHT MARK
    0x200F,  # RIGHT-TO-LEFT MARK
    0x2060,  # WORD JOINER
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM)
    0x00AD,  # SOFT HYPHEN
    0x2061,  # FUNCTION APPLICATION
    0x2062,  # INVISIBLE TIMES
    0x2063,  # INVISIBLE SEPARATOR
    0x2064,  # INVISIBLE PLUS
}

# Min/max length of a base58-encoded Solana address (32-byte pubkey → 32..44 chars).
MIN_ADDRESS_LEN = 32
MAX_ADDRESS_LEN = 44


def normalize_address(address: str) -> str:
    """Strip whitespace and known hidden characters.

    This is the "what the user probably *meant*" form: it removes the invisible
    padding an attacker may have injected so that subsequent look-alike math runs
    against clean base58.
    """
    if not address:
        return ""
    out = []
    for ch in address:
        if ord(ch) in HIDDEN_CHARS:
            continue
        if ch.isspace():
            continue
        out.append(ch)
    return "".join(out)


def hidden_char_positions(address: str) -> list:
    """Return [(codepoint, index)] for every hidden/format char in the raw input.

    Indexing is over the *original* string so the UI can pinpoint where the
    injection happened.
    """
    found = []
    if not address:
        return found
    for i, ch in enumerate(address):
        if ord(ch) in HIDDEN_CHARS:
            found.append((ord(ch), i))
    return found


def invalid_chars(address: str) -> list:
    """Return the set of characters that are not part of the base58 alphabet.

    Only meaningful for a non-empty, already-normalized string. Useful to flag
    homoglyphs (e.g. Cyrillic "а", Latin "0"/"O") that survive normalization.
    """
    if not address:
        return []
    seen = []
    for ch in address:
        if ch not in BASE58_ALPHABET and ch not in seen:
            seen.append(ch)
    return seen


def is_base58_address(address: str) -> bool:
    """True if `address` is non-empty, pure base58, and plausibly Solana-sized."""
    if not address:
        return False
    if not (MIN_ADDRESS_LEN <= len(address) <= MAX_ADDRESS_LEN):
        return False
    return all(ch in BASE58_ALPHABET for ch in address)


def levenshtein(a: str, b: str) -> int:
    """Standard iterative edit distance (insert/delete/substitute = 1 each)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = cur
    return prev[-1]


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _common_suffix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[-1 - i] == b[-1 - i]:
        i += 1
    return i


def _classify_pair(addr: str, known: str) -> tuple:
    """Compare two *normalized* addresses (addr != known) → (reasons, severity, score).

    `score` is 0..100 (higher = more suspicious). `severity` is one of
    "danger" / "warning" / None.
    """
    reasons = []
    severity = None
    score = 0
    if not addr or not known or addr == known:
        return reasons, severity, score

    p = _common_prefix(addr, known)
    s = _common_suffix(addr, known)
    dist = levenshtein(addr, known)
    maxlen = max(len(addr), len(known))

    # Classic poisoning: identical head AND tail, different middle.
    if p >= 4 and s >= 4:
        score = 100
        severity = "danger"
        reasons.append(
            f"Shares the first {p} and last {s} characters with a known address "
            f"(classic poisoning pattern) but differs in the middle."
        )
        return reasons, severity, score

    # Same head or tail + very few edits.
    if (p >= 4 or s >= 4) and dist <= 6:
        score = 88
        severity = "danger"
        reasons.append(
            f"Matches {p} leading / {s} trailing characters and is only {dist} "
            f"edits away from a known address."
        )
        return reasons, severity, score

    # Moderate: shares a 3-char head/tail, small-ish distance.
    if (p >= 3 or s >= 3) and dist <= 8:
        score = 60
        severity = "warning"
        reasons.append(
            f"Shares {p} leading / {s} trailing characters with a known address "
            f"({dist} edits apart)."
        )
    elif dist <= 4 and maxlen >= MIN_ADDRESS_LEN:
        # Near-identical but no obvious head/tail anchor — still worth flagging.
        score = 55
        severity = "warning"
        reasons.append(
            f"Only {dist} characters different from a known address "
            f"(possible typo or look-alike)."
        )
    elif p >= 3 or s >= 3:
        score = 35
        severity = "warning"
        reasons.append(
            f"Shares {p} leading / {s} trailing characters with a known address."
        )

    return reasons, severity, score


def _coerce_known(item) -> tuple:
    """Normalize a known-address entry to (address, label).

    Accepts a bare string or a dict with any of:
    address / address_base58 / recipient, label / name.
    """
    if item is None:
        return "", None
    if isinstance(item, str):
        return item, None
    if isinstance(item, dict):
        addr = (
            item.get("address")
            or item.get("address_base58")
            or item.get("recipient")
            or ""
        )
        label = item.get("label") or item.get("name") or item.get("note")
        return addr, label
    return "", None


def check_address_poisoning(address: str, known_items=None) -> dict:
    """Inspect `address` against `known_items` for poisoning risk.

    Args:
        address: the raw recipient string the user entered/pasted.
        known_items: iterable of known addresses (strings) or dicts
            ``{"address": ..., "label": ...}`` (address book / own wallets /
            history counterparties).

    Returns a dict::

        {
          "input": "<original>",
          "normalized": "<stripped>",
          "valid": bool,             # pure base58 + Solana-sized
          "hidden_chars": bool,      # raw input had invisible chars
          "hidden_positions": [(codepoint, idx), ...],
          "invalid_chars": [str, ...],  # non-base58 chars after normalize (homoglyphs)
          "exact": {"address","label"} | None,   # an exact known match (positive)
          "warnings": [
             {"label","address","reasons":[...],"severity":"danger"|"warning","score":int},
             ...
          ],
          "has_danger": bool,
          "has_warning": bool,
        }

    The function never raises; on empty/garbage input it returns a structured
    result with ``valid=False`` and no warnings.
    """
    original = address or ""
    normalized = normalize_address(original)
    hidden = hidden_char_positions(original)
    inv = invalid_chars(normalized)
    valid = is_base58_address(normalized)

    result = {
        "input": original,
        "normalized": normalized,
        "valid": valid,
        "hidden_chars": bool(hidden),
        "hidden_positions": [(cp, idx) for cp, idx in hidden],
        "invalid_chars": inv,
        "exact": None,
        "warnings": [],
        "has_danger": False,
        "has_warning": False,
    }

    known_list = list(known_items or [])
    if not known_list:
        # Even with no known addresses we still flag injected invisible chars.
        if result["hidden_chars"] or inv:
            result["warnings"].append({
                "label": None,
                "address": normalized,
                "reasons": ["Contains hidden / non-base58 characters."],
                "severity": "danger",
                "score": 100,
            })
            result["has_danger"] = True
        return result

    norm_known = []
    for item in known_list:
        kaddr, klabel = _coerce_known(item)
        if not kaddr:
            continue
        norm_known.append((normalize_address(kaddr), kaddr, klabel))

    # 1) Exact match (a saved contact / own wallet) — positive, not a warning.
    if normalized:
        for knorm, kaddr, klabel in norm_known:
            if knorm == normalized:
                result["exact"] = {"address": kaddr, "label": klabel}
                break

    # 2) Standalone danger from injected chars, regardless of known set.
    if result["hidden_chars"]:
        cps = ", ".join(f"U+{cp:04X}" for cp, _ in hidden)
        result["warnings"].append({
            "label": None,
            "address": normalized,
            "reasons": [
                f"Contains invisible/format characters ({cps}) that are not part "
                f"of a real address — commonly used to fake a look-alike address."
            ],
            "severity": "danger",
            "score": 100,
        })
        result["has_danger"] = True
    elif inv:
        result["warnings"].append({
            "label": None,
            "address": normalized,
            "reasons": [
                f"Contains non-base58 characters ({', '.join(inv)}) — a real Solana "
                f"address uses only base58. This may be a homoglyph look-alike."
            ],
            "severity": "danger",
            "score": 95,
        })
        result["has_danger"] = True

    # 3) Look-alike comparisons (only meaningful between two base58-shaped strings).
    if valid and normalized:
        for knorm, kaddr, klabel in norm_known:
            if not knorm or knorm == normalized:
                continue
            if not (MIN_ADDRESS_LEN <= len(knorm) <= MAX_ADDRESS_LEN):
                continue
            reasons, severity, score = _classify_pair(normalized, knorm)
            if severity:
                result["warnings"].append({
                    "label": klabel,
                    "address": kaddr,
                    "reasons": reasons,
                    "severity": severity,
                    "score": score,
                })
                if severity == "danger":
                    result["has_danger"] = True
                else:
                    result["has_warning"] = True

    return result
