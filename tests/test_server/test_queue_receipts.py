from dan.server.chat_v2_store import V2TaskRecord, QueueItemRecord


def test_completed_delivery_survives_snapshot_without_leaking_request_context():
    task = V2TaskRecord(task_id='task', queue_items=[
        QueueItemRecord(id='done', task_id='task', lane='append', surface_turn_id='turn', status='completed', text='large request', metadata={
            'delivered_run_id':'run', 'command_payload':{'client_message_id':'user', 'surface_context':{'private':'context'}}}),
        QueueItemRecord(id='pending', task_id='task', lane='append', surface_turn_id='turn2', status='queued'),
        QueueItemRecord(id='cancelled', task_id='task', lane='append', surface_turn_id='turn3', status='cancelled', metadata={'command_payload':{'client_message_id':'withdrawn'}}),
    ])
    snapshot = task.snapshot().model_dump(mode='json')
    assert [q['id'] for q in snapshot['metadata']['queue_items']] == ['pending']
    receipt = snapshot['metadata']['queue_receipts'][0]
    assert receipt['metadata']['delivered_run_id'] == 'run'
    assert receipt['metadata']['command_payload']['client_message_id'] == 'user'
    assert receipt['text'] == ''
    assert 'surface_context' not in str(snapshot)
    assert snapshot['metadata']['queue_receipts'][1]['status'] == 'cancelled'
    assert snapshot['metadata']['append_queue_length'] == 1
