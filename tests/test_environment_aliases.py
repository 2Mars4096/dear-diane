from diane.environment import apply_environment_aliases


def test_canonical_names_override_legacy_and_preserve_other_variables():
    env = {'DIANE_LLM_MODEL': 'chosen', 'DAN_LLM_MODEL': 'old', 'DAN_GRAPHS_DIR': '/legacy', 'PATH': '/bin'}
    apply_environment_aliases(env)
    assert env['DAN_LLM_MODEL'] == 'chosen'
    assert env['DAN_GRAPHS_DIR'] == '/legacy'
    assert env['PATH'] == '/bin'
    assert apply_environment_aliases(env) == env
