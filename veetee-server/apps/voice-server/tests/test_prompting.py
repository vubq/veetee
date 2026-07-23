from __future__ import annotations

from datetime import UTC, datetime

import pytest

from veetee_voice_server.prompting import (
    DEFAULT_AGENT_BASE_PROMPT,
    PromptConfiguration,
    PromptTemplateError,
    render_prompt_template,
    validate_prompt_template,
)


def configuration(template: str = DEFAULT_AGENT_BASE_PROMPT) -> PromptConfiguration:
    return PromptConfiguration(
        template=template,
        language="Tiếng Việt tự nhiên",
        time_zone="Asia/Bangkok",
        personality_preset_id="stubborn-reasoned",
        personality="Có chính kiến, hơi ngang bướng nhưng chịu đổi ý trước bằng chứng.",
        response_style="Ngắn gọn, nói thẳng và phù hợp để đọc thành tiếng.",
        user_address="bạn",
        time_zone_source="device",
    )


def test_render_prompt_uses_all_static_and_runtime_variables() -> None:
    prompt = configuration().render(
        agent_name="Mây",
        locale="vi-VN",
        persona="Trợ lý khoa học cho gia đình.",
        interaction_mode="auto",
        config_version=8,
        tools=[{"name": "self.get_device_status", "description": "Đọc trạng thái thiết bị."}],
        now=datetime(2026, 7, 23, 3, 4, 5, tzinfo=UTC),
    )

    assert "Mây" in prompt
    assert "Tiếng Việt tự nhiên" in prompt
    assert "Trợ lý khoa học cho gia đình." in prompt
    assert "hơi ngang bướng" in prompt
    assert "2026-07-23" in prompt
    assert "10:04:05" in prompt
    assert "self.get_device_status" in prompt
    assert "{{" not in prompt

def test_render_prompt_prefers_reported_device_locale_and_time_zone() -> None:
    prompt = configuration().render(
        agent_name="Mây",
        locale="vi-VN",
        persona="Trợ lý gia đình.",
        interaction_mode="auto",
        config_version=8,
        tools=[],
        device_locale="en-US",
        device_time_zone="America/New_York",
        device_time_zone_offset_minutes=-240,
        now=datetime(2026, 7, 23, 3, 4, 5, tzinfo=UTC),
    )

    assert "en-US" in prompt
    assert "America/New_York" in prompt
    assert "UTC-04:00" in prompt
    assert "2026-07-22" in prompt


@pytest.mark.parametrize(
    "template",
    [
        "{{agent_name}} {{language}} {{persona}} {{personality}} {{unknown}}",
        "{{agent_name}} {{language}} {{persona}} {{personality}} {{current_time",
        "{{agent_name}} {{language}} {{persona}} {{personality}} {{name | upper}}",
        "{{agent_name}} {{language}} {{persona}} {{personality}} {% for x in tools %}",
        "{{agent_name}} {{language}} {{persona}}",
    ],
)
def test_prompt_validation_rejects_unknown_malformed_or_missing_tokens(template: str) -> None:
    with pytest.raises(PromptTemplateError):
        validate_prompt_template(template)


def test_prompt_renderer_never_evaluates_template_expressions() -> None:
    template = "{{agent_name}} {{language}} {{persona}} {{personality}} {{__class__.__mro__}}"
    with pytest.raises(PromptTemplateError):
        render_prompt_template(
            template,
            {
                "agent_name": "VeeTee",
                "language": "Tiếng Việt",
                "persona": "fixture",
                "personality": "fixture",
            },
        )


def test_prompt_configuration_rejects_an_unknown_time_zone() -> None:
    with pytest.raises(PromptTemplateError, match="time zone"):
        PromptConfiguration.from_payload(
            {
                "template": DEFAULT_AGENT_BASE_PROMPT,
                "language": "Tiếng Việt",
                "timeZone": "Mars/Olympus_Mons",
                "personalityPresetId": "warm-empathetic",
                "personality": "Ấm áp và tự nhiên.",
                "responseStyle": "",
                "userAddress": "",
            },
            defaults=configuration(),
        )
