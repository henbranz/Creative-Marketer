# mypy: disable-error-code="no-untyped-def"

from scripts.pipeline_operator_simulation import run, validate_action_matrix


def test_operator_simulation_proves_complete_action_matrix(capsys) -> None:
    assert validate_action_matrix() == ()
    assert run() == 0
    output = capsys.readouterr().out
    assert "RUN_RESEARCH" in output
    assert "RESTRATEGIZE_CREATIVE" in output
    assert "REVIEW_PRODUCTION_PLAN" in output
    assert output.endswith(
        "ALL RESOLVER ACTIONS HAVE EXECUTION BEHAVIOR\nNO NORMAL PIPELINE DEAD ENDS FOUND\n"
    )
