# Expense Tracker Plugin for Homelab OS

Version: 2.1.0

Expense Tracker is a local-first FastAPI and SQLite plugin for tracking debits, credits, budgets, recurring expenses, and spending analytics from Homelab OS.

## What's New in 2.1.0

- Default local AI runtime changed to NAS-backed `llama.cpp` instead of requiring Ollama.
- The app now targets an OpenAI-compatible `llama-server` endpoint at `LLM_BASE_URL`.
- The model file is expected to live under `/mnt/nas/ai/models/...`, so the Raspberry Pi SD card is not used for model storage.
- Expanded `training/` into a full Windows WSL2 workflow: synthetic dataset generation, QLoRA fine-tuning, LoRA merge, GGUF conversion, Q4 quantization, and Raspberry Pi systemd service.

## What's New in 2.0.0

- Replaced the fixed prompt router with a real local agent backed by Ollama.
- Added finance-analysis tools the local LLM can call for dashboard context, category comparisons, top expenses, goal status, category totals, and transaction search.
- `/api/ask` now sends the user's free-form question to the local model with tool schemas and returns the model answer plus tool evidence.
- Added Raspberry Pi local-model settings in `docker-compose.yml`.
- Added `training/` workflow for generating Q&A/tool-use datasets and fine-tuning a small model on Windows WSL2 with an RTX GPU.

## What's New in 1.2.0

- Added local prompt-based tracking through the Ask tab.
- Added `/api/ask` for natural-language expense questions.
- Supports questions about month-to-month category changes, expenses to reduce for goals, goal time left, and top expenses across recent months.
- Answers include a short explanation plus supporting rows.

## What's New in 1.1.0

- Native offline ML categorization using scikit-learn `TfidfVectorizer` and `MultinomialNB`.
- Typed custom categories are supported and learned from expenses and recurring templates.
- Current bank balance can be set once and is automatically updated by debits and credits.
- Dashboard and analytics include projected monthly/yearly recurring expenses.
- Dashboard Recent Expenses now shows debits only; credits remain in Transactions.
- Smart spending descriptions, category charts, recurring impact, and investment suggestions are generated locally.

## Architecture

```text
expense-tracker/
├── plugin.json
└── docker/
    ├── Dockerfile
    ├── docker-compose.yml
    ├── app.py
    ├── app/
    │   ├── config.py
    │   ├── database.py
    │   ├── models.py
    │   ├── routes.py
    │   └── services/
    │       ├── balance_service.py
    │       ├── budget_service.py
    │       ├── expense_service.py
    │       ├── ml_service.py
    │       └── recurring_service.py
    ├── templates/index.html
    └── static/
        ├── css/styles.css
        └── js/script.js
```

## Runtime

- Backend: FastAPI, SQLAlchemy, SQLite, APScheduler
- ML: scikit-learn, trained from local transaction descriptions
- Frontend: vanilla JavaScript and Chart.js
- Data: `/mnt/nas/homelab/runtime/expense-tracker/data/expenses.db`
- Local AI: llama.cpp OpenAI-compatible server with a NAS-hosted GGUF model.

## Local AI Setup

Recommended Raspberry Pi setup:

```bash
/mnt/nas/ai/bin/llama-server \
  -m /mnt/nas/ai/models/expense-agent/expense-agent-q4_k_m.gguf \
  -c 4096 \
  --host 0.0.0.0 \
  --port 8080
```

The Docker service uses:

```yaml
LLM_BACKEND: "llama_cpp"
LLM_BASE_URL: "http://host.docker.internal:8080"
LLM_MODEL: "expense-agent"
```

Ollama is optional. If you choose to use it anyway, set `LLM_BACKEND=ollama`.

## Key Endpoints

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/analytics?months=6`
- `POST /api/ask`
- `GET /api/expenses`
- `POST /api/expenses`
- `PUT /api/expenses/{id}`
- `DELETE /api/expenses/{id}`
- `GET /api/balance`
- `POST /api/balance`
- `GET /api/categories`
- `POST /api/predict-category`
- `GET /api/budget`
- `POST /api/budget`
- `GET /api/recurring`
- `POST /api/recurring`
- `PUT /api/recurring/{id}`
- `DELETE /api/recurring/{id}`

## Notes

- The plugin folder and id remain `expense-tracker`.
- No `.tgz` package is required for source editing.
- Existing SQLite installs are migrated on startup by creating the `app_settings` table when missing.
