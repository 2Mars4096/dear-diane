import 'dart:async';

import 'package:diane_phone/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const host = 'https://computer.test';
const workflow = 'project';
final composer = find.byKey(const ValueKey('phone-composer'));

Map<String, dynamic> sessionState(String thread, {String? text}) => {
  'thread': {
    'id': thread,
    'title': 'Conversation $thread',
    'messages': [
      if (text != null)
        {'id': 'remote-$thread', 'role': 'assistant', 'content': text},
    ],
  },
  'run': null,
  'task': null,
  'execution_context': {
    'ready': true,
    'workspace_root': '/research',
    'agent_name': 'Research lead',
    'permission_mode': 'review',
  },
  'pending_approvals': [],
};

PhoneSessionSummary session(String thread) => PhoneSessionSummary(
  id: thread,
  workflowId: workflow,
  title: 'Conversation $thread',
  messageCount: 1,
  updatedAt: '2026-10-20T10:00:00Z',
  archived: false,
  mode: 'agent',
);

Map<String, dynamic> initialState({
  String thread = 'a',
  List<PhoneDeliveryItem> deliveries = const [],
}) => {
  'selections': {
    host: {
      'workflow_id': workflow,
      'thread_id': thread,
      'workspace_root': '/research',
      'title': 'Conversation $thread',
      'messages': [],
    },
  },
  'drafts': {
    PhoneDeviceState.draftKey(host, workflow, 'a', '/research'):
        'Original draft',
    PhoneDeviceState.draftKey(host, workflow, 'b', '/research'): 'Other draft',
  },
  'deliveries': deliveries.map((item) => item.toJson()).toList(),
};

class FakeApi implements PhoneApiClient {
  final calls =
      <
        ({String base, String method, String path, Map<String, Object?>? body})
      >[];
  final states = {
    'a': sessionState('a'),
    'b': sessionState('b', text: 'Conversation B is current.'),
  };
  final receipts = <String, Map<String, dynamic>>{};
  Completer<Map<String, dynamic>>? nextAState;
  bool offline = false;
  bool loseNextPostResponse = false;
  bool disconnectAfterAnswer = false;
  bool failReceiptReads = false;
  int stateReads = 0;
  int get postCount => calls.where((call) => call.method == 'POST').length;

  @override
  Future<Map<String, dynamic>> request(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    String token = '',
    Duration timeout = const Duration(seconds: 15),
  }) async {
    calls.add((base: base, method: method, path: path, body: body));
    if (offline) {
      throw PhoneApiFailure('Computer offline', uncertain: method != 'GET');
    }
    if (path == '/api/health') return {'status': 'ok'};
    if (path == '/api/chats') {
      return {
        'threads': [
          for (final id in ['a', 'b'])
            {
              'id': id,
              'workflow_id': workflow,
              'title': 'Conversation $id',
              'message_count': 1,
              'updated_at': '2026-10-20T10:00:00Z',
            },
        ],
      };
    }
    if (path.startsWith('/api/v2/tasks')) return {'tasks': []};
    if (path.endsWith('/state') && path.startsWith('/api/phone/sessions/')) {
      stateReads++;
      final thread = Uri.decodeComponent(path.split('/')[5]);
      if (thread == 'a' && nextAState != null) {
        final held = nextAState!;
        nextAState = null;
        return held.future;
      }
      return states[thread] ?? sessionState(thread);
    }
    if (path == '/api/phone/requests' && method == 'POST') {
      final id = body!['request_id'] as String;
      final thread = '${body['thread_id']}'.isEmpty
          ? 'created'
          : '${body['thread_id']}';
      final receipt = receipts.putIfAbsent(
        id,
        () => {
          'request_id': id,
          'delivery_status': 'accepted',
          'phase': 'submitted',
          'status': 'completed',
          'workflow_id': workflow,
          'thread_id': thread,
          'run_id': body['action'] == 'answer' ? body['run_id'] : '',
          'task_id': '',
          'workspace_root': '/research',
          'summary': 'Accepted',
        },
      );
      if (body['action'] != 'answer') {
        states[thread] = {
          ...sessionState(thread),
          'thread': {
            'id': thread,
            'title': 'Conversation $thread',
            'messages': [
              {
                'id': body['message_id'],
                'role': 'user',
                'content': body['text'],
              },
              {
                'id': 'desktop-concurrent',
                'role': 'assistant',
                'content': 'A concurrent desktop reply.',
              },
            ],
          },
        };
      }
      if (loseNextPostResponse) {
        loseNextPostResponse = false;
        offline = true;
        throw const PhoneApiFailure('Acknowledgement lost', uncertain: true);
      }
      if (body['action'] == 'answer' && disconnectAfterAnswer) {
        offline = true;
      }
      return receipt;
    }
    if (path.startsWith('/api/phone/requests/')) {
      if (failReceiptReads) {
        throw const PhoneApiFailure('Receipt lookup timed out');
      }
      final receipt = receipts[Uri.decodeComponent(path.split('/').last)];
      if (receipt == null) {
        throw const PhoneApiFailure('Unknown request', statusCode: 404);
      }
      return receipt;
    }
    return {};
  }
}

