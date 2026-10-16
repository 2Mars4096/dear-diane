from diane.server.chat_v2 import SurfaceTurn
from diane.server.chat_v2_async_core import (
    BoardRun, BoardSnapshot, TaskBoard, ForegroundAdmissionInput,
    classify_foreground_turn, parse_admission_command,
)


def admission(text='resume', *, prior_thread='chat', active=False):
    row = BoardRun(run_id='old-run', task_id='old-task', thread_id=prior_thread, status='paused' if active else 'completed')
    board = BoardSnapshot(board=TaskBoard(thread_id='chat'), active_runs=[row] if active else [], completed_runs=[] if active else [row])
    return ForegroundAdmissionInput(surface_turn=SurfaceTurn(id='resume-turn', surface='chat', text=text, thread_id='chat'), user_text=text, thread_id='chat', board_snapshot=board, command_hints=parse_admission_command(text))


def test_ordinary_words_are_not_control_commands():
    for text in ['resume', 'stop using that library', 'pause the animation', 'cancel the subscription', 'status report on this paper', 'tasks remaining', 'progress on the design']:
        hints = parse_admission_command(text)
        assert not any([hints.explicit_resume, hints.explicit_pause, hints.explicit_cancel, hints.explicit_status])
        assert hints.payload_text == text
    assert classify_foreground_turn(admission()).action == 'start_parallel'


def test_explicit_slash_commands_retain_targeted_control_behavior():
    assert classify_foreground_turn(admission('/resume')).action == 'ask_clarification'
    decision = classify_foreground_turn(admission('/resume', active=True))
    assert decision.metadata['control_command'] == 'resume'
    assert decision.target_run_id == 'old-run'
    for text in ['/stop', '/cancel', '/pause', '/resume', '/status', '/tasks']:
        hints = parse_admission_command(text)
        assert any([hints.explicit_resume, hints.explicit_pause, hints.explicit_cancel, hints.explicit_status])
