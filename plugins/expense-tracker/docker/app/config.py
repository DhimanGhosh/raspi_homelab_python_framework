from __future__ import annotations
import os
from pathlib import Path

APP_NAME    = os.getenv("APP_NAME", "Expense Tracker")
APP_VERSION = os.getenv("APP_VERSION", "2.1.0")
PORT        = int(os.getenv("PORT", "8161"))
LLM_BACKEND   = os.getenv("LLM_BACKEND", "llama_cpp")  # llama_cpp|ollama
LLM_BASE_URL  = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080")
LLM_MODEL     = os.getenv("LLM_MODEL", "expense-agent")
LLM_TIMEOUT   = int(os.getenv("LLM_TIMEOUT", "120"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", LLM_MODEL)

DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/mnt/nas/homelab/runtime/expense-tracker/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/expenses.db"

CATEGORIES = [
    "Office Travel", "Grocery", "Restaurant", "Cigarette", "Subscription",
    "Other Travels", "Games", "Medicine", "CC Bill", "Maid Cash",
    "Bank Savings", "Pocket Money", "Bank GST", "Online Shopping", "Movies",
    "ATM Cash", "Mobile Recharge", "Offline Shopping", "Parcel",
    "Flat/Rent", "Utilities", "Outside Food", "Other",
]

CARDHOLDERS = ["Dhiman Ghosh", "Anushree Mitra"]