class DelayedStore extends MemoryPhoneStatePersistence {
  DelayedStore(super.value);
  Completer<void>? blockOutbound;
  final saveStarted = Completer<void>();
  @override
  Future<void> save(Map<String, Object?> state) async {
    if (blockOutbound != null && (state['deliveries'] as List).isNotEmpty) {
      final block = blockOutbound!;
      blockOutbound = null;
      if (!saveStarted.isCompleted) saveStarted.complete();
      await block.future;
    }
    await super.save(state);
  }
}

class UnreadableStore extends MemoryPhoneStatePersistence {
  int saves = 0;
  @override
  Future<Map<String, dynamic>> load() async =>
      throw StateError('Storage unavailable');
  @override
  Future<void> save(Map<String, Object?> state) async {
    saves++;
  }
}

Future<void> mount(
  WidgetTester tester,
  FakeApi api,
  PhoneStatePersistence store,
) async {
  await tester.pumpWidget(
    DanPhoneApp(apiClient: api, persistence: store, initialApiBase: host),
  );
  await tester.pumpAndSettle();
}

Future<void> unmount(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox.shrink());
  await tester.pump();
}

String composerText(WidgetTester tester) =>
    tester.widget<TextField>(composer).controller!.text;
Future<void> selectB(WidgetTester tester) async {
  await tester.tap(find.text('Work'));
  await tester.pumpAndSettle();
  tester
      .widget<PhoneWorkOverview>(find.byType(PhoneWorkOverview))
      .onSelectSession(session('b'));
  await tester.pump();
}

