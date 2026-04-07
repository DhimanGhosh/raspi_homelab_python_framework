from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path


class JobStore:
    def __init__(self, jobs_file: Path) -> None:
        self.jobs_file = jobs_file
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.jobs_file.exists():
            self._write({"jobs": {}})

    def _read(self) -> dict:
        return json.loads(self.jobs_file.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.jobs_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def create_job(self, job_type: str, target: str, metadata: dict | None = None) -> dict:
        data = self._read()
        job_id = str(uuid.uuid4())
        payload = {
            "job_id": job_id,
            "job_type": job_type,
            "target": target,
            "status": "queued",
            "progress": 0,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "metadata": metadata or {},
        }
        data["jobs"][job_id] = payload
        self._write(data)
        return payload

    def update_job(self, job_id: str, **updates) -> dict:
        data = self._read()
        job = data["jobs"][job_id]
        job.update(updates)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write(data)
        return job

    def get_job(self, job_id: str) -> dict | None:
        return self._read().get("jobs", {}).get(job_id)

    def list_jobs(self) -> list[dict]:
        jobs = list(self._read().get("jobs", {}).values())
        jobs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return jobs

    def clear_completed(self) -> int:
        data = self._read()
        before = len(data.get("jobs", {}))
        data["jobs"] = {
            job_id: payload
            for job_id, payload in data.get("jobs", {}).items()
            if payload.get("status") not in {"completed", "success"}
        }
        self._write(data)
        return before - len(data["jobs"])

    def clear_all(self) -> int:
        data = self._read()
        before = len(data.get("jobs", {}))
        data["jobs"] = {}
        self._write(data)
        return before
