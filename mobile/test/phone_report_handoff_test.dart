import 'dart:async';
import 'dart:convert';

import 'package:diane_phone/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'phone_handoff_test.dart' as handoff;

const _reportPath = '/research/reports/quarterly.md';
const _secondPath = '/research/reports/revised.md';
const _otherRoot = '/other';
const _otherReportPath = '/other/reports/other.md';
const _reportText =
    '# Quarterly report\nRevenue rose from 40 to 52 units.\n'
    'The increase came from repeat customers.';
const _secondText = '# Revised report\nThe corrected total is 49 units.';
const _report = PhonePreviewArtifact(
  id: 'quarterly-report',
  title: 'Quarterly report',
  path: _reportPath,
  url: '',
  kind: 'file',
  source: 'run',
);
const _secondReport = PhonePreviewArtifact(
  id: 'revised-report',
  title: 'Revised report',
  path: _secondPath,
  url: '',
  kind: 'file',
  source: 'run',
);

final _ask = find.byKey(const ValueKey('phone-ask-report'));
final _reference = find.byKey(const ValueKey('phone-report-reference'));
final _removeReference = find.byKey(const ValueKey('phone-remove-report'));

Map<String, Object?> _file(String path, {String root = '/research'}) => {
  'path': path,
  'relative_path': path.startsWith('$root/')
      ? path.substring(root.length + 1)
      : path.split('/').last,
  'name': path.split('/').last,
  'parent': '/research/reports',
  'is_directory': false,
  'size': 100,
  'depth': 1,
};

Map<String, dynamic> _readResult(
  String path,
  String text, {
  String root = '/research',
}) => {
  'file': _file(path, root: root),
  'content': text,
  'truncated': false,
  'root': root,
};

class _ReportApi extends handoff.FakeApi {
  _ReportApi({bool running = false}) {
    states['a'] = {
      ...handoff.sessionState('a'),
      'run': {'run_id': 'run-a', 'status': running ? 'running' : 'completed'},
      'task': {
        'task_id': 'task-a',
        'status': running ? 'running' : 'completed',
        'artifacts': [
          {
            'id': _report.id,
            'name': _report.title,
            'path': _report.path,
            'kind': _report.kind,
          },
        ],
      },
    };
  }

  final reads = <String>[];
  final heldReads = <String, Completer<Map<String, dynamic>>>{};
  final failedReads = <String>{};
  final texts = {_reportPath: _reportText, _secondPath: _secondText};
  Completer<void>? heldPostResponse;

  @override
  Future<Map<String, dynamic>> request(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    String token = '',
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final uri = Uri.parse(path);
    if (uri.path == '/api/workspace-files' ||
        uri.path == '/api/workspace-files/read') {
      calls.add((base: base, method: method, path: path, body: body));
      if (offline) throw const PhoneApiFailure('Computer offline');
      final root = uri.queryParameters['root_path'] ?? '/research';
      if (uri.path == '/api/workspace-files') {
        return {
          'root': root,
          'entries': root == _otherRoot
              ? [_file(_otherReportPath, root: root)]
              : [_file(_reportPath), _file(_secondPath)],
        };
      }
      final file = uri.queryParameters['path']!;
      final resolvedPath = file.startsWith('/') ? file : '$root/$file';
      reads.add(file);
      if (failedReads.contains(file) || failedReads.contains(resolvedPath)) {
        throw const PhoneApiFailure('File unavailable', statusCode: 404);
      }
      final held = heldReads.remove(file);
      if (held != null) return held.future;
      return _readResult(
        resolvedPath,
        texts[resolvedPath] ?? 'Other report',
        root: root,
      );
    }
    final response = await super.request(
      base,
      path,
      method: method,
      body: body,
      token: token,
      timeout: timeout,
    );
    // Match the real phone API: canonical user messages keep attachment
    // metadata as well as their complete text after acknowledgement.
    if (path == '/api/phone/requests' && method == 'POST') {
      final thread = '${response['thread_id']}';
      final messages = (states[thread]?['thread'] as Map?)?['messages'];
      if (messages is List && messages.isNotEmpty) {
        (messages.first as Map)['attachments'] = body?['attachments'] ?? [];
      }
      final held = heldPostResponse;
      heldPostResponse = null;
      if (held != null) await held.future;
    }
    return response;
  }
}

