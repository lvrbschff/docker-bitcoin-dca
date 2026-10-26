"""Persistent local activity and dashboard snapshot storage."""
import json
import os
from pathlib import Path

ACTIVITY_PATH = Path("/data/activity.json")
SNAPSHOT_PATH = Path("/data/dashboard.json")


def read_activity_records():
    if not ACTIVITY_PATH.exists():
        return []
    payload = json.loads(ACTIVITY_PATH.read_text(encoding="utf-8"))
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Stored activity data must be a list")
    return [record for record in records if isinstance(record, dict) and isinstance(record.get("row"), dict)]


def read_activity_rows():
    return [record["row"] for record in read_activity_records()]


def append_activity_records(records):
    existing = read_activity_records()
    known_ids = {record.get("id") for record in existing}
    added = [record for record in records if record.get("id") not in known_ids]
    if not added:
        return 0
    existing.extend(added)
    write_json_atomic(ACTIVITY_PATH, {"records": existing})
    return len(added)


def read_dashboard_snapshot():
    if not SNAPSHOT_PATH.exists():
        return {}
    payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_dashboard_snapshot(snapshot):
    write_json_atomic(SNAPSHOT_PATH, snapshot)


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
