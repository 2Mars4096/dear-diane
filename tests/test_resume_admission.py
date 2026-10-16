from diane.server.chat_v2 import SurfaceTurn
from diane.server.chat_v2_async_core import (
    BoardRun, BoardSnapshot, TaskBoard, ForegroundAdmissionInput,
    classify_foreground_turn, parse_admission_command,
)


def admission(text='resume', *, prior_thread='chat', active=False):
    row = BoardRun(run_id='old-run', task_id='old-task', thread_id=prior_thread, status='paused' if active else 'completed')
    board = BoardSnapshot(board=TaskBoard(thread_id='chat'), active_runs=[row] if active else [], completed_runs=[] if active else [row])
    return ForegroundAdmissionInput(surface_turn=SurfaceTurn(id='resume-turn', surface='chat', text=text, thread_id='chat'), user_text=text, thread_id='chat', board_snapshot=board, command_hints=parse_admission_command(text))


def test_resume_in_settled_conversation_reaches_lead_not_clarification():
    decision = classify_foreground_turn(admission())
    assert decision.action == 'start_parallel'
    assert decision.relation == 'continuation'
    assert 'control_command' not in decision.metadata


def test_resume_does_not_borrow_another_chats_context_or_change_explicit_controls():
    assert classify_foreground_turn(admission(prior_thread='other')).action == 'ask_clarification'
    assert classify_foreground_turn(admission('/resume')).action == 'ask_clarification'
    decision = classify_foreground_turn(admission(active=True))
    assert decision.metadata['control_command'] == 'resume'
    assert decision.target_run_id == 'old-run'
