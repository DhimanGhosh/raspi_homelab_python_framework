# Expense Tracker 2.1.0 Implementation Summary

## Implemented

- Bumped plugin/app version to `2.1.0`.
- Kept plugin id and folder name as `expense-tracker`.
- Made llama.cpp OpenAI-compatible server the default local LLM backend.
- Moved model-storage assumption to `/mnt/nas/ai/models/...` for low-storage Raspberry Pi installs.
- Added scripts for WSL2 setup, llama.cpp build, synthetic dataset generation, QLoRA fine-tuning, LoRA merge, GGUF export, Q4 quantization, and Pi systemd inference.
- Replaced fixed prompt routing with a local LLM agent through `/api/ask` and the Ask tab.
- Added local LLM tools for financial overview, category comparison, top expenses, goal status, category totals, and transaction search.
- Added WSL2/RTX fine-tuning kit under `training/`.
- Added offline ML category prediction using scikit-learn.
- Added typed/custom categories learned from saved expenses and recurring templates.
- Added one global current bank balance with automatic debit/credit deltas.
- Added recurring projections to dashboard, budget status, and analytics.
- Added smart spending notes and investment suggestions.
- Fixed Dashboard Recent Expenses so it excludes credited transactions.
- Added startup migration support for the `app_settings` table.

## Backend

- `balance_service.py`: stores and updates current balance in `app_settings`.
- `ml_service.py`: trains a lightweight local classifier from expense history and falls back to keyword rules.
- `expense_service.py`: applies balance deltas, returns learned categories, includes recurring projections, and generates insights.
- `recurring_service.py`: projects active templates into monthly totals without double-counting generated `[Auto]` rows.
- `routes.py`: exposes `/api/balance`, richer `/api/predict-category`, recurring-aware dashboard/analytics, and learned categories.
- `agent_service.py`: calls llama.cpp `/v1/chat/completions` or optional Ollama `/api/chat` with tool schemas, executes selected finance tools, and feeds results back for final answers.

## Frontend

- Dashboard now includes bank balance, projected recurring impact, smart notes, and investment suggestions.
- Quick Add and modal forms use typed category inputs with shared suggestions.
- Description fields request category predictions and show ML/rule hints.
- Analytics trend chart includes projected recurring expense data.
- Category filters refresh from learned categories.
- Ask tab submits free-form prompts, shows local model/source, and renders tool evidence plus follow-up prompt buttons.

## Verification

- `python3 -m compileall plugins/expense-tracker/docker/app` passed.
- Temporary localhost API test confirmed:
- `/api/health` reports `2.1.0`.
- `/api/ask` returns a clear local LLM setup message when a model server is not running.
- Browser smoke test validates the Ask tab UI path.
  - Balance set/create credit/create debit/edit/delete deltas are correct.
  - `/api/predict-category` returns ML prediction details.
  - Dashboard recent items contain only negative expense amounts.
  - Recurring projections appear in dashboard and analytics totals.
  - A newly typed category appears in `/api/categories`.
- `find .. -name '*.tgz' -print` returned no `.tgz` artifacts.