class _FailOutboundStore extends MemoryPhoneStatePersistence {
  _FailOutboundStore(super.value);
  bool failNextOutbound = false;

  @override
  Future<void> save(Map<String, Object?> state) async {
    if (failNextOutbound && (state['deliveries'] as List).isNotEmpty) {
      failNextOutbound = false;
      throw StateError('Device storage failed');
    }
    await super.save(state);
  }
}

PhoneConversationView _conversation(WidgetTester tester) =>
    tester.widget<PhoneConversationView>(find.byType(PhoneConversationView));

Future<void> _openOutput(WidgetTester tester) async {
  await tester.tap(find.text('Library'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Outputs'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Quarterly report'));
  await tester.pumpAndSettle();
}

Future<void> _askAboutOutput(WidgetTester tester) async {
  await _openOutput(tester);
  expect(_ask, findsOneWidget);
  await tester.tap(_ask);
  await tester.pumpAndSettle();
  expect(handoff.composer, findsOneWidget);
  expect(_reference, findsOneWidget);
}

Future<void> _selectSession(WidgetTester tester, String thread) async {
  await tester.tap(find.text('Work'));
  await tester.pumpAndSettle();
  tester
      .widget<PhoneWorkOverview>(find.byType(PhoneWorkOverview))
      .onSelectSession(handoff.session(thread));
  await tester.pumpAndSettle();
}

Map<String, Object?> _sentBody(_ReportApi api) =>
    api.calls.singleWhere((call) => call.method == 'POST').body!;

void _expectReportPayload(Map<String, Object?> body, String question) {
  expect(body['workflow_id'], handoff.workflow);
  expect(body['thread_id'], 'a');
  expect(body['text'], contains(question));
  expect(body['text'], contains(_reportPath));
  expect(body['text'], contains('Revenue rose from 40 to 52 units.'));
  expect(jsonEncode(body), contains('Quarterly report'));
}

