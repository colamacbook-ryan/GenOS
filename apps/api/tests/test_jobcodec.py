"""arq mã hoá việc/kết quả bằng JSON (gh/jobcodec.py) — không pickle: Redis bị ghi bậy không thành chạy mã."""

import pickle
from datetime import UTC, datetime

import pytest
from arq.jobs import DeserializationError, deserialize_job, serialize_job, serialize_result

from gh import jobcodec
from gh.worker import WorkerSettings


def test_worker_uses_json_codec() -> None:
    assert WorkerSettings.job_serializer is jobcodec.dumps
    assert WorkerSettings.job_deserializer is jobcodec.loads


def test_job_roundtrip_and_pickle_rejected() -> None:
    raw = serialize_job("backup_now", (), {"trigger": "manual"}, 1, 1_700_000_000_000, serializer=jobcodec.dumps)
    assert raw.startswith(b"{")
    job = deserialize_job(raw, deserializer=jobcodec.loads)
    assert job.function == "backup_now" and job.kwargs == {"trigger": "manual"}

    ran: list[str] = []

    class Boom:
        def __reduce__(self) -> tuple[object, tuple[str]]:
            return (ran.append, ("pwned",))

    evil = pickle.dumps({"t": 1, "f": "x", "a": (Boom(),), "k": {}, "et": 1})
    with pytest.raises(DeserializationError):
        deserialize_job(evil, deserializer=jobcodec.loads)
    assert ran == []


def test_unserializable_result_becomes_text() -> None:
    raw = serialize_result("f", (), {}, 1, 1, False, ValueError("hỏng"), 1, 2, "r", "arq:queue", "id",
                           serializer=jobcodec.dumps)
    assert raw is not None
    data = jobcodec.loads(raw)
    assert data["r"] == "hỏng" and data["s"] is False
    assert jobcodec.loads(jobcodec.dumps({"at": datetime(2026, 1, 1, tzinfo=UTC), "s": {1}}))["s"] == [1]
