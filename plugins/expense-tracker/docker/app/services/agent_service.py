from __future__ import annotations
import json
import urllib.error
import urllib.request
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.config import LLM_BACKEND, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT, OLLAMA_BASE_URL, OLLAMA_MODEL
from app.models import Expense
from app.services.budget_service import BudgetService
from app.services.expense_service import ExpenseService
from app.services.recurring_service import RecurringService


class AgentService:
    def __init__(self, db: Session):
        self.db = db
        self.exp_svc = ExpenseService(db)
        self.rec_svc = RecurringService(db)
        self.budget_svc = BudgetService(db)

    def answer(self, prompt: str) -> dict:
        question = (prompt or "").strip()
        if not question:
            return {
                "source": "local_agent",
                "model": LLM_MODEL,
                "answer": "Ask me anything about your expenses, goals, savings, recurring payments, categories, or trends.",
                "tool_results": [],
                "suggestions": self._suggested_questions(),
            }

        overview = self._tool_financial_overview({})
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "system", "content": "Current finance snapshot JSON:\n" + json.dumps(overview, ensure_ascii=False)},
            {"role": "user", "content": question},
        ]
        tool_results = []

        try:
            for _ in range(4):
                response = self._llm_chat(messages, tools=self._tool_schemas())
                message = response.get("message", {})
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    content = (message.get("content") or "").strip()
                    return {
                        "source": f"{LLM_BACKEND}_agent",
                        "model": LLM_MODEL,
                        "answer": content or "I could not produce an answer from the local model.",
                        "tool_results": tool_results,
                        "suggestions": self._suggested_questions(),
                    }

                messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls})
                for call in tool_calls:
                    name, arguments = self._parse_tool_call(call)
                    result = self._run_tool(name, arguments)
                    tool_results.append({"tool": name, "arguments": arguments, "result": result})
                    tool_message = {"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False)}
                    if call.get("id"):
                        tool_message["tool_call_id"] = call["id"]
                    messages.append(tool_message)

            final_response = self._llm_chat(messages + [{
                "role": "user",
                "content": "Use the tool results above to give the final answer. Do not call more tools.",
            }], tools=[])
            return {
                "source": f"{LLM_BACKEND}_agent",
                "model": LLM_MODEL,
                "answer": (final_response.get("message", {}).get("content") or "").strip(),
                "tool_results": tool_results,
                "suggestions": self._suggested_questions(),
            }
        except Exception as exc:
            return {
                "source": f"{LLM_BACKEND}_unavailable",
                "model": LLM_MODEL,
                "answer": (
                    "The local LLM server is not reachable yet. Start the NAS-backed llama.cpp server on the Raspberry Pi "
                    f"with the GGUF model stored under /mnt/nas, and make sure `LLM_BASE_URL={LLM_BASE_URL}` "
                    f"is reachable from this container. Backend is `{LLM_BACKEND}`, model is `{LLM_MODEL}`. "
                    f"Technical detail: {exc}"
                ),
                "tool_results": [{"tool": "financial_overview", "result": overview}],
                "suggestions": self._suggested_questions(),
            }

    def _system_prompt(self) -> str:
        return (
            "You are Expense Tracker's local agentic finance analyst running fully locally. "
            "Answer any user question about their expense database by deciding which tools to call. "
            "Use tools for facts, calculations, rankings, trends, and goal timing. "
            "Never invent transaction data. If data is missing, say what is missing and what to set up. "
            "Keep answers concise, practical, and specific. Currency is INR. "
            "For investments, provide general educational guidance only and connect it to the user's budget, savings, and spending data."
        )

    def _llm_chat(self, messages: list[dict], tools: list[dict]) -> dict:
        if LLM_BACKEND == "ollama":
            return self._ollama_native_chat(messages, tools)
        return self._openai_compatible_chat(messages, tools)

    def _openai_compatible_chat(self, messages: list[dict], tools: list[dict]) -> dict:
        cleaned_messages = []
        for message in messages:
            item = dict(message)
            item.pop("tool_name", None)
            cleaned_messages.append(item)
        payload: dict[str, Any] = {
            "model": LLM_MODEL,
            "messages": cleaned_messages,
            "temperature": 0.2,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        req = urllib.request.Request(
            f"{LLM_BASE_URL.rstrip('/')}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"llama.cpp HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"llama.cpp connection failed: {exc.reason}") from exc

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {"message": message}

    def _ollama_native_chat(self, messages: list[dict], tools: list[dict]) -> dict:
        payload: dict[str, Any] = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 4096},
        }
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama connection failed: {exc.reason}") from exc

    def _tool_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "financial_overview",
                    "description": "Get this month dashboard, budget status, recurring impact, category breakdown, and recent trends.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "category_comparison",
                    "description": "Compare category spending between two months. Use for last month vs this month questions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "base_month": {"type": "string", "description": "YYYY-MM month to compare from. Defaults to last month."},
                            "compare_month": {"type": "string", "description": "YYYY-MM month to compare to. Defaults to this month."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "top_expenses",
                    "description": "Return largest debit transactions over the last N months.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "months": {"type": "integer", "description": "Number of recent months to scan, 1 to 24."},
                            "limit": {"type": "integer", "description": "Maximum rows, 1 to 25."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "goal_status",
                    "description": "Calculate savings, goal amount, months/days left, and current budget position.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "expenses_by_category",
                    "description": "Return ranked category totals for a month or recent month window.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "month": {"type": "string", "description": "Optional YYYY-MM month."},
                            "months": {"type": "integer", "description": "Optional recent-month window, 1 to 24."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_transactions",
                    "description": "Find matching transactions by category, description text, date window, or debit/credit type.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Text to search in category or description."},
                            "months": {"type": "integer", "description": "Recent-month window, 1 to 24."},
                            "type": {"type": "string", "enum": ["expense", "credit", "all"]},
                            "limit": {"type": "integer", "description": "Maximum rows, 1 to 50."},
                        },
                    },
                },
            },
        ]

    def _parse_tool_call(self, call: dict) -> tuple[str, dict]:
        fn = call.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return name, args if isinstance(args, dict) else {}

    def _run_tool(self, name: str, arguments: dict) -> dict | list:
        tools = {
            "financial_overview": self._tool_financial_overview,
            "category_comparison": self._tool_category_comparison,
            "top_expenses": self._tool_top_expenses,
            "goal_status": self._tool_goal_status,
            "expenses_by_category": self._tool_expenses_by_category,
            "search_transactions": self._tool_search_transactions,
        }
        if name not in tools:
            return {"error": f"Unknown tool: {name}"}
        return tools[name](arguments or {})

    def _tool_financial_overview(self, arguments: dict) -> dict:
        month = date.today().strftime("%Y-%m")
        breakdown = self.exp_svc.category_breakdown(month, include_recurring=True)
        trends = self.exp_svc.monthly_totals(months=6, include_recurring=True)
        status = self._budget_status(month)
        return {
            "month": month,
            "budget_status": status,
            "category_breakdown": breakdown[:10],
            "trends": trends,
            "recurring_total": self.rec_svc.projected_total_for_month(month),
            "recent_expenses": self._format_expenses(
                [row for row in self.exp_svc.list(month=month) if row.amount < 0][:8]
            ),
        }

    def _tool_category_comparison(self, arguments: dict) -> dict:
        compare_month = arguments.get("compare_month") or date.today().strftime("%Y-%m")
        base_month = arguments.get("base_month") or self._shift_month(compare_month, -1)
        compare = self.exp_svc.category_breakdown(compare_month, include_recurring=True)
        base = self.exp_svc.category_breakdown(base_month, include_recurring=True)
        compare_map = {row["category"]: row["total"] for row in compare}
        base_map = {row["category"]: row["total"] for row in base}
        categories = sorted(set(compare_map) | set(base_map))
        rows = [{
            "category": category,
            "base_month": base_month,
            "base_total": round(base_map.get(category, 0), 2),
            "compare_month": compare_month,
            "compare_total": round(compare_map.get(category, 0), 2),
            "change": round(compare_map.get(category, 0) - base_map.get(category, 0), 2),
        } for category in categories]
        rows.sort(key=lambda item: abs(item["change"]), reverse=True)
        return {"base_month": base_month, "compare_month": compare_month, "rows": rows}

    def _tool_top_expenses(self, arguments: dict) -> list[dict]:
        months = self._bounded_int(arguments.get("months"), 3, 1, 24)
        limit = self._bounded_int(arguments.get("limit"), 10, 1, 25)
        start, end = self._range_for_last_months(months)
        rows = (
            self.db.query(Expense)
            .filter(Expense.amount < 0, Expense.date >= start, Expense.date <= end)
            .order_by(Expense.amount.asc())
            .limit(limit)
            .all()
        )
        return self._format_expenses(rows)

    def _tool_goal_status(self, arguments: dict) -> dict:
        month = date.today().strftime("%Y-%m")
        status = self._budget_status(month)
        goal = status.get("investment_goal", 0) or 0
        savings = status.get("savings", 0) or 0
        result = {"month": month, "budget_status": status}
        if goal > 0 and savings > 0:
            months_left = goal / savings
            days_left = round(months_left * 30.4375)
            result.update({
                "goal": goal,
                "monthly_savings": savings,
                "months_left": round(months_left, 1),
                "days_left": days_left,
                "years_left": round(months_left / 12, 2),
                "human_time_left": self._human_duration(days_left),
            })
        elif goal <= 0:
            result["message"] = "No investment goal is set."
        else:
            result["message"] = "Monthly savings is not positive, so the goal is currently unreachable."
        return result

    def _tool_expenses_by_category(self, arguments: dict) -> list[dict]:
        month = arguments.get("month")
        months = arguments.get("months")
        if month:
            return self.exp_svc.category_breakdown(month, include_recurring=True)
        months = self._bounded_int(months, 3, 1, 24)
        start, end = self._range_for_last_months(months)
        totals: dict[str, float] = {}
        for row in self.db.query(Expense).filter(Expense.amount < 0, Expense.date >= start, Expense.date <= end).all():
            totals[row.category] = totals.get(row.category, 0) + abs(row.amount)
        rows = [{"category": category, "total": round(total, 2)} for category, total in totals.items()]
        return sorted(rows, key=lambda item: item["total"], reverse=True)

    def _tool_search_transactions(self, arguments: dict) -> list[dict]:
        query = (arguments.get("query") or "").strip().lower()
        tx_type = arguments.get("type") or "all"
        months = self._bounded_int(arguments.get("months"), 6, 1, 24)
        limit = self._bounded_int(arguments.get("limit"), 20, 1, 50)
        start, end = self._range_for_last_months(months)
        rows = self.db.query(Expense).filter(Expense.date >= start, Expense.date <= end)
        if tx_type == "expense":
            rows = rows.filter(Expense.amount < 0)
        elif tx_type == "credit":
            rows = rows.filter(Expense.amount >= 0)
        matched = []
        for row in rows.order_by(Expense.date.desc()).all():
            haystack = f"{row.category} {row.description or ''}".lower()
            if query and query not in haystack:
                continue
            matched.append(row)
            if len(matched) >= limit:
                break
        return self._format_expenses(matched)

    def _budget_status(self, month: str) -> dict:
        breakdown = self.exp_svc.category_breakdown(month, include_recurring=True)
        total_exp = sum(row["total"] for row in breakdown)
        return self.budget_svc.compute_status(self.budget_svc.get(month), total_exp)

    def _format_expenses(self, rows: list[Expense]) -> list[dict]:
        return [{
            "date": str(row.date),
            "amount": round(abs(row.amount), 2),
            "signed_amount": round(row.amount, 2),
            "category": row.category,
            "description": row.description or "",
            "cardholder": row.cardholder or "",
            "type": "credit" if row.amount >= 0 else "expense",
        } for row in rows]

    def _range_for_last_months(self, months: int) -> tuple[date, date]:
        today = date.today()
        start_label = self._shift_month(today.strftime("%Y-%m"), -(months - 1))
        y, m = [int(part) for part in start_label.split("-")]
        return date(y, m, 1), today

    def _shift_month(self, month: str, delta: int) -> str:
        y, m = [int(part) for part in month.split("-")]
        absolute = y * 12 + (m - 1) + delta
        return f"{absolute // 12}-{(absolute % 12) + 1:02d}"

    def _human_duration(self, days: int) -> str:
        years = days // 365
        rem = days % 365
        months = rem // 30
        rem_days = rem % 30
        return f"{years} years, {months} months, {rem_days} days"

    def _bounded_int(self, value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _suggested_questions(self) -> list[str]:
        return [
            "Which investment plan should I go for based on my savings?",
            "Which category increased the most compared to last month?",
            "What expenses should I reduce to achieve my goal faster?",
            "Show me my top 10 expenses in the last 3 months.",
        ]
