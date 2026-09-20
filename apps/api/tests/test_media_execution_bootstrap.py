from scripts.bootstrap_media_execution import MEDIA_TOOL_KEYS, media_execution_configuration


def test_media_execution_principal_is_zero_model_and_narrowly_tool_scoped() -> None:
    configuration = media_execution_configuration()

    assert configuration.run_budget_policy.max_model_calls == 0
    assert configuration.memory_scopes == ()
    assert configuration.read_scopes == ()
    assert configuration.write_scopes == ("production.media",)
    assert set(configuration.allowed_tool_keys) == set(MEDIA_TOOL_KEYS)
    assert configuration.denied_tool_keys == ()
