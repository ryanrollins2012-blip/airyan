#!/usr/bin/env python3
"""
Personal Finance Manager
Track income, expenses, budgets, and get AI-powered insights.

Setup:
  pip install anthropic python-dotenv
  Copy .env.example to .env and set ANTHROPIC_API_KEY

Usage:
  python finance.py add                          # interactive add
  python finance.py add -a 45.50 -c Food -t expense -d "Lunch"
  python finance.py list                         # recent transactions
  python finance.py list --month 2026-03         # filter by month
  python finance.py summary                      # current month overview
  python finance.py budget set <category> <amount>
  python finance.py budget view
  python finance.py insight                      # AI analysis of your spending
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime, date
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = Path(os.environ.get("FINANCE_DB", Path.home() / ".finance.db"))

CATEGORIES = [
    "Housing", "Food", "Transport", "Healthcare", "Entertainment",
    "Shopping", "Education", "Savings", "Income", "Other",
]

TRANSACTION_TYPES = ["expense", "income"]


# ─── Database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                amount      REAL    NOT NULL CHECK(amount > 0),
                type        TEXT    NOT NULL CHECK(type IN ('income','expense')),
                category    TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                txn_date    TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS budgets (
                category    TEXT PRIMARY KEY,
                monthly_limit REAL NOT NULL CHECK(monthly_limit > 0),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def current_month() -> str:
    return date.today().strftime("%Y-%m")


def fmt_amount(amount: float, txn_type: str) -> str:
    sign = "+" if txn_type == "income" else "-"
    return f"{sign}${amount:,.2f}"


def fmt_row(row: sqlite3.Row) -> str:
    d = dict(row)
    amt = fmt_amount(d["amount"], d["type"])
    return (
        f"  [{d['id']:>4}] {d['txn_date']}  {amt:>12}  "
        f"{d['category']:<14} {d['description']}"
    )


def divider(char: str = "─", width: int = 70) -> str:
    return char * width


def header(title: str, width: int = 70) -> str:
    return f"\n{'═' * width}\n  {title}\n{'═' * width}"


def pick_from_list(prompt: str, options: list[str]) -> str:
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i:2}] {opt}")
    while True:
        raw = input(f"Choose [1–{len(options)}]: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print("  Invalid choice, try again.")


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_add(args: argparse.Namespace) -> None:
    """Add a new transaction."""
    # Interactive mode when key fields are missing
    if args.amount is None:
        raw = input("Amount: $").strip()
        try:
            args.amount = float(raw)
        except ValueError:
            print("Invalid amount.")
            sys.exit(1)

    if args.amount <= 0:
        print("Amount must be positive.")
        sys.exit(1)

    if args.type is None:
        args.type = pick_from_list("Transaction type:", TRANSACTION_TYPES)

    if args.category is None:
        args.category = pick_from_list("Category:", CATEGORIES)
    elif args.category not in CATEGORIES:
        print(f"Unknown category '{args.category}'. Valid: {', '.join(CATEGORIES)}")
        sys.exit(1)

    if args.description is None:
        args.description = input("Description (optional): ").strip()

    if args.date is None:
        args.date = date.today().isoformat()
    else:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print("Date must be YYYY-MM-DD.")
            sys.exit(1)

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO transactions (amount, type, category, description, txn_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (args.amount, args.type, args.category, args.description, args.date),
        )
        txn_id = cur.lastrowid

    sign = "+" if args.type == "income" else "-"
    print(
        f"\n  Added #{txn_id}: {sign}${args.amount:,.2f}  "
        f"{args.category}  {args.date}"
        + (f"  \"{args.description}\"" if args.description else "")
    )

    # Budget warning for expenses
    if args.type == "expense":
        month = args.date[:7]
        with get_db() as conn:
            budget = conn.execute(
                "SELECT monthly_limit FROM budgets WHERE category = ?",
                (args.category,),
            ).fetchone()
            if budget:
                spent = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
                    "WHERE type='expense' AND category=? AND txn_date LIKE ?",
                    (args.category, f"{month}%"),
                ).fetchone()["total"]
                limit = budget["monthly_limit"]
                pct = spent / limit * 100
                if pct >= 100:
                    print(f"\n  ⚠  Over budget! {args.category}: ${spent:,.2f} / ${limit:,.2f} ({pct:.0f}%)")
                elif pct >= 80:
                    print(f"\n  ⚠  Approaching budget: {args.category}: ${spent:,.2f} / ${limit:,.2f} ({pct:.0f}%)")


def cmd_list(args: argparse.Namespace) -> None:
    """List transactions with optional filters."""
    month = args.month or current_month()
    query = (
        "SELECT * FROM transactions WHERE txn_date LIKE ? "
        "ORDER BY txn_date DESC, id DESC LIMIT ?"
    )
    with get_db() as conn:
        rows = conn.execute(query, (f"{month}%", args.limit)).fetchall()

    if not rows:
        print(f"\n  No transactions found for {month}.")
        return

    print(header(f"Transactions — {month}"))
    income_total = 0.0
    expense_total = 0.0
    for row in rows:
        print(fmt_row(row))
        if row["type"] == "income":
            income_total += row["amount"]
        else:
            expense_total += row["amount"]

    print(divider())
    net = income_total - expense_total
    net_str = f"+${net:,.2f}" if net >= 0 else f"-${abs(net):,.2f}"
    print(f"  Income: +${income_total:,.2f}   Expenses: -${expense_total:,.2f}   Net: {net_str}\n")


def cmd_summary(args: argparse.Namespace) -> None:
    """Show a monthly summary broken down by category."""
    month = args.month or current_month()

    with get_db() as conn:
        expenses = conn.execute(
            "SELECT category, SUM(amount) AS total FROM transactions "
            "WHERE type='expense' AND txn_date LIKE ? GROUP BY category ORDER BY total DESC",
            (f"{month}%",),
        ).fetchall()

        income = conn.execute(
            "SELECT SUM(amount) AS total FROM transactions "
            "WHERE type='income' AND txn_date LIKE ?",
            (f"{month}%",),
        ).fetchone()["total"] or 0.0

        budgets = {
            row["category"]: row["monthly_limit"]
            for row in conn.execute("SELECT category, monthly_limit FROM budgets").fetchall()
        }

    total_expenses = sum(r["total"] for r in expenses)
    net = income - total_expenses

    print(header(f"Monthly Summary — {month}"))
    print(f"  Total Income:   +${income:>10,.2f}")
    print(f"  Total Expenses: -${total_expenses:>10,.2f}")
    net_fmt = f"+${net:,.2f}" if net >= 0 else f"-${abs(net):,.2f}"
    print(f"  Net:             {net_fmt:>11}")
    print(divider())

    if not expenses:
        print("  No expenses recorded.\n")
        return

    print(f"\n  {'Category':<14}  {'Spent':>10}  {'Budget':>10}  {'Used':>6}  Bar")
    print(f"  {divider('─', 60)}")
    for row in expenses:
        cat = row["category"]
        spent = row["total"]
        limit = budgets.get(cat)
        if limit:
            pct = min(spent / limit * 100, 100)
            bar_fill = int(pct / 5)
            bar = f"[{'█' * bar_fill}{'░' * (20 - bar_fill)}]"
            warn = " ⚠" if spent > limit else ""
            print(
                f"  {cat:<14}  ${spent:>9,.2f}  ${limit:>9,.2f}  {pct:>5.1f}%  {bar}{warn}"
            )
        else:
            print(f"  {cat:<14}  ${spent:>9,.2f}  {'—':>10}  {'—':>6}")
    print()


def cmd_budget(args: argparse.Namespace) -> None:
    """Set or view category budgets."""
    if args.budget_cmd == "set":
        category = args.category
        if category not in CATEGORIES:
            print(f"Unknown category '{category}'. Valid: {', '.join(CATEGORIES)}")
            sys.exit(1)
        try:
            limit = float(args.amount)
            if limit <= 0:
                raise ValueError
        except ValueError:
            print("Budget amount must be a positive number.")
            sys.exit(1)

        with get_db() as conn:
            conn.execute(
                "INSERT INTO budgets (category, monthly_limit, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit, updated_at=datetime('now')",
                (category, limit),
            )
        print(f"\n  Budget set: {category} — ${limit:,.2f}/month\n")

    elif args.budget_cmd == "view":
        month = current_month()
        with get_db() as conn:
            budgets = conn.execute(
                "SELECT b.category, b.monthly_limit, "
                "COALESCE(SUM(t.amount), 0) AS spent "
                "FROM budgets b "
                "LEFT JOIN transactions t ON t.category = b.category "
                "  AND t.type = 'expense' AND t.txn_date LIKE ? "
                "GROUP BY b.category ORDER BY b.category",
                (f"{month}%",),
            ).fetchall()

        if not budgets:
            print("\n  No budgets set. Use: python finance.py budget set <Category> <amount>\n")
            return

        print(header(f"Budgets — {month}"))
        print(f"  {'Category':<14}  {'Spent':>10}  {'Limit':>10}  {'Remaining':>10}  {'Used':>6}")
        print(f"  {divider('─', 62)}")
        for row in budgets:
            remaining = row["monthly_limit"] - row["spent"]
            pct = row["spent"] / row["monthly_limit"] * 100
            warn = " ⚠" if row["spent"] > row["monthly_limit"] else ""
            rem_fmt = f"-${abs(remaining):,.2f}" if remaining < 0 else f"${remaining:,.2f}"
            print(
                f"  {row['category']:<14}  ${row['spent']:>9,.2f}  "
                f"${row['monthly_limit']:>9,.2f}  {rem_fmt:>10}  {pct:>5.1f}%{warn}"
            )
        print()

    else:
        print("Usage: python finance.py budget [set <Category> <amount> | view]")


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete a transaction by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (args.id,)
        ).fetchone()
        if not row:
            print(f"\n  No transaction with id {args.id}.\n")
            sys.exit(1)
        print(f"\n  {fmt_row(row)}")
        confirm = input("  Delete this transaction? [y/N] ").strip().lower()
        if confirm in ("y", "yes"):
            conn.execute("DELETE FROM transactions WHERE id = ?", (args.id,))
            print(f"  Deleted #{args.id}.\n")
        else:
            print("  Cancelled.\n")


def cmd_insight(args: argparse.Namespace) -> None:
    """Use Claude to analyze spending and provide financial insights."""
    try:
        import anthropic
    except ImportError:
        print("Missing dependency: pip install anthropic")
        sys.exit(1)

    month = args.month or current_month()

    with get_db() as conn:
        transactions = conn.execute(
            "SELECT type, category, amount, description, txn_date FROM transactions "
            "WHERE txn_date LIKE ? ORDER BY txn_date",
            (f"{month}%",),
        ).fetchall()

        budgets = conn.execute(
            "SELECT category, monthly_limit FROM budgets"
        ).fetchall()

        # Last 3 months net
        history = conn.execute(
            "SELECT strftime('%Y-%m', txn_date) AS month, type, SUM(amount) AS total "
            "FROM transactions WHERE txn_date >= date('now', '-3 months') "
            "GROUP BY month, type ORDER BY month",
        ).fetchall()

    if not transactions:
        print(f"\n  No transactions for {month} to analyze.\n")
        return

    # Build context for Claude
    lines = [f"Personal Finance Data — {month}\n"]

    income = sum(r["amount"] for r in transactions if r["type"] == "income")
    expenses = sum(r["amount"] for r in transactions if r["type"] == "expense")
    lines.append(f"Income: ${income:,.2f}")
    lines.append(f"Expenses: ${expenses:,.2f}")
    lines.append(f"Net: ${income - expenses:,.2f}\n")

    lines.append("Spending by category:")
    from collections import defaultdict
    by_cat: dict[str, float] = defaultdict(float)
    for r in transactions:
        if r["type"] == "expense":
            by_cat[r["category"]] += r["amount"]
    for cat, total in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"  {cat}: ${total:,.2f}")

    if budgets:
        lines.append("\nMonthly budgets:")
        for b in budgets:
            spent = by_cat.get(b["category"], 0.0)
            lines.append(f"  {b['category']}: spent ${spent:,.2f} / limit ${b['monthly_limit']:,.2f}")

    if history:
        lines.append("\nLast 3 months history:")
        for h in history:
            lines.append(f"  {h['month']} {h['type']}: ${h['total']:,.2f}")

    context = "\n".join(lines)

    prompt = f"""You are a personal finance advisor. Analyze the following financial data and provide:
