import 'package:diane_phone/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

PhoneSessionSummary session(String id, String title) => PhoneSessionSummary(
  id: id,
  workflowId: 'project',
  title: title,
  messageCount: 2,
  updatedAt: '2026-10-20T09:00:00Z',
  archived: false,
  mode: 'agent',
);

PhoneTaskSummary task(String id, String status) => PhoneTaskSummary(
  taskId: 'task-$id',
  threadId: id,
  status: status,
  latestProgress: 'A concrete update with a document ready to inspect.',
  workspaceRoot: '/research',
  workspaceId: 'project',
  activeRunId: 'run-$id',
  title: '',
);

Widget framed(
  Widget child, {
  double scale = 1,
  Brightness brightness = Brightness.light,
}) => MaterialApp(
  theme: buildPhoneTheme(brightness),
  builder: (context, body) => MediaQuery(
    data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(scale)),
    child: body!,
  ),
  home: child,
);

void smallPhone(WidgetTester tester, {double height = 780}) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = Size(360, height);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetViewInsets);
}

PhoneConversationView conversation(
  TextEditingController controller, {
  List<PhoneChatMessage> messages = const [],
  bool active = false,
  VoidCallback? onSend,
  VoidCallback? onStop,
  String id = 'primary',
  String status = 'Ready',
}) => PhoneConversationView(
  workspaceRoot: '/research',
  conversationId: id,
  messages: messages,
  controller: controller,
  status: status,
  busy: false,
  activeRunLive: active,
  runMode: WorkRunMode.auto,
  attachments: const [],
  previewArtifacts: const [],
  previewArtifactsExpanded: false,
  loadingPromptLog: false,
  onRunModeChanged: (_) {},
  onPasteScreenshot: () {},
  onRemoveAttachment: (_) {},
  onStopRun: onStop ?? () {},
  onOpenPromptLog: () {},
  onTogglePreviewArtifacts: () {},
  onSelectArtifact: (_) {},
  onSend: onSend ?? () {},
);

