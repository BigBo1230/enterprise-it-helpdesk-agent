from ai_agent_template.agent.graph import (
    remove_ungrounded_contact_lines,
    remove_unsupported_action_claims,
)


def test_removes_contact_details_absent_from_trusted_context() -> None:
    answer = (
        "请先确认网络连接。\n"
        "访问 https://support.example.com 提交请求。\n"
        "也可以联系 ext. 5555，工作时间为 9:00-18:00。"  # noqa: RUF001
    )

    sanitized = remove_ungrounded_contact_lines(answer, "VPN 常见问题排查手册")

    assert "请先确认网络连接" in sanitized
    assert "support.example.com" not in sanitized
    assert "5555" not in sanitized
    assert "9:00-18:00" not in sanitized
    assert "无法从已检索的公司资料中验证" in sanitized


def test_preserves_contact_details_present_in_trusted_context() -> None:
    answer = "请访问 https://support.example.com。"

    assert remove_ungrounded_contact_lines(answer, answer) == answer


def test_removes_unverified_portal_name() -> None:
    sanitized = remove_ungrounded_contact_lines(
        "请访问密码重置门户 [portal-password-reset]。",
        "VPN 常见问题排查手册",
    )

    assert "portal-password-reset" not in sanitized
    assert "无法从已检索的公司资料中验证" in sanitized


def test_removes_handoff_claim_without_tool_evidence() -> None:
    sanitized = remove_unsupported_action_claims(
        "我将为您发起人工服务台转接请求。",
        "Ticket service is unavailable.",
    )

    assert "我将" not in sanitized
    assert "没有可核验的工具结果" in sanitized


def test_preserves_escalation_claim_with_tool_evidence() -> None:
    answer = "工单已升级人工。"
    evidence = '{"id":"IT-123","status":"escalated"}'

    assert remove_unsupported_action_claims(answer, evidence) == answer


def test_removes_english_retry_promise_without_tool_evidence() -> None:
    answer = (
        "The request could not be confirmed. "
        "I will retry once using the same idempotency key."
    )

    sanitized = remove_unsupported_action_claims(answer, "Ticket service is unavailable.")

    assert "I will retry" not in sanitized
    assert "无法确认该操作是否完成" in sanitized


def test_does_not_treat_verified_channel_phrase_as_completed_action() -> None:
    answer = "请使用你已经确认的官方 IT 服务渠道，或要求升级至人工支持。"  # noqa: RUF001

    assert remove_unsupported_action_claims(answer, "") == answer


def test_future_action_without_tool_attempt_reports_no_write() -> None:
    sanitized = remove_unsupported_action_claims("我将为您创建工单。", "")

    assert "我将" not in sanitized
    assert "尚未执行任何写操作" in sanitized