1. A brief overall assessment (2-3 sentences)
2. Top 2-3 actionable recommendations to improve financial health
3. One specific savings opportunity based on the spending pattern

Keep your response concise, practical, and encouraging. Use plain text, no markdown headers.

{context}"""

    client = anthropic.Anthropic()
    print(header(f"AI Financial Insight — {month}"))
    print()

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for chunk in stream.text_stream:
            print(chunk, end="", flush=True)

    print("\n")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finance",
        description="Personal Finance Manager — track income, expenses, and budgets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    # add
    p_add = sub.add_parser("add", help="Add a transaction")
    p_add.add_argument("-a", "--amount", type=float, metavar="AMOUNT", help="Transaction amount")
    p_add.add_argument("-t", "--type", choices=TRANSACTION_TYPES, metavar="TYPE", help="income or expense")
    p_add.add_argument("-c", "--category", metavar="CATEGORY", help=f"Category: {', '.join(CATEGORIES)}")
    p_add.add_argument("-d", "--description", metavar="DESC", help="Short description")
    p_add.add_argument("--date", metavar="YYYY-MM-DD", help="Transaction date (default: today)")

    # list
    p_list = sub.add_parser("list", help="List transactions")
    p_list.add_argument("--month", metavar="YYYY-MM", help="Filter by month (default: current)")
    p_list.add_argument("-n", "--limit", type=int, default=50, metavar="N", help="Max rows (default: 50)")

    # summary
    p_summary = sub.add_parser("summary", help="Monthly summary by category")
    p_summary.add_argument("--month", metavar="YYYY-MM", help="Month (default: current)")

    # budget
    p_budget = sub.add_parser("budget", help="Manage monthly budgets")
    budget_sub = p_budget.add_subparsers(dest="budget_cmd", metavar="subcommand")
    p_bset = budget_sub.add_parser("set", help="Set a budget for a category")
    p_bset.add_argument("category", help=f"Category: {', '.join(CATEGORIES)}")
    p_bset.add_argument("amount", help="Monthly limit amount")
    budget_sub.add_parser("view", help="View budgets vs spending")

    # delete
    p_del = sub.add_parser("delete", help="Delete a transaction")
    p_del.add_argument("id", type=int, help="Transaction ID")

    # insight
    p_insight = sub.add_parser("insight", help="AI-powered financial insight (requires ANTHROPIC_API_KEY)")
    p_insight.add_argument("--month", metavar="YYYY-MM", help="Month to analyze (default: current)")

    return parser


def main() -> None:
    init_db()
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        print(f"\n  Database: {DB_PATH}")
        print(f"  Current month: {current_month()}\n")
        sys.exit(0)

    dispatch = {
        "add": cmd_add,
        "list": cmd_list,
        "summary": cmd_summary,
        "budget": cmd_budget,
        "delete": cmd_delete,
        "insight": cmd_insight,
    }

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
