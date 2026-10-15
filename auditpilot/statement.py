"""Statement parsing: bank/credit-card CSV or pasted text -> transactions.

Supported input shapes (auto-detected):
1. CSV with a header row: date, description, amount  (also debit/credit pairs)
2. CSV without a header: date-ish column, text column, money-ish column
3. Pasted text lines: "01/03/2026 SQ *JOES COFFEE #221 4.50"

Rules that matter:
- Charge direction is inferred from the majority sign of a single signed
  amount column (most banks list debits negative); explicit debit/credit
  columns are trusted as-is.
- Unparseable rows are counted, never silently dropped and never guessed.
- Every transaction keeps its source line number so findings can cite it.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------
# header aliases
# --------------------------------------------------------------------------

HEADER_ALIASES = {
    "date": {
        "date", "transaction date", "trans date", "posted", "posting date",
        "date posted", "time", "datetime", "value date", "payment date",
    },
    "description": {
        "description", "desc", "merchant", "payee", "details", "name",
        "memo", "narrative", "transaction", "transaction details", "summary",
        "payee name",
    },
    "amount": {"amount", "amt", "value", "sum", "transaction amount"},
    "debit": {
        "debit", "debits", "withdrawal", "withdrawals", "money out",
        "paid out", "spend", "debit amount",
    },
    "credit": {
        "credit", "credits", "deposit", "deposits", "money in", "paid in",
        "received", "credit amount",
    },
}

_MONEY_TOKEN = re.compile(
    r"[(\[]?\s*(?:\$|€|£|¥)?\s*-?\s*\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\s*[)\]]?"
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# merchant tokens that carry no identity
_LOCATION_TOKENS = {
    "US", "USA", "CA", "NY", "UK", "EU", "COM", "LLC", "INC", "CO",
    "CORP", "LTD", "INT", "INTL", "PLC", "GMBH", "SVCS", "SERVICES",
    "PAYMENT", "PAYMENTS", "PURCHASE", "POS", "CARD", "DEBIT", "ACH",
    "ONLINE", "STORE", "SHOP",
}

# For unsigned exports (every amount positive) the charge direction cannot
# be read from signs, so rows that clearly describe money coming *in* are
# excluded from the charge set. Deliberately narrow to avoid false hits.
_INCOME_KEYWORDS = (
    "SALARY", "PAYROLL", "PAYCHECK", "PAYCHEQUE", "DIRECT DEPOSIT",
    "DIRECT DEP", "PAYMENT RECEIVED", "REFUND", "INTEREST", "DIVIDEND",
    "TAX REFUND", "TRANSFER IN",
)


def _is_income(description: str) -> bool:
    d = description.upper()
    return any(k in d for k in _INCOME_KEYWORDS)


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------

@dataclass
class Txn:
    date: date
    description: str
    amount: float          # signed, as written in the source
    line_no: int           # 1-based line number in the input text
    raw: str               # the original row text

    def as_evidence(self) -> dict:
        return {
            "line": self.line_no,
            "date": self.date.isoformat(),
            "description": self.description,
            "amount": round(self.amount, 2),
            "raw": self.raw.strip()[:120],
        }


@dataclass
class ParseResult:
    transactions: List[Txn] = field(default_factory=list)
    skipped: int = 0
    mode: str = "text"                    # "csv" | "text"
    charge_sign: int = 1                  # +1: positive amounts are charges

    def charges(self) -> List[Txn]:
        """Only money-out rows (charges). Income/refunds are excluded."""
        out = []
        for t in self.transactions:
            if t.amount * self.charge_sign <= 0:
                continue
            if self.charge_sign > 0 and _is_income(t.description):
                continue          # unsigned export: salary is not a subscription
            out.append(t)
        return out


# --------------------------------------------------------------------------
# money & date primitives
# --------------------------------------------------------------------------

def parse_money(token: str) -> Optional[float]:
    """'($1,234.50)' -> -1234.5 ; '15.99' -> 15.99 ; junk -> None."""
    s = token.strip()
    if not s:
        return None
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("[") and s.endswith("]")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in {"-", ".", "-."}:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _find_money_tokens(text: str) -> List[Tuple[float, int, int]]:
    out = []
    for m in _MONEY_TOKEN.finditer(text):
        v = parse_money(m.group(0))
        if v is not None and abs(v) > 0.0001:
            out.append((v, m.start(), m.end()))
    return out


def _try_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date_str(s: str) -> Optional[date]:
    """Parse a standalone date string in common formats."""
    s = s.strip().strip(",")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        return _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # US default MM/DD; fall back to DD/MM when impossible
        return _try_date(y, a, b) if a <= 12 else _try_date(y, b, a)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", s)
    if m:
        a, b, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = 2000 + yy
        return _try_date(y, a, b) if a <= 12 else _try_date(y, b, a)
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        mo = _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            return _try_date(int(m.group(3)), mo, int(m.group(2)))
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m:
        mo = _MONTHS.get(m.group(2)[:3].lower())
        if mo:
            return _try_date(int(m.group(3)), mo, int(m.group(1)))
    return None


_DATE_PATTERNS = [
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),
    re.compile(r"\d{1,2}/\d{1,2}/\d{4}"),
    re.compile(r"\d{1,2}/\d{1,2}/\d{2}"),
    re.compile(r"\d{4}/\d{1,2}/\d{1,2}"),
    re.compile(r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"),
    re.compile(r"\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}"),
]


def find_date(text: str) -> Tuple[Optional[date], int, int]:
    """First date-like substring -> (date, start, end)."""
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            d = parse_date_str(m.group(0))
            if d is not None:
                return d, m.start(), m.end()
    return None, -1, -1


# --------------------------------------------------------------------------
# merchant normalization
# --------------------------------------------------------------------------

def merchant_key(description: str) -> str:
    """'SQ *JOES COFFEE #221 01/03' -> 'JOES'; 'NETFLIX.COM' -> 'NETFLIX'."""
    s = description.upper()
    s = re.sub(r"\b(SQ|TST|PP|SP|AMZN|VISA|MC|ACH|DEBIT|PURCHASE|POS|CARD)\b\s*\*?", " ", s)
    s = re.sub(r"#\s*\d+", " ", s)                 # store numbers
    s = re.sub(r"\b\d{4,}\b", " ", s)              # long digit runs
    s = re.sub(r"\b\d{1,2}/\d{1,2}(\/\d{2,4})?\b", " ", s)  # embedded dates
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t not in _LOCATION_TOKENS]
    if not tokens:
        tokens = s.split()
    return tokens[0] if tokens else "UNKNOWN"


def display_name(description: str) -> str:
    """Human-friendly merchant label: first meaningful words, title-cased."""
    s = re.sub(r"\s+", " ", description).strip(" *|-")
    s = s.replace("*", " ")
    s = re.sub(
        r"^(SQ|TST|PP|SP|AMZN|VISA|MC|ACH|DEBIT|PURCHASE|POS|CARD)\s+", " ", s, flags=re.I
    )
    s = re.sub(r"#\s*\d+", " ", s)
    s = re.sub(r"\b\d{4,}\b", " ", s)
    words = []
    for w in s.split():
        w = w.strip(".,")
        if not w or w.upper() in _LOCATION_TOKENS:
            continue
        if not re.search(r"[A-Za-z]", w):        # pure numbers/punctuation
            continue
        w = re.sub(r"\.(COM|NET|ORG)$", "", w, flags=re.I)
        if w:
            words.append(w)
    label = " ".join(words[:2]) if words else s.strip()
    return label.title() if (label.isupper() or label.isdigit()) else label


# --------------------------------------------------------------------------
# CSV parsing
# --------------------------------------------------------------------------

def _norm_header_cell(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.strip().strip("\ufeff").lower())


def _map_header(row: List[str]) -> Optional[dict]:
    mapping = {}
    for idx, cell in enumerate(row):
        key = None
        for canon, aliases in HEADER_ALIASES.items():
            if _norm_header_cell(cell) in aliases:
                key = canon
                break
        if key and key not in mapping:
            mapping[key] = idx
    if "date" in mapping and ("amount" in mapping or "debit" in mapping):
        return mapping
    return None


def _looks_like_csv(lines: List[str]) -> bool:
    if len(lines) < 2:
        return False
    comma_lines = sum(1 for ln in lines if "," in ln)
    return comma_lines / len(lines) >= 0.6


def _parse_csv(text: str, lines: List[str]) -> Tuple[List[Txn], int]:
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], 0
    mapping = _map_header(rows[0]) if len(rows) > 1 else None
    start = 1 if mapping else 0
    if not mapping and len(rows) >= 4:
        # no recognized header: maybe there is an *unrecognized* header row
        # (e.g. "Trans Date / Payee / Amount") — never count it as skipped.
        if not _row_has_data(rows[0]) and _row_has_data(rows[1]):
            mapping = _infer_columns(rows[1:])
            start = 1
        else:
            mapping = _infer_columns(rows)
            start = 0
    if not mapping:
        return _parse_text_lines(lines)

    txns, skipped = [], 0
    for offset, row in enumerate(rows[start:]):
        line_no = start + offset + 1
        raw = ",".join(row)
        try:
            d = parse_date_str(row[mapping["date"]])
            desc = row[mapping["description"]].strip() if "description" in mapping else ""
            if "debit" in mapping or "credit" in mapping:
                debit = parse_money(row[mapping["debit"]]) if "debit" in mapping else None
                credit = parse_money(row[mapping["credit"]]) if "credit" in mapping else None
                amount = (debit or 0.0) - (credit or 0.0)
            else:
                amount = parse_money(row[mapping["amount"]])
        except (IndexError, ValueError):
            skipped += 1
            continue
        if d is None or amount is None or amount == 0 or not desc:
            skipped += 1
            continue
        txns.append(Txn(date=d, description=desc, amount=amount, line_no=line_no, raw=raw))
    return txns, skipped


def _row_has_data(row: List[str]) -> bool:
    """True if any cell looks like a date or a money amount."""
    for cell in row:
        cell = cell.strip()
        if cell and (parse_date_str(cell) is not None or parse_money(cell) is not None):
            return True
    return False


def _infer_columns(rows: List[List[str]]) -> Optional[dict]:
    """Headerless CSV: guess date / description / amount columns by ratios."""
    ncols = max(len(r) for r in rows)
    if ncols < 3:
        return None
    scores = {"date": [0] * ncols, "amount": [0] * ncols, "text": [0] * ncols}
    for row in rows:
        for i in range(min(ncols, len(row))):
            cell = row[i].strip()
            if not cell:
                continue
            if parse_date_str(cell) is not None:
                scores["date"][i] += 1
            if parse_money(cell) is not None:
                scores["amount"][i] += 1
            if re.search(r"[A-Za-z]{3}", cell):
                scores["text"][i] += 1
    date_idx = _best(scores["date"], len(rows), 0.5)
    amount_idx = _best(scores["amount"], len(rows), 0.5)
    if date_idx is None or amount_idx is None or date_idx == amount_idx:
        return None
    text_idx = _best(scores["text"], len(rows), 0.5, exclude={date_idx, amount_idx})
    if text_idx is None:
        return None
    return {"date": date_idx, "description": text_idx, "amount": amount_idx}


def _best(counts, total, ratio, exclude=None):
    exclude = exclude or set()
    for i, c in enumerate(counts):
        if i in exclude:
            continue
        if c > ratio * total:
            return i
    return None


# --------------------------------------------------------------------------
# pasted-text parsing
# --------------------------------------------------------------------------

def _parse_text_lines(raw_lines: List[str]) -> Tuple[List[Txn], int]:
    """Pasted text: 'MM/DD/YYYY DESCRIPTION AMOUNT' per line.

    Line numbers refer to the original text (blank lines are skipped but
    still counted), so findings can cite the real source line.
    """
    txns, skipped = [], 0
    for i, line in enumerate(raw_lines, start=1):
        s = line.strip()
        if not s:
            continue
        d, ds, de = find_date(s)
        # drop money tokens that are part of the date itself (01/03/2026)
        money = [
            (v, ms, me) for (v, ms, me) in _find_money_tokens(s)
            if not (ms < de and me > ds)
        ]
        if d is None or not money:
            skipped += 1
            continue
        amount, ms, me = money[-1]
        desc = (s[:ds] + " " + s[de:ms] + " " + s[me:])
        desc = re.sub(r"[*|\t]+", " ", desc)
        desc = re.sub(r"\s{2,}", " ", desc).strip(" -|*\t")
        if not desc:
            skipped += 1
            continue
        txns.append(Txn(date=d, description=desc, amount=amount, line_no=i, raw=s))
    return txns, skipped


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def parse_statement(text: str) -> ParseResult:
    """Parse a statement (CSV or pasted text) into a ParseResult."""
    if not text or not text.strip():
        return ParseResult(mode="empty")
    text = text.replace("\ufeff", "")          # strip UTF-8 BOM(s)
    raw_lines = text.splitlines()              # keep original line numbers
    lines = [ln for ln in raw_lines if ln.strip()]
    mode = "csv" if _looks_like_csv(lines) else "text"
    txns, skipped = (
        _parse_csv(text, raw_lines) if mode == "csv" else _parse_text_lines(raw_lines)
    )
    txns.sort(key=lambda t: (t.date, t.line_no))

    positives = sum(1 for t in txns if t.amount > 0)
    negatives = sum(1 for t in txns if t.amount < 0)
    charge_sign = 1 if positives >= negatives else -1
    return ParseResult(
        transactions=txns, skipped=skipped, mode=mode, charge_sign=charge_sign,
    )
