import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Acme Appliances Order Ledger Answer API")

TZ = ZoneInfo("Asia/Kolkata")
DATA_URL = os.getenv("DATA_URL", "").strip()
RATES_URL = os.getenv("RATES_URL", "").strip()
FALLBACK_ORDERS_URL = os.getenv("ORDERS_URL", "").strip()

orders = []
usd_per_unit = {"USD": 1.0, "EUR": 1.03, "INR": 0.01164}
load_error = None


class AnswerRequest(BaseModel):
    question: str


def parse_export(text):
    """Accept JSON arrays/objects and newline-delimited JSON exports."""
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for key in ("orders", "data", "results", "items"):
                if isinstance(obj.get(key), list):
                    return obj[key]
            if "id" in obj:
                return [obj]
    except json.JSONDecodeError:
        pass

    parsed = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict) and "id" in item:
                parsed.append(item)
            elif isinstance(item, list):
                parsed.extend(x for x in item if isinstance(x, dict) and "id" in x)
        except json.JSONDecodeError:
            continue
    return parsed


def get_json(url):
    response = requests.get(url, timeout=12)
    response.raise_for_status()
    return response.json()


def load_all_orders():
    if DATA_URL:
        r = requests.get(DATA_URL, timeout=20)
        r.raise_for_status()
        found = parse_export(r.text)
        if found:
            return found

    if FALLBACK_ORDERS_URL:
        url = FALLBACK_ORDERS_URL
        found = []
        seen_urls = set()
        while url and url not in seen_urls and len(seen_urls) < 100:
            seen_urls.add(url)
            payload = get_json(url)
            page_rows = payload.get("orders", [])
            found.extend(page_rows)
            url = payload.get("next")
        if found:
            return found

    raise RuntimeError(
        "Could not load orders. Set DATA_URL to the export URL or "
        "ORDERS_URL to the first orders-page URL."
    )


def load_rates():
    global usd_per_unit
    if not RATES_URL:
        return
    payload = get_json(RATES_URL)
    rates = payload.get("usd_per_unit", {})
    if isinstance(rates, dict) and rates:
        usd_per_unit = {str(k).upper(): float(v) for k, v in rates.items()}


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except (ValueError, TypeError):
        return None


def latest_order_rows(rows):
    latest = {}
    for row in rows:
        oid = str(row.get("id", ""))
        if not oid:
            continue
        stamp = parse_time(row.get("updated_at")) or datetime.min.replace(tzinfo=TZ)
        old = latest.get(oid)
        old_stamp = (parse_time(old.get("updated_at")) if old else None)
        if old is None or old_stamp is None or stamp > old_stamp:
            latest[oid] = row
    return list(latest.values())


def amount_usd(row):
    try:
        amount = float(row.get("amount", 0) or 0)
        currency = str(row.get("currency", "USD")).upper()
        return amount * float(usd_per_unit.get(currency, 0))
    except (TypeError, ValueError):
        return 0.0


def money(value):
    return round(float(value) + 1e-9, 2)


MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
REGIONS = ["North", "South", "East", "West", "Central"]
PRODUCTS = [
    "Air Fryer", "Rice Cooker", "Toaster", "Blender",
    "Juicer", "Grinder", "Kettle", "Mixer"
]


def question_filters(q, rows):
    qlow = q.lower()
    filtered = rows[:]

    region = next((r for r in REGIONS if re.search(r"\b" + re.escape(r.lower()) + r"\b", qlow)), None)
    product = next((p for p in PRODUCTS if p.lower() in qlow), None)
    customer_match = re.search(r"\bC\d{4}\b", q, re.I)
    customer = customer_match.group(0).upper() if customer_match else None

    if region:
        filtered = [r for r in filtered if str(r.get("region", "")).lower() == region.lower()]
    if product:
        filtered = [r for r in filtered if str(r.get("product", "")).lower() == product.lower()]
    if customer:
        filtered = [r for r in filtered if str(r.get("customer", "")).upper() == customer]

    # Recognize month/year questions. If a month is named, filter by created_at in Kolkata time.
    month = None
    for name, num in MONTHS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", qlow):
            month = num
            break
    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else None

    if month or year:
        date_rows = []
        for r in filtered:
            dt = parse_time(r.get("created_at"))
            if dt is None:
                continue
            if month and dt.month != month:
                continue
            if year and dt.year != year:
                continue
            date_rows.append(r)
        filtered = date_rows

    return filtered, {"region": region, "product": product, "customer": customer, "month": month, "year": year}