void main() {
  testWidgets(
    'output Ask opens chat with a report reference and preserves the unsent question',
    (tester) async {
      final api = _ReportApi();
      final store = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, api, store);
      await _askAboutOutput(tester);
      expect(handoff.composerText(tester), 'Original draft');
      expect(api.postCount, 0);
      expect(
        find.descendant(
          of: _reference,
          matching: find.text('Report: Quarterly report'),
        ),
        findsOneWidget,
      );
      final field = tester.widget<TextField>(handoff.composer);
      expect(field.focusNode?.hasFocus, isTrue);

      await tester.enterText(handoff.composer, 'Why did revenue increase?');
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      _expectReportPayload(_sentBody(api), 'Why did revenue increase?');
      expect(_sentBody(api)['action'], 'send');
      expect(_reference, findsNothing);
      expect(handoff.composerText(tester), isEmpty);
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );

  testWidgets(
    'file preview Ask attaches that file and remove keeps the question',
    (tester) async {
      final api = _ReportApi();
      final store = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, api, store);
      await tester.tap(find.text('Library'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('quarterly.md'));
      await tester.pumpAndSettle();
      await tester.tap(_ask);
      await tester.pumpAndSettle();
      expect(_reference, findsOneWidget);
      expect(handoff.composerText(tester), 'Original draft');
      expect(api.postCount, 0);
      await tester.tap(_removeReference);
      await tester.pumpAndSettle();
      expect(_reference, findsNothing);
      expect(handoff.composerText(tester), 'Original draft');
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      expect(_sentBody(api)['text'], 'Original draft');
      expect(jsonEncode(_sentBody(api)), isNot(contains(_reportPath)));
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );

  for (final action in ['steer', 'queue']) {
    testWidgets('$action carries the cited report to the fixed active run', (
      tester,
    ) async {
      final api = _ReportApi(running: true);
      final store = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, api, store);
      await _askAboutOutput(tester);
      await tester.enterText(
        handoff.composer,
        'Explain the change in this report.',
      );
      expect(api.postCount, 0);
      if (action == 'steer') {
        await tester.tap(find.byTooltip('Steer current task'));
      } else {
        await tester.tap(find.byTooltip('Message options'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Send after this task'));
      }
      await tester.pumpAndSettle();
      final body = _sentBody(api);
      _expectReportPayload(body, 'Explain the change in this report.');
      expect(body['action'], action);
      expect(body['run_id'], 'run-a');
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    });
  }

  testWidgets(
    'report references stay with their chat and survive an offline restart',
    (tester) async {
      final api = _ReportApi();
      final store = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, api, store);
      await _askAboutOutput(tester);
      await _selectSession(tester, 'b');
      expect(handoff.composerText(tester), 'Other draft');
      expect(_reference, findsNothing);
      await _selectSession(tester, 'a');
      expect(handoff.composerText(tester), 'Original draft');
      expect(_reference, findsOneWidget);
      expect(api.postCount, 0);
      await handoff.unmount(tester);

      api.offline = true;
      await handoff.mount(tester, api, store);
      expect(handoff.composerText(tester), 'Original draft');
      expect(_reference, findsOneWidget);
      expect(api.postCount, 0);
      await tester.tap(find.byTooltip('Save message to send'));
      await tester.pumpAndSettle();
      expect(api.postCount, 0);
      final saved = PhoneDeviceState.fromJson(await store.load());
      _expectReportPayload(saved.deliveries.single.payload, 'Original draft');
      expect(_reference, findsNothing);
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );

  testWidgets('a late report read cannot replace the newly selected report', (
    tester,
  ) async {
    final api = _ReportApi();
    final store = MemoryPhoneStatePersistence(handoff.initialState());
    await handoff.mount(tester, api, store);
    final select = _conversation(tester).onSelectArtifact;
    final oldRead = Completer<Map<String, dynamic>>();
    api.heldReads[_reportPath] = oldRead;
    select(_report);
    await tester.pump();
    select(_secondReport);
    await tester.pumpAndSettle();
    oldRead.complete(_readResult(_reportPath, 'Stale first report text.'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Stale first report text.'), findsNothing);
    await tester.tap(_ask);
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Send message'));
    await tester.pumpAndSettle();
    final body = _sentBody(api);
    expect(body['text'], contains(_secondPath));
    expect(body['text'], contains('The corrected total is 49 units.'));
    expect(body['text'], isNot(contains('Stale first report text.')));
    expect(jsonEncode(body), isNot(contains(_reportPath)));
    expect(tester.takeException(), isNull);
    await handoff.unmount(tester);
  });

  testWidgets(
    'a report response from another conversation cannot be cited in the new chat',
    (tester) async {
      final api = _ReportApi();
      final store = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, api, store);
      final oldRead = Completer<Map<String, dynamic>>();
      api.heldReads[_reportPath] = oldRead;
      _conversation(tester).onSelectArtifact(_report);
      await tester.pump();
      await _selectSession(tester, 'b');
      oldRead.complete(
        _readResult(_reportPath, 'Report from the old conversation.'),
      );
      await tester.pumpAndSettle();
      expect(handoff.composerText(tester), 'Other draft');
      expect(_reference, findsNothing);
      await tester.tap(find.text('Library'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('Report from the old conversation.'),
        findsNothing,
      );
      if (_ask.evaluate().isNotEmpty) {
        await tester.tap(_ask);
        await tester.pumpAndSettle();
        expect(_reference, findsNothing);
      }
      expect(api.postCount, 0);
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );

  testWidgets(
    'unreadable and URL-only reports cite their source without fabricated excerpts',
    (tester) async {
      final api = _ReportApi()..failedReads.add(_reportPath);
      final store = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, api, store);
      await _askAboutOutput(tester);
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      final unreadable = _sentBody(api);
      expect(unreadable['text'], contains(_reportPath));
      expect(unreadable['text'], isNot(contains(_reportText)));
      expect(unreadable['text'], isNot(contains('Loading output artifact...')));
      expect(unreadable['text'], isNot(contains('Artifact recorded at:')));
      await handoff.unmount(tester);

      final urlApi = _ReportApi();
      final urlStore = MemoryPhoneStatePersistence(handoff.initialState());
      await handoff.mount(tester, urlApi, urlStore);
      _conversation(tester).onSelectArtifact(
        const PhonePreviewArtifact(
          id: 'online-report',
          title: 'Online report',
          path: '',
          url: 'https://reports.example.test/october',
          kind: 'file',
          source: 'run',
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(_ask);
      await tester.pumpAndSettle();
      expect(urlApi.postCount, 0);
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      final urlBody = _sentBody(urlApi);
      expect(urlBody['text'], contains('https://reports.example.test/october'));
      expect(urlBody['text'], contains('Online report'));
      expect(
        urlBody['text'],
        isNot(contains('Open this URL from a browser-capable surface.')),
      );
      expect(urlApi.reads, isEmpty);
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );

  testWidgets(
    'failed outbound storage preserves the question and report for retry',
    (tester) async {
      final api = _ReportApi();
      final store = _FailOutboundStore(handoff.initialState());
      await handoff.mount(tester, api, store);
      await _askAboutOutput(tester);
      store.failNextOutbound = true;
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      expect(api.postCount, 0);
      expect(handoff.composerText(tester), 'Original draft');
      expect(_reference, findsOneWidget);
      expect(PhoneDeviceState.fromJson(await store.load()).deliveries, isEmpty);
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      _expectReportPayload(_sentBody(api), 'Original draft');
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );

  testWidgets('a delayed save clears only the submitted report reference', (
    tester,
  ) async {
    final api = _ReportApi();
    final store = handoff.DelayedStore(handoff.initialState());
    await handoff.mount(tester, api, store);
    await _askAboutOutput(tester);
    final outbound = Completer<void>();
    store.blockOutbound = outbound;
    await tester.tap(find.byTooltip('Send message'));
    await tester.pump();
    expect(store.saveStarted.isCompleted, isTrue);
    expect(api.postCount, 0);
    _conversation(tester).onSelectArtifact(_secondReport);
    await tester.pumpAndSettle();
    await tester.tap(_ask);
    // The first send is intentionally still saving and keeps its spinner alive.
    await tester.pump();
    await tester.enterText(
      handoff.composer,
      'Question about the revised report.',
    );
    outbound.complete();
    await tester.pumpAndSettle();
    _expectReportPayload(_sentBody(api), 'Original draft');
    expect(handoff.composerText(tester), 'Question about the revised report.');
    expect(_reference, findsOneWidget);
    expect(
      find.descendant(
        of: _reference,
        matching: find.text('Report: Revised report'),
      ),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
    await handoff.unmount(tester);
  });

  testWidgets(
    'relative output paths use the resolved server path or captured workspace fallback',
    (tester) async {
      for (final unavailable in [false, true]) {
        final api = _ReportApi();
        if (unavailable) api.failedReads.add(_reportPath);
        final store = MemoryPhoneStatePersistence(handoff.initialState());
        await handoff.mount(tester, api, store);
        _conversation(tester).onSelectArtifact(
          const PhonePreviewArtifact(
            id: 'relative-report',
            title: 'Quarterly report',
            path: 'reports/quarterly.md',
            url: '',
            kind: 'file',
            source: 'run',
          ),
        );
        await tester.pumpAndSettle();
        expect(api.reads, contains('reports/quarterly.md'));
        await tester.tap(_ask);
        await tester.pumpAndSettle();
        await tester.tap(find.byTooltip('Send message'));
        await tester.pumpAndSettle();
        final body = _sentBody(api);
        final attachment = (body['attachments'] as List).single as Map;
        expect(attachment['path'], _reportPath);
        expect(body['text'], contains(_reportPath));
        if (unavailable) {
          expect(body['text'], isNot(contains('Revenue rose')));
          expect(
            (attachment['report_reference'] as Map)['content_available'],
            isFalse,
          );
        } else {
          expect(body['text'], contains('Revenue rose from 40 to 52 units.'));
        }
        expect(tester.takeException(), isNull);
        await handoff.unmount(tester);
      }
    },
  );

  testWidgets(
    'a first-send receipt cannot adopt another workspace draft or report',
    (tester) async {
      const newWorkflow = '_scratch';
      final firstDraftKey = PhoneDeviceState.draftKey(
        handoff.host,
        newWorkflow,
        '',
        '/research',
      );
      final otherDraftKey = PhoneDeviceState.draftKey(
        handoff.host,
        newWorkflow,
        '',
        _otherRoot,
      );
      final api = _ReportApi();
      final store = MemoryPhoneStatePersistence({
        'selections': {
          handoff.host: {
            'workflow_id': newWorkflow,
            'thread_id': '',
            'workspace_root': '/research',
            'messages': [],
          },
        },
        'drafts': {
          firstDraftKey: 'Question for the first workspace.',
          otherDraftKey: 'Existing question in the other workspace.',
        },
        'deliveries': [],
      });
      await handoff.mount(tester, api, store);
      _conversation(tester).onSelectArtifact(_report);
      await tester.pumpAndSettle();
      await tester.tap(_ask);
      await tester.pumpAndSettle();
      final receipt = Completer<void>();
      api.heldPostResponse = receipt;
      await tester.tap(find.byTooltip('Send message'));
      await tester.pumpAndSettle();
      expect(api.postCount, 1);
      expect(_sentBody(api)['thread_id'], '');
      expect(_sentBody(api)['workspace_root'], '/research');
      expect(_sentBody(api)['text'], contains(_reportPath));

      // Browse a different workspace while the first request is on the wire.
      await tester.tap(find.text('Library'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Files'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('research'));
      await tester.pumpAndSettle();
      final rootField = find.byWidgetPredicate(
        (widget) =>
            widget is TextField &&
            widget.decoration?.labelText == 'Project folder',
      );
      await tester.enterText(rootField, _otherRoot);
      await tester.tap(find.byTooltip('Use this folder'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('other.md'));
      await tester.tap(find.text('other.md'));
      await tester.pumpAndSettle();
      await tester.tap(_ask);
      await tester.pumpAndSettle();
      expect(
        handoff.composerText(tester),
        'Existing question in the other workspace.',
      );
      await tester.enterText(
        handoff.composer,
        'Keep this question with workspace B.',
      );
      await tester.pump(const Duration(milliseconds: 300));

      receipt.complete();
      await tester.pumpAndSettle();
      expect(api.postCount, 1);
      expect(_conversation(tester).conversationId, isEmpty);
      expect(_conversation(tester).workspaceRoot, _otherRoot);
      expect(_conversation(tester).busy, isFalse);
      expect(_conversation(tester).reportReference?.path, _otherReportPath);
      expect(
        handoff.composerText(tester),
        'Keep this question with workspace B.',
      );
      final saved = PhoneDeviceState.fromJson(await store.load());
      expect(saved.selections[handoff.host]!['thread_id'], '');
      expect(saved.selections[handoff.host]!['workspace_root'], _otherRoot);
      expect(
        saved.drafts[otherDraftKey],
        'Keep this question with workspace B.',
      );
      expect(saved.reportDrafts[otherDraftKey]?.path, _otherReportPath);
      expect(saved.reportDrafts.containsKey(firstDraftKey), isFalse);
      expect(saved.deliveries.single.status, 'accepted');
      expect(saved.deliveries.single.threadId, 'created');
      expect(tester.takeException(), isNull);
      await handoff.unmount(tester);
    },
  );
}
