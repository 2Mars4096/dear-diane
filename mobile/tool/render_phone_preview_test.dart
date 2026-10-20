// Optional visual inspection harness for the actual phone widgets.
//
// flutter test tool/render_phone_preview_test.dart \
//   --dart-define=PHONE_PREVIEW_DIR=/tmp/diane-phone-ui \
//   --dart-define=PHONE_FONT_DIR=<flutter-sdk>/bin/cache/artifacts/material_fonts
// Optional PHONE_SERIF_FONT / PHONE_MONO_FONT select local platform fonts.
//
// All content is fictional. No backend or account is contacted.
import 'dart:io';
import 'dart:ui' as ui;

import 'package:diane_phone/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _output = String.fromEnvironment('PHONE_PREVIEW_DIR');
const _fontDirectory = String.fromEnvironment('PHONE_FONT_DIR');
const _serifFont = String.fromEnvironment('PHONE_SERIF_FONT');
const _monoFont = String.fromEnvironment('PHONE_MONO_FONT');

const _sessions = [
  PhoneSessionSummary(
    id: 'phone-review',
    workflowId: 'diane',
    title: 'Phone app improvements',
    messageCount: 12,
    updatedAt: '2026-10-20T12:40:00Z',
    archived: false,
    mode: 'agent',
  ),
  PhoneSessionSummary(
    id: 'project-brief',
    workflowId: 'diane',
    title: 'Prepare the project brief',
    messageCount: 8,
    updatedAt: '2026-10-20T12:20:00Z',
    archived: false,
    mode: 'agent',
  ),
  PhoneSessionSummary(
    id: 'research-notes',
    workflowId: 'diane',
    title: 'Organize the research notes',
    messageCount: 6,
    updatedAt: '2026-10-20T11:00:00Z',
    archived: false,
    mode: 'agent',
  ),
];