def answer_question(question):
    if not orders:
        raise RuntimeError("Order data has not loaded.")
    rows, f = question_filters(question, orders)
    q = question.lower()
    paid = [r for r in rows if str(r.get("status", "")).lower() == "paid"]
    refunded = [r for r in rows if str(r.get("status", "")).lower() == "refunded"]
    void = [r for r in rows if str(r.get("status", "")).lower() == "void"]

    # Explicit order status filters take precedence.
    if "refunded" in q or "refund" in q:
        if any(x in q for x in ("how many", "number of", "count")):
            return len(refunded)
        if "customer" in q and f["customer"]:
            return money(sum(amount_usd(r) for r in refunded))
        return money(sum(amount_usd(r) for r in refunded))

    if "void" in q or "cancelled" in q or "canceled" in q:
        if any(x in q for x in ("how many", "number of", "count")):
            return len(void)
        return money(sum(amount_usd(r) for r in void))

    # Customer-specific order count/spend.
    if f["customer"]:
        customer_rows = rows
        if any(x in q for x in ("how many", "number of", "count")) and "order" in q:
            return len(customer_rows)
        if "order" in q and any(x in q for x in ("revenue", "sales", "spent", "spend", "total")):
            return money(sum(amount_usd(r) for r in customer_rows if str(r.get("status", "")).lower() == "paid"))
        if any(x in q for x in ("spent", "spend", "revenue", "sales", "total")):
            return money(sum(amount_usd(r) for r in customer_rows if str(r.get("status", "")).lower() == "paid"))

    # Product rankings / best sellers.
    ranking_words = ("most popular", "best-selling", "bestselling", "top product", "which product", "highest-selling", "sold the most")
    if any(w in q for w in ranking_words) and not f["product"]:
        metric = "qty" if any(w in q for w in ("units", "quantity", "items", "best-selling", "bestselling", "sold the most")) else "revenue"
        totals = {}
        for r in paid:
            name = r.get("product", "Unknown")
            totals[name] = totals.get(name, 0.0) + (float(r.get("qty", 0) or 0) if metric == "qty" else amount_usd(r))
        if not totals:
            return "No matching paid orders were found."
        best = max(totals, key=totals.get)
        return {"product": best, metric: money(totals[best]) if metric == "revenue" else int(totals[best])}

    # Top/highest-spending customer.
    if any(w in q for w in ("top customer", "highest-spending customer", "spent the most", "largest customer")) and not f["customer"]:
        totals = {}
        for r in paid:
            c = r.get("customer", "Unknown")
            totals[c] = totals.get(c, 0.0) + amount_usd(r)
        if not totals:
            return "No matching paid orders were found."
        best = max(totals, key=totals.get)
        return {"customer": best, "revenue_usd": money(totals[best])}

    # Unique customer counts.
    if "customer" in q and any(w in q for w in ("how many", "number of", "count", "unique", "distinct")):
        return len({r.get("customer") for r in rows if r.get("customer")})

    # Counts.
    if any(w in q for w in ("how many", "number of", "count")):
        if "customer" in q:
            return len({r.get("customer") for r in rows if r.get("customer")})
        if "unit" in q or "item" in q or "quantity" in q:
            return int(sum(float(r.get("qty", 0) or 0) for r in paid))
        if "order" in q:
            if "paid" in q:
                return len(paid)
            return len(rows)

    # Revenue/sales questions count only paid orders.
    if any(w in q for w in ("revenue", "sales", "sold", "turnover", "generated", "earned", "income")):
        return money(sum(amount_usd(r) for r in paid))

    # Quantity or product unit questions.
    if any(w in q for w in ("units", "items", "quantity", "how many were sold")):
        return int(sum(float(r.get("qty", 0) or 0) for r in paid))

    # Average order value.
    if "average order value" in q or "average order" in q or "aov" in q:
        return money(sum(amount_usd(r) for r in paid) / len(paid)) if paid else 0

    # Generic total/amount question defaults to paid USD total, with filters applied.
    if any(w in q for w in ("total", "amount", "value")):
        return money(sum(amount_usd(r) for r in paid))

    # Helpful aggregate response for otherwise recognizable filtered questions.
    return {
        "orders": len(rows),
        "paid_orders": len(paid),
        "revenue_usd": money(sum(amount_usd(r) for r in paid)),
        "refunded_orders": len(refunded),
        "refund_amount_usd": money(sum(amount_usd(r) for r in refunded)),
        "note": "Revenue includes only current paid orders; amounts are converted to USD."
    }


class StartupError(Exception):
    pass


@app.on_event("startup")
def startup():
    global orders, load_error
    try:
        load_rates()
        orders = latest_order_rows(load_all_orders())
    except Exception as exc:
        load_error = str(exc)
        print(f"DATA LOAD ERROR: {load_error}")


@app.get("/")
def health():
    return {
        "service": "Acme Appliances order ledger answer API",
        "status": "ok" if orders else "data_not_loaded",
        "orders_loaded": len(orders),
        "data_error": load_error,
        "endpoint": "POST /answer"
    }


@app.post("/answer")
def answer(request: AnswerRequest):
    if not orders:
        raise HTTPException(status_code=503, detail=f"Order data unavailable: {load_error or 'not loaded'}")
    try:
        result = answer_question(request.question.strip())
        if isinstance(result, str):
            answer_value = result
        else:
            answer_value = result
        return {"answer": answer_value}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
