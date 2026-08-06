import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("redrive_cli", ROOT / "scripts" / "redrive.py")
assert SPEC and SPEC.loader
redrive = importlib.util.module_from_spec(SPEC)
sys.modules["redrive_cli"] = redrive
SPEC.loader.exec_module(redrive)


def _msg(**overrides):
    base = {
        "MessageId": "mid-1",
        "ReceiptHandle": "rh-1",
        "Body": '{"job_id":"11111111-1111-1111-1111-111111111111","content_id":"22222222-2222-2222-2222-222222222222","content_hash":"abc123","object_key":"k","caption":"c"}',
    }
    base.update(overrides)
    return redrive.parse_dlq_message(base)


def test_parse_dlq_message_extracts_ids():
    msg = _msg()
    assert msg.job_id == "11111111-1111-1111-1111-111111111111"
    assert msg.content_hash == "abc123"
    assert msg.message_id == "mid-1"


def test_parse_falls_back_to_message_attributes():
    msg = redrive.parse_dlq_message(
        {
            "MessageId": "mid-2",
            "ReceiptHandle": "rh-2",
            "Body": "not-json",
            "MessageAttributes": {
                "job_id": {
                    "DataType": "String",
                    "StringValue": "11111111-1111-1111-1111-111111111111",
                },
                "content_hash": {"DataType": "String", "StringValue": "deadbeef"},
            },
        }
    )
    assert msg.job_id == "11111111-1111-1111-1111-111111111111"
    assert msg.content_hash == "deadbeef"


def test_plan_redrive_dead_job_resets():
    action = redrive.plan_redrive(_msg(), job_status="dead")
    assert action.reset_job is True
    assert action.skip_reason is None


def test_plan_redrive_failed_job_resets():
    action = redrive.plan_redrive(_msg(), job_status="failed")
    assert action.reset_job is True


def test_plan_skips_succeeded():
    action = redrive.plan_redrive(_msg(), job_status="succeeded")
    assert action.reset_job is False
    assert action.skip_reason == "job_already_succeeded"


def test_plan_job_id_filter():
    action = redrive.plan_redrive(
        _msg(),
        job_id_filter="99999999-9999-9999-9999-999999999999",
        job_status="dead",
    )
    assert action.skip_reason == "job_id_filter"


def test_plan_queued_moves_without_reset():
    action = redrive.plan_redrive(_msg(), job_status="queued")
    assert action.reset_job is False
    assert action.skip_reason == "job_status_queued"


def test_plan_missing_job_id():
    msg = redrive.parse_dlq_message(
        {"MessageId": "x", "ReceiptHandle": "y", "Body": '{"caption":"nope"}'}
    )
    action = redrive.plan_redrive(msg, job_status="dead")
    assert action.skip_reason == "missing_job_id"
