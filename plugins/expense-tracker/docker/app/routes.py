from __future__ import annotations
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import APP_NAME, APP_VERSION, CATEGORIES, CARDHOLDERS
from app.core import templates
from app.database import get_db
from app.services.expense_service import ExpenseService
from app.services.budget_service import BudgetService
from app.services.recurring_service import RecurringService
from app.services.balance_service import BalanceService
from app.services.agent_service import AgentService

router = APIRouter()


# ── HTML ──────────────────────────────────────────────────────────────────────

@router.get("/", include_in_schema=False)
def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "categories": CATEGORIES,
        "cardholders": CARDHOLDERS,
    })


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/api/health")
def health():
    return {"ok": True, "service": APP_NAME, "version": APP_VERSION}


# ── Dashboard  (Python computes everything) ───────────────────────────────────

@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    today      = date.today()
    month      = today.strftime("%Y-%m")
    exp_svc    = ExpenseService(db)
    bud_svc    = BudgetService(db)
    rec_svc    = RecurringService(db)
    bal_svc    = BalanceService(db)

    expenses   = exp_svc.list(month=month)
    recurring  = rec_svc.projected_for_month(month)
    total_exp  = sum(abs(e.amount) for e in expenses if e.amount < 0) + sum(abs(r["amount"]) for r in recurring)
    budget     = bud_svc.get(month)
    status     = bud_svc.compute_status(budget, total_exp)
    breakdown  = exp_svc.category_breakdown(month, include_recurring=True)
    recent     = [_fmt_expense(e) for e in expenses if e.amount < 0][:5]
    trends     = exp_svc.monthly_totals(months=6, include_recurring=True)
    insights   = exp_svc.smart_insights(month, status, breakdown, trends)

    return {
        "month":         month,
        "status":        status,
        "balance":       bal_svc.get_balance(),
        "recurring":     {"projected": [_fmt_projection(r) for r in recurring], "total": round(sum(abs(r["amount"]) for r in recurring), 2)},
        "breakdown":     breakdown,
        "recent":        recent,
        "trends":        trends,
        "insights":       insights,
    }


# ── Expenses ──────────────────────────────────────────────────────────────────

@router.get("/api/expenses")
def list_expenses(
    month:      Optional[str] = None,
    category:   Optional[str] = None,
    cardholder: Optional[str] = None,
    db: Session = Depends(get_db),
):
    svc  = ExpenseService(db)
    rows = svc.list(month=month, category=category, cardholder=cardholder)
    return [_fmt_expense(e) for e in rows]


@router.post("/api/expenses", status_code=201)
def create_expense(payload: dict, db: Session = Depends(get_db)):
    data = _parse_expense_payload(payload)
    exp  = ExpenseService(db).create(data)
    return _fmt_expense(exp)


@router.put("/api/expenses/{eid}")
def update_expense(eid: int, payload: dict, db: Session = Depends(get_db)):
    data = _parse_expense_payload(payload)
    exp  = ExpenseService(db).update(eid, data)
    if not exp:
        raise HTTPException(404, "Expense not found")
    return _fmt_expense(exp)


@router.delete("/api/expenses/{eid}", status_code=204)
def delete_expense(eid: int, db: Session = Depends(get_db)):
    if not ExpenseService(db).delete(eid):
        raise HTTPException(404, "Expense not found")
    return Response(status_code=204)


# ── Category helpers ──────────────────────────────────────────────────────────

@router.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    return [{"name": c} for c in ExpenseService(db).all_categories()]


@router.post("/api/predict-category")
def predict_category(payload: dict, db: Session = Depends(get_db)):
    desc = payload.get("description", "")
    return ExpenseService(db).predict_category_details(desc)


# ── Balance ──────────────────────────────────────────────────────────────────

@router.get("/api/balance")
def get_balance(db: Session = Depends(get_db)):
    return {"balance": BalanceService(db).get_balance()}


@router.post("/api/balance")
def save_balance(payload: dict, db: Session = Depends(get_db)):
    amount = float(payload.get("balance", 0))
    return {"balance": BalanceService(db).set_balance(amount)}


# ── Budget ────────────────────────────────────────────────────────────────────

@router.get("/api/budget")
def get_budget(month: Optional[str] = None, db: Session = Depends(get_db)):
    if not month:
        month = date.today().strftime("%Y-%m")
    bud_svc   = BudgetService(db)
    exp_svc   = ExpenseService(db)
    rec_svc   = RecurringService(db)
    budget    = bud_svc.get(month)
    expenses  = exp_svc.list(month=month)
    total_exp = sum(abs(e.amount) for e in expenses if e.amount < 0) + rec_svc.projected_total_for_month(month)
    return bud_svc.compute_status(budget, total_exp)