void main() {
  testWidgets(
    'outbound storage completes before sending; navigation keeps the other draft',
    (tester) async {
      final api = FakeApi();
      final store = DelayedStore(initialState());
      await mount(tester, api, store);
      final saved = Completer<void>();
      store.blockOutbound = saved;
      await tester.tap(find.byTooltip('Send message'));
      await tester.pump();
      expect(store.saveStarted.isCompleted, isTrue);
      expect(api.postCount, 0);
      expect(composerText(tester), 'Original draft');
      await selectB(tester);
      expect(composerText(tester), 'Other draft');
      await tester.enterText(composer, 'New text in B');
      await tester.pump(const Duration(milliseconds: 300));
      expect(api.postCount, 0);
      saved.complete();
      await tester.pumpAndSettle();
      expect(composerText(tester), 'New text in B');
      expect(api.postCount, 1);
      final post = api.calls.singleWhere((call) => call.method == 'POST');
      expect(post.base, host);
      expect(post.body!['thread_id'], 'a');
      expect(post.body!['text'], 'Original draft');
      expect(post.body!.containsKey('profile_policy'), isFalse);
      expect(post.body!.containsKey('workspace_root'), isFalse);
      final restored = PhoneDeviceState.fromJson(await store.load());
      expect(restored.selections[host]!['thread_id'], 'b');
      expect(
        restored.drafts[PhoneDeviceState.draftKey(
          host,
          workflow,
          'b',
          '/research',
        )],
        'New text in B',
      );
      expect(
        restored.drafts[PhoneDeviceState.draftKey(
          host,
          workflow,
          'a',
          '/research',
        )],
        '',
      );
      expect(
        api.calls.where(
          (call) => call.method == 'PUT' && call.path.startsWith('/api/chats'),
        ),
        isEmpty,
      );
      expect(tester.takeException(), isNull);
      await unmount(tester);
    },
  );

  testWidgets(
    'failed outbound storage cannot restore old content into another conversation',
    (tester) async {
      final api = FakeApi();
      final store = DelayedStore(initialState());
      await mount(tester, api, store);
      final saved = Completer<void>();
      store.blockOutbound = saved;
      await tester.tap(find.byTooltip('Send message'));
      await tester.pump();
      await selectB(tester);
      await tester.enterText(composer, 'Keep this draft in B');
      saved.completeError(StateError('Device storage failed'));
      await tester.pumpAndSettle();
      expect(composerText(tester), 'Keep this draft in B');
      expect(api.postCount, 0);
      expect(find.text('Conversation B is current.'), findsOneWidget);
      final restored = PhoneDeviceState.fromJson(await store.load());
      expect(restored.deliveries, isEmpty);
      expect(
        restored.drafts[PhoneDeviceState.draftKey(
          host,
          workflow,
          'a',
          '/research',
        )],
        'Original draft',
      );
      expect(tester.takeException(), isNull);
      await unmount(tester);
    },
  );

  testWidgets(
    'an unreadable saved outbox is preserved and prevents new delivery',
    (tester) async {
      final api = FakeApi();
      final store = UnreadableStore();
      await mount(tester, api, store);
      await tester.enterText(composer, 'Keep my draft');
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      expect(api.postCount, 0);
      expect(store.saves, 0);
      expect(composerText(tester), 'Keep my draft');
      expect(
        find.textContaining('Saved phone work could not be loaded'),
        findsOneWidget,
      );
      await unmount(tester);
    },
  );

  testWidgets(
    'worker approval identities remain distinct when request IDs match',
    (tester) async {
      final api = FakeApi();
      api.states['a'] = {
        ...sessionState('a'),
        'run': {'run_id': 'run-a', 'status': 'needs_input'},
        'task': {'task_id': 'task-a', 'status': 'needs_input'},
        'pending_approvals': [
          for (final worker in ['worker-a', 'worker-b'])
            {
              'request_id': '1',
              'run_id': 'run-a',
              'worker_id': worker,
              'method': 'item/commandExecution/requestApproval',
              'params': {'command': 'python $worker.py'},
            },
        ],
      };
      final store = MemoryPhoneStatePersistence(initialState());
      await mount(tester, api, store);
      final first = tester
          .widgetList<PhoneApprovalCard>(find.byType(PhoneApprovalCard))
          .singleWhere((card) => card.request.workerId == 'worker-a');
      unawaited(
        first.onReply!(first.request, {
          'decision': 'accept',
          'answers': <String, String>{},
        }),
      );
      await tester.pumpAndSettle();
      expect(
        tester
            .widgetList<PhoneApprovalCard>(find.byType(PhoneApprovalCard))
            .where((card) => card.request.workerId == 'worker-a'),
        isEmpty,
      );
      final second = tester
          .widgetList<PhoneApprovalCard>(find.byType(PhoneApprovalCard))
          .singleWhere((card) => card.request.workerId == 'worker-b');
      expect(second.request.canAnswer, isTrue);
      unawaited(
        second.onReply!(second.request, {
          'decision': 'decline',
          'answers': <String, String>{},
        }),
      );
      await tester.pumpAndSettle();
      final posts = api.calls.where((call) => call.method == 'POST').toList();
      expect(posts, hasLength(2));
      expect(posts.map((call) => call.body!['worker_id']), [
        'worker-a',
        'worker-b',
      ]);
      expect(posts.map((call) => call.body!['approval_request_id']), [
        '1',
        '1',
      ]);
      expect(posts.map((call) => call.body!['run_id']), ['run-a', 'run-a']);
      expect(tester.takeException(), isNull);
      await unmount(tester);
    },
  );

  testWidgets(
    'an accepted answer keeps the live run controllable until canonical state confirms completion',
    (tester) async {
      final api = FakeApi()..disconnectAfterAnswer = true;
      api.states['a'] = {
        ...sessionState('a'),
        'run': {'run_id': 'run-a', 'status': 'needs_input'},
        'task': {'task_id': 'task-a', 'status': 'needs_input'},
        'pending_approvals': [
          {
            'request_id': 'approval',
            'run_id': 'run-a',
            'method': 'item/commandExecution/requestApproval',
            'params': {'command': 'python report.py'},
          },
        ],
      };
      final store = MemoryPhoneStatePersistence(initialState());
      await mount(tester, api, store);
      await tester.enterText(composer, '');
      final card = tester.widget<PhoneApprovalCard>(
        find.byType(PhoneApprovalCard),
      );
      unawaited(
        card.onReply!(card.request, {
          'decision': 'accept',
          'answers': <String, String>{},
        }),
      );
      await tester.pumpAndSettle();
      expect(
        PhoneDeviceState.fromJson(await store.load()).deliveries.single.status,
        'accepted',
      );
      expect(
        tester
            .widget<PhoneConversationView>(find.byType(PhoneConversationView))
            .activeRunLive,
        isTrue,
      );
      expect(find.byTooltip('Stop current task'), findsOneWidget);
      await tester.enterText(composer, 'Change the chart');
      await tester.pump();
      expect(find.byTooltip('Steer current task'), findsOneWidget);
      await unmount(tester);
    },
  );

  testWidgets(
    'a late response from the previous conversation cannot replace the selected one',
    (tester) async {
      final api = FakeApi();
      final store = MemoryPhoneStatePersistence(initialState());
      await mount(tester, api, store);
      final oldResponse = Completer<Map<String, dynamic>>();
      api.nextAState = oldResponse;
      await tester.pump(const Duration(seconds: 2));
      await selectB(tester);
      await tester.pumpAndSettle();
      expect(find.text('Conversation B is current.'), findsOneWidget);
      oldResponse.complete(sessionState('a', text: 'Stale response from A.'));
      await tester.pumpAndSettle();
      expect(find.text('Conversation B is current.'), findsOneWidget);
      expect(find.text('Stale response from A.'), findsNothing);
      expect(composerText(tester), 'Other draft');
      await unmount(tester);
    },
  );

  testWidgets(
    'a lost acknowledgement reconciles by request ID after restart without another POST',
    (tester) async {
      final api = FakeApi()..loseNextPostResponse = true;
      final store = MemoryPhoneStatePersistence(initialState());
      await mount(tester, api, store);
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      expect(api.postCount, 1);
      final saved = PhoneDeviceState.fromJson(await store.load());
      expect(saved.deliveries.single.status, 'uncertain');
      final id = saved.deliveries.single.id;
      await unmount(tester);
      api.offline = false;
      await mount(tester, api, store);
      expect(api.postCount, 1);
      expect(
        api.calls.any(
          (call) =>
              call.path == '/api/phone/requests/$id' && call.method == 'GET',
        ),
        isTrue,
      );
      expect(
        PhoneDeviceState.fromJson(await store.load()).deliveries.single.status,
        'accepted',
      );
      expect(find.text('A concurrent desktop reply.'), findsOneWidget);
      expect(
        api.calls.where(
          (call) => call.method == 'PUT' && call.path.startsWith('/api/chats'),
        ),
        isEmpty,
      );
      await unmount(tester);
    },
  );

  testWidgets(
    'a failed receipt lookup retains original uncertainty and can later recover without resending',
    (tester) async {
      final api = FakeApi()..failReceiptReads = true;
      final delivery = PhoneDeliveryItem(
        id: 'uncertain-request',
        kind: 'send',
        text: 'Original work',
        apiBase: host,
        workflowId: workflow,
        threadId: 'a',
        status: 'uncertain',
        createdAt: DateTime.utc(2026),
        payload: const {
          'request_id': 'uncertain-request',
          'thread_id': 'a',
          'workflow_id': workflow,
          'action': 'send',
          'text': 'Original work',
          'message_id': 'stable-message',
        },
      );
      final store = MemoryPhoneStatePersistence(
        initialState(deliveries: [delivery]),
      );
      await mount(tester, api, store);
      expect(api.postCount, 0);
      expect(
        PhoneDeviceState.fromJson(await store.load()).deliveries.single.status,
        'uncertain',
      );
      api.failReceiptReads = false;
      api.receipts['uncertain-request'] = {
        'request_id': 'uncertain-request',
        'delivery_status': 'accepted',
        'phase': 'submitted',
        'status': 'completed',
        'workflow_id': workflow,
        'thread_id': 'a',
      };
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(api.postCount, 0);
      expect(
        PhoneDeviceState.fromJson(await store.load()).deliveries.single.status,
        'accepted',
      );
      await unmount(tester);
    },
  );

  testWidgets(
    'polling continues beyond the old six-minute window and resumes after backgrounding',
    (tester) async {
      final api = FakeApi();
      final store = MemoryPhoneStatePersistence(initialState());
      await mount(tester, api, store);
      final initialReads = api.stateReads;
      for (var cycle = 0; cycle < 185; cycle++) {
        await tester.pump(const Duration(seconds: 2));
        await tester.pump();
      }
      expect(api.stateReads, greaterThanOrEqualTo(initialReads + 185));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
      await tester.pump();
      final pausedReads = api.stateReads;
      await tester.pump(const Duration(minutes: 2));
      expect(api.stateReads, pausedReads);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pumpAndSettle();
      expect(api.stateReads, greaterThan(pausedReads));
      expect(api.calls.where((call) => call.path.endsWith('/events')), isEmpty);
      await unmount(tester);
    },
  );
}
