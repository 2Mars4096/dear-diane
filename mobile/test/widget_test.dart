import 'package:diane_phone/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _OfflineApi implements PhoneApiClient {
  @override
  Future<Map<String, dynamic>> request(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    String token = '',
    Duration timeout = const Duration(seconds: 15),
  }) async => throw const PhoneApiFailure('The computer could not be reached.');
}

void main() {
  testWidgets('phone opens to Diane with Work, Library and secondary setup', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      DanPhoneApp(
        apiClient: _OfflineApi(),
        persistence: MemoryPhoneStatePersistence(),
        initialApiBase: 'https://computer.test',
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('phone-composer')), findsOneWidget);
    expect(find.text('Work'), findsOneWidget);
    expect(find.text('Library'), findsOneWidget);
    expect(find.text('Computer address'), findsNothing);

    await tester.tap(find.byTooltip('Conversations'));
    await tester.pumpAndSettle();
    expect(find.text('Search conversations'), findsOneWidget);
    expect(find.byTooltip('New workspace'), findsOneWidget);
    expect(find.byTooltip('Refresh sessions'), findsOneWidget);
    await tester.tapAt(const Offset(750, 80));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Computer connection'));
    await tester.pumpAndSettle();
    expect(find.text('Your computer'), findsOneWidget);
    expect(find.text('Reconnect'), findsOneWidget);
    expect(find.text('Computer address'), findsNothing);
    await tester.tap(find.text('Connection details'));
    await tester.pumpAndSettle();
    expect(find.text('Computer address'), findsOneWidget);
    expect(find.text('Access token'), findsOneWidget);
    expect(find.text('Private network'), findsOneWidget);
    await tester.ensureVisible(find.text('Done'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Done'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Work'));
    await tester.pumpAndSettle();
    expect(find.text('A place to pick things up'), findsOneWidget);

    await tester.tap(find.text('Library'));
    await tester.pumpAndSettle();
    expect(find.text('Files'), findsOneWidget);
    expect(find.text('Outputs'), findsOneWidget);

    await tester.tap(find.text('Notes'));
    await tester.pumpAndSettle();
    expect(find.text('Pages'), findsWidgets);

    await tester.tap(find.text('Edit'));
    await tester.pumpAndSettle();
    expect(find.text('Markdown source'), findsWidgets);
    expect(find.text('Save'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
  });
}