@router.post("/api/budget")
def save_budget(payload: dict, db: Session = Depends(get_db)):
    month = payload.pop("month", date.today().strftime("%Y-%m"))
    # Map UI field name to DB column name
    if "investment_goal" in payload:
        payload["product_cost"] = payload.pop("investment_goal")
    payload.pop("product_goal", None)   # retired field
    budget = BudgetService(db).upsert(month, payload)
    return {"ok": True, "id": budget.id}


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/api/analytics")
def analytics(months: int = 6, db: Session = Depends(get_db)):
    exp_svc   = ExpenseService(db)
    month     = date.today().strftime("%Y-%m")
    trends    = exp_svc.monthly_totals(months=months, include_recurring=True)
    breakdown = exp_svc.category_breakdown(month, include_recurring=True)
    budget    = BudgetService(db).get(month)
    total_exp = trends[-1]["expenses"] if trends else 0
    status    = BudgetService(db).compute_status(budget, total_exp)
    return {
        "trends":    trends,
        "breakdown": breakdown,
        "insights":  exp_svc.smart_insights(month, status, breakdown, trends),
    }


# ── Ask / prompt tracking ─────────────────────────────────────────────────────

@router.post("/api/ask")
def ask_expenses(payload: dict, db: Session = Depends(get_db)):
    return AgentService(db).answer(payload.get("prompt", ""))


# ── Recurring ─────────────────────────────────────────────────────────────────

@router.get("/api/recurring")
def list_recurring(db: Session = Depends(get_db)):
    svc = RecurringService(db)
    return [_fmt_recurring(t) for t in svc.list()]


@router.post("/api/recurring", status_code=201)
def create_recurring(payload: dict, db: Session = Depends(get_db)):
    data = {
        "description": payload["description"],
        "amount":      float(payload["amount"]),
        "category":    payload["category"],
        "cardholder":  payload.get("cardholder"),
        "frequency":   payload["frequency"],
        "next_due":    date.fromisoformat(payload["next_due"]),
        "is_active":   True,
    }
    tmpl = RecurringService(db).create(data)
    return _fmt_recurring(tmpl)


@router.put("/api/recurring/{tid}")
def update_recurring(tid: int, payload: dict, db: Session = Depends(get_db)):
    data = {
        "description": payload["description"],
        "amount":      float(payload["amount"]),
        "category":    payload["category"],
        "cardholder":  payload.get("cardholder"),
        "frequency":   payload["frequency"],
        "next_due":    date.fromisoformat(payload["next_due"]),
    }
    tmpl = RecurringService(db).update(tid, data)
    if not tmpl:
        raise HTTPException(404, "Template not found")
    return _fmt_recurring(tmpl)


@router.delete("/api/recurring/{tid}", status_code=204)
def delete_recurring(tid: int, db: Session = Depends(get_db)):
    if not RecurringService(db).delete(tid):
        raise HTTPException(404, "Template not found")
    return Response(status_code=204)


@router.get("/api/recurring/{tid}/preview")
def preview_recurring(tid: int, months: int = 3, db: Session = Depends(get_db)):
    return RecurringService(db).preview(tid, months)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fmt_expense(e) -> dict:
    return {
        "id":          e.id,
        "date":        str(e.date),
        "amount":      round(e.amount, 2),
        "category":    e.category,
        "description": e.description or "",
        "cardholder":  e.cardholder or "",
    }


def _fmt_recurring(t) -> dict:
    from app.services.recurring_service import _next_due
    # Advance past-due dates forward so the UI always shows the next future occurrence
    today = date.today()
    display_next = t.next_due
    while display_next <= today:
        display_next = _next_due(display_next, t.frequency)
    return {
        "id":          t.id,
        "description": t.description,
        "amount":      round(t.amount, 2),
        "category":    t.category,
        "cardholder":  t.cardholder or "",
        "frequency":   t.frequency,
        "next_due":    str(display_next),
        "is_active":   t.is_active,
    }


def _fmt_projection(item: dict) -> dict:
    return {
        "date":        str(item["date"]),
        "amount":      round(item["amount"], 2),
        "category":    item["category"],
        "description": item["description"],
        "cardholder":  item.get("cardholder", ""),
        "template_id": item.get("template_id"),
    }


def _parse_expense_payload(p: dict) -> dict:
    raw_amount = float(p.get("amount", 0))
    # Expenses are stored negative, income positive
    is_expense = p.get("type", "expense") != "income"
    amount = -abs(raw_amount) if is_expense else abs(raw_amount)
    return {
        "date":        date.fromisoformat(p["date"]),
        "amount":      amount,
        "category":    (p.get("category") or "Other").strip() or "Other",
        "description": p.get("description", ""),
        "cardholder":  p.get("cardholder", ""),
    }
