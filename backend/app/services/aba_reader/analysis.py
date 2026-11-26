from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from statistics import median
from .scoring import score, DEFAULT_CONFIG


def analyze(statement, config=DEFAULT_CONFIG):
    s = statement
    issues = list(s.get("extraction_issues", []))
    def issue(code, message, **extra):
        issues.append({"code": code, "message": message, **extra})
    required = ["start", "end", "opening", "ending", "reported_in", "reported_out", "currency"]
    missing = [key for key in required if s.get(key) is None]
    if missing:
        issue("missing_fields", "Unreadable required fields: " + ", ".join(missing))
        return {"status": "Needs review", "issues": issues, "metrics": None, "daily": [], "monthly": [], "score": {"value": None, "label": "Needs review", "components": [], "config": config}}
    start, end = date.fromisoformat(s["start"]), date.fromisoformat(s["end"])
    if end < start or (end-start).days > 3660:
        issue("period", "Invalid or unsupported statement period.")
        return {"status": "Needs review", "issues": issues, "metrics": None, "daily": [], "monthly": [], "score": {"value": None, "label": "Needs review", "components": [], "config": config}}
    if end > date.today():
        issue("future_period", "Statement ends in the future.")
    rows = s["transactions"]
    dated = [t["date"] for t in rows]
    if any(a > b for a,b in zip(dated, dated[1:])):
        issue("ordering", "Transaction ordering is ambiguous or not chronological.")
    opening, ending = s["opening"], s["ending"]
    i, o = sum(t["money_in"] or 0 for t in rows), sum(t["money_out"] or 0 for t in rows)
    checks = []
    def check(name, expected, actual):
        passed = expected == actual
        checks.append({"name": name, "expected": expected, "actual": actual, "difference": actual-expected, "passed": passed})
        if not passed:
            issue("reconciliation", name, expected=expected, actual=actual, difference=actual-expected)
    check("Summary opening + money in - money out equals ending", ending, opening+s["reported_in"]-s["reported_out"])
    check("Transaction credits match reported money in", s["reported_in"], i)
    check("Transaction debits match reported money out", s["reported_out"], o)
    previous, eod, by_day = opening, {}, defaultdict(list)
    complete = True
    references = {}
    for t in rows:
        if any(t.get(k) is None for k in ("money_in", "money_out", "balance")):
            complete = False
            issue("unreadable_row", "Unreadable transaction amount.", row=t["id"], page=t["page"])
            continue
        if t["money_in"] < 0 or t["money_out"] < 0 or (t["money_in"] and t["money_out"]):
            issue("amount_direction", "Unexpected credit/debit sign or both columns populated.", row=t["id"], page=t["page"])
        expected = previous+t["money_in"]-t["money_out"]
        if expected != t["balance"]:
            issue("row_balance", "Transaction does not reconcile to the preceding balance.", row=t["id"], page=t["page"], expected=expected, actual=t["balance"], difference=t["balance"]-expected)
        previous = t["balance"]
        if not s["start"] <= t["date"] <= s["end"]:
            issue("row_date", "Transaction date falls outside the statement period.", row=t["id"], page=t["page"])
        eod[t["date"]] = t["balance"]
        by_day[t["date"]].append(t["id"])
        if t.get("reference"):
            ref = t["reference"]
            if ref in references:
                issue("duplicate_reference", "Repeated transaction reference requires review.", row=t["id"], page=t["page"])
            references[ref] = t["id"]
    check("Last posted balance matches ending", ending, previous)
    daily, current, day, source = [], opening, start, None
    row_lookup = {t["id"]:t for t in rows}
    while day <= end:
        key = day.isoformat()
        current = eod.get(key, current)
        if by_day.get(key):
            source=by_day[key][-1]
        daily.append({"date": key, "balance": current, "transactions": by_day.get(key, []),
                      "source_transaction":source,"source_page":row_lookup[source]["page"] if source is not None else 1})
        day += timedelta(days=1)
    balances = [d["balance"] for d in daily]
    n = len(balances)
    avg = Decimal(sum(balances))/n
    def longest(threshold):
        best = run = 0
        for b in balances:
            run = run+1 if b < threshold else 0
            best = max(best, run)
        return best
    monthly = []
    months = sorted({d["date"][:7] for d in daily})
    for month in months:
        days = [d for d in daily if d["date"].startswith(month)]
        tx = [t for t in rows if t["date"].startswith(month)]
        mi, mo = sum(t["money_in"] or 0 for t in tx), sum(t["money_out"] or 0 for t in tx)
        first = date.fromisoformat(days[0]["date"])
        last = date.fromisoformat(days[-1]["date"])
        closing_day = days[-1]
        source_transaction = closing_day["source_transaction"]
        monthly.append({"month": month, "money_in": mi, "money_out": mo, "net": mi-mo,
                        "average": str(Decimal(sum(d["balance"] for d in days))/len(days)), "ending": days[-1]["balance"],
                        "days": len(days), "partial": first.day != 1 or (last+timedelta(days=1)).month == last.month,
                        "source_date": row_lookup[source_transaction]["date"] if source_transaction is not None else None,
                        "source_page": closing_day["source_page"],
                        "source_transaction": source_transaction,
                        "evidence_type": "DERIVED_DAILY_CLOSING"})
    final_week = [t for t in rows if (end-date.fromisoformat(t["date"])).days in range(7)]
    largest_final = max((t["money_in"] or 0 for t in final_week), default=0)
    ref = balances[-config["reference_days"]:-config["excluded_final_days"]] if n >= config["reference_days"] else []
    metrics = {"days": n, "age_days": (date.today()-end).days, "opening": opening, "ending": ending,
               "money_in": i, "money_out": o, "net": i-o, "average": str(avg), "median": str(median(balances)),
               "minimum": min(balances), "maximum": max(balances), "p20": sorted(balances)[ceil(n*.2)-1],
               "intraday_minimum": min([t["balance"] for t in rows if t["balance"] is not None], default=opening),
               "exposure": sum(balances), "exposure_unit": f'{s["currency"]}-days (minor units)',
               "below": {str(v): {"days": sum(b < v for b in balances), "percentage": sum(b < v for b in balances)/n*100} for v in [50000,100000,200000,400000]},
               "at_above_5000": sum(b >= 500000 for b in balances), "longest_below_1000": longest(100000), "longest_below_4000": longest(400000),
               "windows": {str(w): {"days": min(w,n), "average": str(Decimal(sum(balances[-w:]))/min(w,n)),
                   "credits": sum(t["money_in"] or 0 for t in rows if 0 <= (end-date.fromisoformat(t["date"])).days < w)} for w in (7,14,30)},
               "reference_median": str(median(ref)) if ref else None, "outflow_ratio": str(Decimal(o)/i) if i else None,
               "largest_credit": max((t["money_in"] or 0 for t in rows), default=0), "largest_debit": max((t["money_out"] or 0 for t in rows), default=0),
               "largest_final_week_credit": largest_final, "largest_final_week_share": largest_final/ending if ending > 0 else None,
               "transaction_count": len(rows), "complete": complete}
    result_score = {"value": None, "label": "Needs review", "components": [], "config": config}
    status = "Needs review" if issues else "Reconciled"
    if not issues:
        if s["currency"] != "USD":
            result_score["label"] = "Scoring unavailable for this currency"
        else:
            result_score = score(metrics, config)
    return {"status": status, "issues": issues, "checks": checks, "metrics": metrics, "daily": daily, "monthly": monthly, "score": result_score,
            "authenticity": "Not verified", "available_balance": "Unknown: posted balances may exclude pending withdrawals and holds."}
