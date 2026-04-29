from __future__ import annotations

import threading
import uuid
from datetime import datetime

from app.config import JOBS_FILE


JOBS_LOCK = threading.Lock()


# ── Persistence ────────────────────────────────────────────────────────────────

def load_jobs() -> list[dict]:
    if JOBS_FILE.exists():
        try:
            import json
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_jobs(jobs: list[dict]) -> None:
    import json
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Mutations ──────────────────────────────────────────────────────────────────

def update_job(job_id: str, **updates) -> dict | None:
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(updates)
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_jobs(jobs)
                return job
    return None


def create_job(payload: dict) -> dict:
    job = {
        "id":               str(uuid.uuid4()),
        "status":           "queued",
        "created_at":       datetime.now().isoformat(timespec="seconds"),
        "updated_at":       datetime.now().isoformat(timespec="seconds"),
        "payload":          payload,
        "logs":             [],
        "output_file":      "",
        "final_file":       "",
        "error":            "",
        "progress":         0,
        "abort_requested":  False,
    }
    with JOBS_LOCK:
        jobs = load_jobs()
        jobs.insert(0, job)
        save_jobs(jobs)
    return job


def append_log(job_id: str, line: str) -> None:
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.setdefault("logs", []).append(line)
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_jobs(jobs)
                return


def request_abort(job_id: str) -> dict | None:
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                if job.get("status") in {"queued", "running"}:
                    job["abort_requested"] = True
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    save_jobs(jobs)
                return job
    return None


def request_abort_all() -> int:
    count = 0
    with JOBS_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job.get("status") in {"queued", "running"} and not job.get("abort_requested"):
                job["abort_requested"] = True
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                count += 1
        save_jobs(jobs)
    return count


def is_abort_requested(job_id: str) -> bool:
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            return bool(job.get("abort_requested"))
    return False


# ── Startup reconcile ──────────────────────────────────────────────────────────

def startup_reconcile_jobs() -> None:
    with JOBS_LOCK:
        jobs = load_jobs()
        changed = False
        for job in jobs:
            if job.get("status") in {"queued", "running"}:
                job["status"]          = "aborted"
                job["abort_requested"] = True
                job["updated_at"]      = datetime.now().isoformat(timespec="seconds")
                job.setdefault("logs", []).append("Recovered stale job on startup")
                job["error"]    = job.get("error") or "Recovered stale job on startup"
                job["progress"] = max(int(job.get("progress") or 0), 100)
                changed = True
        if changed:
            save_jobs(jobs)
