"""Tests for OpenAI streaming service_tier capture in OTel instrumentation."""

from opentelemetry.instrumentation.openai.shared import chat_wrappers

from app.otel_utils import _patch_openai_streaming_service_tier_capture


def test_streaming_accumulator_captures_service_tier_from_late_chunk() -> None:
    _patch_openai_streaming_service_tier_capture()

    complete: dict = {"choices": [], "model": ""}

    chat_wrappers._accumulate_stream_items(
        {
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}}],
        },
        complete,
    )
    assert "service_tier" not in complete

    chat_wrappers._accumulate_stream_items(
        {
            "model": "gpt-4o-mini",
            "service_tier": "priority",
            "choices": [{"index": 0, "finish_reason": "stop"}],
        },
        complete,
    )

    assert complete["service_tier"] == "priority"


def test_streaming_accumulator_updates_service_tier_when_downgraded() -> None:
    _patch_openai_streaming_service_tier_capture()

    complete: dict = {"choices": [], "model": ""}

    chat_wrappers._accumulate_stream_items(
        {"service_tier": "priority", "choices": []},
        complete,
    )
    chat_wrappers._accumulate_stream_items(
        {"service_tier": "default", "choices": []},
        complete,
    )

    assert complete["service_tier"] == "default"