const _tasks = {
  'phone-review': PhoneTaskSummary(
    taskId: 't1',
    threadId: 'phone-review',
    status: 'needs_input',
    latestProgress:
        'The updated screens are ready. One choice needs your input.',
    workspaceRoot: '/work/diane',
    workspaceId: 'diane',
    activeRunId: 'r1',
    title: 'Phone app improvements',
  ),
  'project-brief': PhoneTaskSummary(
    taskId: 't2',
    threadId: 'project-brief',
    status: 'running',
    latestProgress: 'Comparing the source notes and drafting the next steps.',
    workspaceRoot: '/work/diane',
    workspaceId: 'diane',
    activeRunId: 'r2',
    title: 'Prepare the project brief',
  ),
  'research-notes': PhoneTaskSummary(
    taskId: 't3',
    threadId: 'research-notes',
    status: 'completed',
    latestProgress: 'The reading notes and references are ready to review.',
    workspaceRoot: '/work/diane',
    workspaceId: 'diane',
    activeRunId: 'r3',
    title: 'Organize the research notes',
  ),
};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    if (_output.isEmpty) return;
    if (_fontDirectory.isEmpty) {
      throw StateError(
        'Pass PHONE_FONT_DIR to render with the real app fonts.',
      );
    }
    final text = FontLoader('Roboto');
    for (final file in [
      'Roboto-Regular.ttf',
      'Roboto-Medium.ttf',
      'Roboto-Bold.ttf',
    ]) {
      text.addFont(
        File('$_fontDirectory/$file').readAsBytes().then(ByteData.sublistView),
      );
    }
    await text.load();
    final icons = FontLoader('MaterialIcons')
      ..addFont(
        File(
          '$_fontDirectory/MaterialIcons-Regular.otf',
        ).readAsBytes().then(ByteData.sublistView),
      );
    await icons.load();
    // The test engine substitutes Ahem for platform families. Give code and
    // the small Diane initial real glyphs as well as the themed body text.
    for (final entry in {'serif': _serifFont, 'monospace': _monoFont}.entries) {
      final path = entry.value.isEmpty
          ? '$_fontDirectory/Roboto-Regular.ttf'
          : entry.value;
      final loader = FontLoader(entry.key)
        ..addFont(File(path).readAsBytes().then(ByteData.sublistView));
      await loader.load();
    }
  });

  for (final scene in ['conversation', 'work', 'offline', 'decision', 'dark']) {
    testWidgets('render $scene phone preview', (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final captureKey = GlobalKey();
      final controller = TextEditingController();
      addTearDown(controller.dispose);
      final offline = scene == 'offline';
      final decision = scene == 'decision';
      final work = scene == 'work';
      final theme = buildPhoneTheme(
        scene == 'dark' ? Brightness.dark : Brightness.light,
      );
      final body = work
          ? PhoneWorkOverview(
              groups: const [
                PhoneWorkspaceSessionGroup(
                  root: '/work/diane',
                  name: 'Dear Diane',
                  sessions: _sessions,
                  archived: false,
                ),
              ],
              tasksByThread: _tasks,
              loading: false,
              onRefresh: () async {},
              onSelectSession: (_) {},
              onOpenDiane: () {},
            )
          : PhoneConversationView(
              workspaceRoot: '/work/diane',
              controller: controller,
              status: offline ? 'Delivery unconfirmed' : 'Ready',
              busy: false,
              activeRunLive: false,
              runMode: WorkRunMode.auto,
              attachments: const [],
              previewArtifacts: const [],
              previewArtifactsExpanded: false,
              loadingPromptLog: false,
              onRunModeChanged: (_) {},
              onPasteScreenshot: () {},
              onRemoveAttachment: (_) {},
              onStopRun: () {},
              onOpenPromptLog: () {},
              onTogglePreviewArtifacts: () {},
              onSelectArtifact: (_) {},
              onSend: () {},
              onRetryDelivery: (_) {},
              onReplyApproval: (_, _) async {},
              connectionState: offline ? 'offline' : 'ready',
              messages: decision
                  ? const [
                      PhoneChatMessage(
                        id: 'question-context',
                        role: PhoneChatRole.assistant,
                        text:
                            'The changes are ready. I need your permission to run the project checks.',
                      ),
                    ]
                  : const [
                      PhoneChatMessage(
                        id: 'm1',
                        role: PhoneChatRole.user,
                        text: 'Can you help me pick up where we left off?',
                      ),
                      PhoneChatMessage(
                        id: 'm2',
                        role: PhoneChatRole.assistant,
                        text:
                            'The phone app review is ready for you. The project brief is still running on your computer.\n\nYou can review what needs your attention in Work, or keep talking with me here.',
                      ),
                      PhoneChatMessage(
                        id: 'm3',
                        role: PhoneChatRole.user,
                        text: 'Let’s focus on the phone app first.',
                      ),
                      PhoneChatMessage(
                        id: 'm4',
                        role: PhoneChatRole.assistant,
                        text:
                            'Of course. The conversation will stay here while we work through the changes.',
                      ),
                    ],
              pendingApprovals: decision
                  ? const [
                      PhoneApprovalRequest(
                        id: 'approval-1',
                        runId: 'r1',
                        method: 'item/commandExecution/requestApproval',
                        params: {
                          'command': 'flutter test',
                          'cwd': '/work/diane/mobile',
                          'reason': 'Run the phone regression checks.',
                        },
                      ),
                    ]
                  : const [],
              deliveries: offline
                  ? [
                      PhoneDeliveryItem(
                        id: 'outbox-1',
                        kind: 'send',
                        text: 'Please include the reconnect check.',
                        apiBase: 'https://computer.example',
                        status: 'uncertain',
                        error:
                            'No acknowledgement yet. Check the connection and retry.',
                        createdAt: DateTime.utc(2026, 10, 20, 12, 45),
                      ),
                    ]
                  : const [],
            );
      await tester.pumpWidget(
        RepaintBoundary(
          key: captureKey,
          child: MaterialApp(
            debugShowCheckedModeBanner: false,
            theme: theme.copyWith(
              textTheme: theme.textTheme.apply(fontFamily: 'Roboto'),
              primaryTextTheme: theme.primaryTextTheme.apply(
                fontFamily: 'Roboto',
              ),
            ),
            home: PhoneShell(
              scaffoldKey: GlobalKey<ScaffoldState>(),
              drawer: const Drawer(child: SizedBox()),
              selectedIndex: work ? 1 : 0,
              onDestinationSelected: (_) {},
              body: body,
              connectionState: offline ? 'offline' : 'ready',
              computerLabel: 'My computer',
              onConfigureBackend: () {},
              onReconnect: () {},
              lastSyncedAt: DateTime.utc(2026, 10, 20, 12, 40),
              attentionCount: 1,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.runAsync(() async {
        final boundary =
            captureKey.currentContext!.findRenderObject()!
                as RenderRepaintBoundary;
        final picture = await boundary.toImage(pixelRatio: 2);
        try {
          final bytes = await picture.toByteData(
            format: ui.ImageByteFormat.png,
          );
          final file = File('$_output/phone-$scene.png');
          await file.parent.create(recursive: true);
          await file.writeAsBytes(bytes!.buffer.asUint8List());
        } finally {
          picture.dispose();
        }
      });
      await tester.pumpWidget(const SizedBox.shrink());
    }, skip: _output.isEmpty);
  }
}
