import 'package:diane_phone/phone_state.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'restart preserves host-scoped drafts and immutable request identity',
    () async {
      const firstHost = 'https://first.test';
      const secondHost = 'https://second.test';
      final request = PhoneDeliveryItem(
        id: 'request-original',
        kind: 'send',
        text: 'Prepare the report',
        apiBase: firstHost,
        status: 'sending',
        createdAt: DateTime.utc(2026, 10, 20),
        payload: const {
          'request_id': 'request-original',
          'message_id': 'message-original',
          'workflow_id': '_scratch',
          'thread_id': '',
          'text': 'Prepare the report',
        },
      );
      final state = PhoneDeviceState(
        selections: {
          firstHost: {
            'thread_id': 'first-thread',
            'attachments': [
              {'id': 'image', 'data_url': 'data:image/png;base64,aGVsbG8='},
            ],
          },
          secondHost: {'thread_id': 'second-thread'},
        },
        drafts: {
          PhoneDeviceState.draftKey(firstHost, '_scratch', 'first-thread', ''):
              'First draft',
          PhoneDeviceState.draftKey(secondHost, '_scratch', 'first-thread', ''):
              'Other computer draft',
        },
        deliveries: [request],
      );
      final store = MemoryPhoneStatePersistence();
      await store.save(state.toJson());
      final restored = PhoneDeviceState.fromJson(await store.load());
      expect(restored.selections[firstHost]!['thread_id'], 'first-thread');
      expect(restored.selections[secondHost]!['thread_id'], 'second-thread');
      expect(
        restored.drafts.values,
        containsAll(['First draft', 'Other computer draft']),
      );
      expect(restored.deliveries.single.status, 'uncertain');
      final acknowledged = restored.deliveries.single.copyWith(
        status: 'accepted',
        threadId: 'created-thread',
        runId: 'created-run',
      );
      expect(acknowledged.payload, request.payload);
      expect(acknowledged.payload['thread_id'], '');
      expect(acknowledged.threadId, 'created-thread');
      expect(acknowledged.id, 'request-original');
    },
  );

  test(
    'receipt admission never mistakes rejected or ambiguous dispatch for success',
    () {
      expect(
        phoneReceiptDeliveryStatus({
          'delivery_status': 'accepted',
          'phase': 'needs_review',
          'status': 'needs_review',
        }),
        'needs_review',
      );
      expect(
        phoneReceiptDeliveryStatus({
          'delivery_status': 'accepted',
          'phase': 'rejected',
          'error_code': 'approval_expired',
        }),
        'expired',
      );
      expect(
        phoneReceiptDeliveryStatus({
          'delivery_status': 'rejected',
          'error_code': 'execution_context_unavailable',
        }),
        'rejected',
      );
      expect(
        phoneReceiptDeliveryStatus({'delivery_status': 'needs_review'}),
        'needs_review',
      );
      expect(
        phoneReceiptDeliveryStatus({
          'delivery_status': 'accepted',
          'phase': 'prepared',
        }),
        'uncertain',
      );
      expect(
        phoneReceiptDeliveryStatus({
          'delivery_status': 'accepted',
          'phase': 'submitted',
          'status': 'failed',
        }),
        'accepted',
      );
    },
  );

  test('reconnect stays bounded without a final retry cutoff', () {
    expect(phoneReconnectDelay(0), const Duration(seconds: 2));
    expect(phoneReconnectDelay(3), const Duration(seconds: 8));
    expect(phoneReconnectDelay(180), const Duration(seconds: 30));
    expect(phoneReconnectDelay(10000), const Duration(seconds: 30));
  });
}
