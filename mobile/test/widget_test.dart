import 'package:diane_phone/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('shows the phone workspace pages', (WidgetTester tester) async {
    await tester.pumpWidget(const DanPhoneApp());

    expect(find.text('Work'), findsOneWidget);
    expect(find.text('Notes'), findsOneWidget);
    expect(find.text('Work Chat'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.menu));
    await tester.pumpAndSettle();
    expect(find.text('Search sessions'), findsOneWidget);
    expect(find.byIcon(Icons.create_new_folder_outlined), findsOneWidget);
    expect(find.byIcon(Icons.refresh), findsOneWidget);
    await tester.tapAt(const Offset(520, 80));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.settings_outlined));
    await tester.pumpAndSettle();
    expect(find.text('Dear Diane API base'), findsOneWidget);
    expect(find.text('Access token'), findsOneWidget);
    expect(find.text('Workspace root'), findsOneWidget);
    expect(find.textContaining('WG:'), findsOneWidget);
    expect(find.text('Force start'), findsOneWidget);
    expect(find.text('Force off'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Notes'));
    await tester.pumpAndSettle();
    expect(find.text('Pages'), findsWidgets);

    await tester.tap(find.text('Edit'));
    await tester.pumpAndSettle();
    expect(find.text('Markdown source'), findsWidgets);
    expect(find.text('Save'), findsOneWidget);
  });
}