void main() {
  test(
    'work categories distinguish attention, execution and saved history',
    () {
      expect(phoneWorkGroup(task('a', 'needs_input')), 'Needs you');
      expect(phoneWorkGroup(task('a', 'paused')), 'Needs you');
      expect(phoneWorkGroup(task('a', 'failed')), 'Needs you');
      expect(phoneWorkGroup(task('a', 'running')), 'Working');
      expect(phoneWorkGroup(task('a', 'waiting_dependency')), 'Working');
      expect(phoneWorkGroup(task('a', 'completed')), 'Recent');
      expect(phoneWorkGroup(null), 'Recent');
    },
  );

  testWidgets(
    'work puts actual attention before running and finished conversations',
    (tester) async {
      smallPhone(tester);
      String? selected;
      await tester.pumpWidget(
        framed(
          Scaffold(
            body: PhoneWorkOverview(
              groups: [
                PhoneWorkspaceSessionGroup(
                  root: '/research',
                  name: 'Research',
                  archived: false,
                  sessions: [
                    session('finished', 'Literature draft'),
                    session('working', 'Source check'),
                    session('attention', 'Choose a chart'),
                  ],
                ),
              ],
              tasksByThread: {
                'finished': task('finished', 'completed'),
                'working': task('working', 'running'),
                'attention': task('attention', 'needs_input'),
              },
              loading: false,
              onRefresh: () async {},
              onSelectSession: (value) => selected = value.id,
            ),
          ),
          scale: 1.6,
        ),
      );
      await tester.pump();
      expect(
        tester.getTopLeft(find.text('Needs you')).dy,
        lessThan(tester.getTopLeft(find.text('Working').first).dy),
      );
      await tester.tap(find.text('Choose a chart'));
      expect(selected, 'attention');
      expect(tester.takeException(), isNull);
      expect(find.text('task-attention'), findsNothing);
    },
  );

  testWidgets(
    'shell remains readable at 360px and 2x text with clear offline recovery',
    (tester) async {
      smallPhone(tester);
      var retried = false;
      var destination = -1;
      await tester.pumpWidget(
        framed(
          PhoneShell(
            scaffoldKey: GlobalKey<ScaffoldState>(),
            drawer: const Drawer(child: Text('Conversations')),
            selectedIndex: 0,
            onDestinationSelected: (index) => destination = index,
            body: const Center(child: Text('Your conversation')),
            connectionState: 'offline',
            computerLabel: 'Research computer',
            onConfigureBackend: () {},
            onReconnect: () => retried = true,
          ),
          scale: 2,
          brightness: Brightness.dark,
        ),
      );
      await tester.pump();
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Retry'));
      expect(retried, isTrue);
      await tester.tap(find.text('Work'));
      expect(destination, 1);
      expect(find.textContaining('Offline'), findsOneWidget);
    },
  );

  testWidgets(
    'keyboard and large type leave an actionable context-sensitive composer',
    (tester) async {
      smallPhone(tester, height: 640);
      tester.view.viewInsets = const FakeViewPadding(bottom: 270);
      final controller = TextEditingController();
      addTearDown(controller.dispose);
      var sends = 0, stops = 0;
      await tester.pumpWidget(
        framed(
          PhoneShell(
            scaffoldKey: GlobalKey<ScaffoldState>(),
            drawer: const Drawer(),
            selectedIndex: 0,
            onDestinationSelected: (_) {},
            body: conversation(
              controller,
              active: true,
              status: 'Could not save this action. Nothing was sent.',
              onSend: () => sends++,
              onStop: () => stops++,
            ),
            connectionState: 'ready',
            computerLabel: 'Your computer',
            onConfigureBackend: () {},
          ),
          scale: 2,
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(
        find.text('Could not save this action. Nothing was sent.'),
        findsOneWidget,
      );
      await tester.tap(find.byTooltip('Stop current task'));
      expect(stops, 1);
      await tester.enterText(
        find.byKey(const ValueKey('phone-composer')),
        'Use the revised chart.',
      );
      await tester.pump();
      await tester.tap(find.byTooltip('Steer current task'));
      expect(sends, 1);
      expect(tester.takeException(), isNull);
      expect(find.text('Auto'), findsNothing);
      expect(find.text('Review'), findsNothing);
    },
  );

  testWidgets('uncertain delivery offers recovery without claiming success', (
    tester,
  ) async {
    smallPhone(tester);
    var retries = 0;
    await tester.pumpWidget(
      framed(
        Scaffold(
          body: ListView(
            children: [
              PhoneDeliveryNotice(
                item: PhoneDeliveryItem(
                  id: 'receipt',
                  kind: 'send',
                  text: 'Prepare the seminar notes.',
                  apiBase: 'https://computer.test',
                  createdAt: DateTime(2026),
                  status: 'uncertain',
                ),
                onRetry: () => retries++,
              ),
            ],
          ),
        ),
        scale: 1.6,
      ),
    );
    expect(find.text('Delivery unconfirmed'), findsOneWidget);
    expect(find.text('Accepted by computer'), findsNothing);
    await tester.tap(find.text('Check and retry'));
    expect(retries, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets('offline drafting remains usable with keyboard and 2x text', (
    tester,
  ) async {
    smallPhone(tester, height: 640);
    tester.view.viewInsets = const FakeViewPadding(bottom: 270);
    final controller = TextEditingController(text: 'Pick this up tomorrow.');
    addTearDown(controller.dispose);
    var retried = false;
    await tester.pumpWidget(
      framed(
        PhoneShell(
          scaffoldKey: GlobalKey<ScaffoldState>(),
          drawer: const Drawer(),
          selectedIndex: 0,
          onDestinationSelected: (_) {},
          body: conversation(controller),
          connectionState: 'offline',
          computerLabel: 'Research computer with a longer name',
          onConfigureBackend: () {},
          onReconnect: () => retried = true,
          lastSyncedAt: DateTime(2026, 10, 20, 9),
        ),
        scale: 2,
        brightness: Brightness.dark,
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('Keep drafting'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('phone-composer')).hitTestable(),
      findsOneWidget,
    );
    await tester.tap(find.text('Retry'));
    expect(retried, isTrue);
  });

  testWidgets(
    'question requires an explicit choice and sends the chosen answer',
    (tester) async {
      smallPhone(tester);
      Map<String, Object?>? reply;
      const request = PhoneApprovalRequest(
        id: 'choice',
        runId: 'run',
        method: 'item/tool/requestUserInput',
        params: {
          'questions': [
            {
              'id': 'chart',
              'question': 'Which chart should I include?',
              'options': [
                {
                  'label': 'Inventory response',
                  'description': 'Shows changes in stock levels.',
                },
                {
                  'label': 'Credit conditions',
                  'description': 'Shows the financing channel.',
                },
              ],
            },
          ],
        },
      );
      await tester.pumpWidget(
        framed(
          Scaffold(
            body: ListView(
              children: [
                PhoneApprovalCard(
                  request: request,
                  onReply: (_, body) async => reply = body,
                ),
              ],
            ),
          ),
          scale: 1.6,
        ),
      );
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Send answer'),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.text('Inventory response'));
      await tester.pump();
      await tester.ensureVisible(find.text('Send answer'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Send answer'));
      await tester.pump();
      expect(reply, {
        'decision': 'accept',
        'answers': {'chart': 'Inventory response'},
      });
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('expired approval cannot be accepted from a stale card', (
    tester,
  ) async {
    smallPhone(tester);
    var replies = 0;
    await tester.pumpWidget(
      framed(
        Scaffold(
          body: ListView(
            children: [
              PhoneApprovalCard(
                request: const PhoneApprovalRequest(
                  id: 'expired',
                  runId: 'run',
                  method: 'commandExecution/requestApproval',
                  status: 'expired',
                  params: {'command': 'python make_chart.py'},
                ),
                onReply: (_, _) async => replies++,
              ),
            ],
          ),
        ),
      ),
    );
    expect(find.text('Allow once'), findsNothing);
    expect(find.textContaining('expired'), findsOneWidget);
    expect(find.text('python make_chart.py'), findsOneWidget);
    expect(replies, 0);
  });

  testWidgets(
    'conversation follows latest but preserves older reading until jump requested',
    (tester) async {
      smallPhone(tester);
      final controller = TextEditingController();
      final messages = ValueNotifier<List<PhoneChatMessage>>(
        List.generate(
          32,
          (index) => PhoneChatMessage(
            id: '$index',
            role: index.isEven ? PhoneChatRole.user : PhoneChatRole.assistant,
            text:
                'Message $index. A useful result with enough detail to read over several lines.',
          ),
        ),
      );
      addTearDown(controller.dispose);
      addTearDown(messages.dispose);
      await tester.pumpWidget(
        framed(
          Scaffold(
            body: ValueListenableBuilder<List<PhoneChatMessage>>(
              valueListenable: messages,
              builder: (_, value, _) =>
                  conversation(controller, messages: value),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      final list = tester.widget<ListView>(find.byType(ListView).first);
      final scroll = list.controller!;
      expect(scroll.offset, greaterThan(0));
      expect(scroll.position.extentAfter, lessThan(88));
      scroll.jumpTo(0);
      await tester.pump();
      messages.value = [
        ...messages.value,
        const PhoneChatMessage(
          id: 'new',
          role: PhoneChatRole.assistant,
          text: 'A new result is ready.',
        ),
      ];
      await tester.pump();
      expect(scroll.offset, 0);
      expect(find.text('New updates'), findsOneWidget);
      await tester.tap(find.text('New updates'));
      await tester.pumpAndSettle();
      expect(scroll.position.extentAfter, lessThan(88));
      expect(find.text('New updates'), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );
}
