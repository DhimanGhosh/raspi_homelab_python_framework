from __future__ import annotations
import argparse
import json
import random
import sqlite3
from pathlib import Path


DEFAULT_CATEGORIES = [
    "Grocery", "Restaurant", "Office Travel", "Online Shopping", "Subscription",
    "Medicine", "Utilities", "Flat/Rent", "Movies", "Mobile Recharge",
    "ATM Cash", "Outside Food", "Games", "Bank Savings", "CC Bill",
]

QUESTION_TEMPLATES = [
    ("Which investment plan should I go for?", "financial_overview,goal_status"),
    ("Can I invest more this month?", "financial_overview,goal_status"),
    ("Should I choose RD LIC Post Office or mutual fund?", "financial_overview,goal_status,expenses_by_category"),
    ("Which expense category did I spend most on this month?", "expenses_by_category"),
    ("Which category increased most compared to last month?", "category_comparison"),
    ("Which category reduced compared to last month?", "category_comparison"),
    ("What expenses should I reduce to achieve my goal?", "goal_status,expenses_by_category"),
    ("How many years months and days are left to achieve my goal?", "goal_status"),
    ("Show me top expenses in the last {months} months.", "top_expenses"),
    ("What are my largest transactions recently?", "top_expenses"),
    ("Did my recurring expenses affect my budget?", "financial_overview"),
    ("Find my {category} spends recently.", "search_transactions"),
    ("How much did I spend on {category}?", "expenses_by_category,search_transactions"),
    ("Should I reduce {category} spending?", "goal_status,expenses_by_category,search_transactions"),
    ("What changed in my spending pattern?", "financial_overview,category_comparison"),
    ("Why is my budget usage high?", "financial_overview,expenses_by_category"),
    ("How can I save faster for my investment goal?", "goal_status,expenses_by_category"),
    ("What are avoidable spends this month?", "financial_overview,expenses_by_category,search_transactions"),
    ("Compare {category} spending across recent months.", "search_transactions,expenses_by_category"),
    ("Which cardholder spent most recently?", "search_transactions,financial_overview"),
]


def load_categories(sqlite_path: str | None) -> list[str]:
    if not sqlite_path or not Path(sqlite_path).exists():
        return []
    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute("select distinct category from expenses where category is not null").fetchall()
        return sorted({row[0] for row in rows if row[0]})
    finally:
        conn.close()


def build_examples(categories: list[str]) -> list[dict]:
    categories = sorted(set(categories or DEFAULT_CATEGORIES))
    examples = []
    for question, tools in QUESTION_TEMPLATES:
        category = random.choice(categories)
        months = random.choice([1, 2, 3, 6, 12])
        rendered = question.format(category=category, months=months)
        examples.append({
            "instruction": rendered,
            "input": "",
            "output": _tool_policy_output(tools, category=category, months=months),
        })
    return examples


def build_synthetic_examples(categories: list[str], count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    categories = sorted(set(categories or DEFAULT_CATEGORIES))
    examples = []
    prefixes = ["Please", "Can you", "Tell me", "Analyze", "I want to know", "Help me understand"]
    for _ in range(count):
        question, tools = rng.choice(QUESTION_TEMPLATES)
        category = rng.choice(categories)
        months = rng.choice([1, 2, 3, 4, 6, 9, 12])
        prefix = rng.choice(prefixes)
        rendered = question.format(category=category, months=months)
        if rng.random() < 0.55:
            rendered = f"{prefix} {rendered[0].lower()}{rendered[1:]}"
        examples.append({
            "instruction": rendered,
            "input": "",
            "output": _tool_policy_output(tools, category=category, months=months),
        })
    return examples


def _tool_policy_output(tools: str, category: str, months: int) -> str:
    tool_names = [tool.strip() for tool in tools.split(",")]
    steps = []
    for tool in tool_names:
        if tool == "top_expenses":
            steps.append(f"Call top_expenses with months={months}.")
        elif tool == "search_transactions":
            steps.append(f"Call search_transactions with query='{category}', type='expense', and months={months}.")
        elif tool == "expenses_by_category":
            steps.append("Call expenses_by_category for the relevant month or recent window.")
        elif tool == "category_comparison":
            steps.append("Call category_comparison for last month versus this month.")
        elif tool == "goal_status":
            steps.append("Call goal_status to get investment goal, savings, and time left.")
        elif tool == "financial_overview":
            steps.append("Call financial_overview for budget, recurring, trends, and recent expenses.")
    steps.append("Then answer using only returned tool data, in INR, with practical next steps.")
    return " ".join(steps)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", help="Optional Expense Tracker SQLite database path")
    parser.add_argument("--out", default="expense_qna_train.jsonl")
    parser.add_argument("--synthetic-count", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    categories = load_categories(args.sqlite)
    examples = build_examples(categories) + build_synthetic_examples(categories, args.synthetic_count, args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        for item in examples:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"Wrote {len(examples)} examples to {args.out}")


if __name__ == "__main__":
    main()
