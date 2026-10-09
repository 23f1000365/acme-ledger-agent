        product_match = None
        for r in customer_rows:
            product_name = str(r.get("product", "")).strip()
            if product_name and product_name.lower() in q:
                product_match = product_name
                break

        if product_match:
            customer_rows = [
                r for r in customer_rows
                if str(r.get("product", "")).strip().lower()
                == product_match.lower()
            ]

        customers = {
            str(r.get("customer", "")).strip().casefold()
            for r in customer_rows
            if str(r.get("customer", "")).strip()
        }

        return len(customers)



    # Average order value: average USD revenue per paid order
    if any(w in q for w in (
        "average order value",
        "average order",
        "aov",
        "on average",
        "average paid order",
        "order worth",
    )):
        return round(
            sum(amount_usd(r) for r in paid) / len(paid), 2
        ) if paid else 0

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
    if any(w in q for w in (
        "revenue", "sales", "sold", "turnover",
        "generated", "earned", "earn", "income"
    )):
        return money(sum(amount_usd(r) for r in paid))


    # Quantity or product unit questions.
    if any(w in q for w in ("units", "items", "quantity", "how many were sold")):
        return int(sum(float(r.get("qty", 0) or 0) for r in paid))

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
