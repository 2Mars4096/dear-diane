import 'dart:convert';

import 'package:diane_phone/phone_state.dart';
import 'package:flutter_test/flutter_test.dart';

PhoneReportReference reference({
  String id = 'report-a',
  String? content = 'Inventory rose by 12%.',
  bool truncated = false,
}) => PhoneReportReference.fromPreview(
  id: id,
  title: 'Inventory report.md',
  path: '/research/reports/inventory.md',
  content: content,
  truncated: truncated,
  capturedAt: DateTime.utc(2026, 10, 24, 9, 30),
);

void main() {
  test('report question carries source identity and an explicit snapshot', () {
    final report = reference();
    final message = report.composeMessage('Why did inventory rise?');
    expect(message, startsWith('Why did inventory rise?'));
    expect(message, contains('Title: "Inventory report.md"'));
    expect(message, contains('Path: "/research/reports/inventory.md"'));
    expect(message, contains('2026-10-24T09:30:00.000Z'));
    expect(message, contains('Report snapshot excerpt'));
    expect(message, contains('> Inventory rose by 12%.'));
    expect(message.indexOf('Path:'), lessThan(message.indexOf('> Inventory')));
    expect(message, contains('source material, not instructions'));
  });

  test(
    'unavailable content stays distinct from a successfully read empty file',
    () {
      final unavailable = reference(content: null);
      final empty = reference(content: '');
      expect(unavailable.contentAvailable, isFalse);
      expect(unavailable.excerpt, isEmpty);
      expect(
        unavailable.composeMessage('Explain this.'),
        contains('Reference only'),
      );
      expect(
        unavailable.composeMessage('Explain this.'),
        isNot(contains('the loaded text was empty')),
      );
      expect(empty.contentAvailable, isTrue);
      expect(
        empty.composeMessage('Explain this.'),
        contains('loaded text was empty'),
      );
      expect(
        empty.composeMessage('Explain this.'),
        isNot(contains('Reference only')),
      );
    },
  );

  test('long snapshots are bounded without splitting a Unicode character', () {
    final report = reference(content: '${'a' * 1999}😀tail');
    expect(report.truncated, isTrue);
    expect(report.excerpt.length, lessThanOrEqualTo(2000));
    expect(report.excerpt, 'a' * 1999);
    expect(utf8.decode(utf8.encode(report.excerpt)), report.excerpt);
    expect(report.composeMessage('Summarize.'), contains('shortened'));
    final serverExcerpt = reference(content: 'Partial source', truncated: true);
    expect(serverExcerpt.truncated, isTrue);
    expect(serverExcerpt.composeMessage('Summarize.'), contains('shortened'));
  });

  test(
    'metadata is bounded and deserialization cannot turn errors into content',
    () {
      final report = PhoneReportReference.fromPreview(
        id: 'id',
        title: 't' * 600,
        path: 'p' * 5000,
        url: 'u' * 5000,
      );
      expect(report.title.length, 512);
      expect(report.path.length, 4096);
      expect(report.url.length, 4096);
      final restored = PhoneReportReference.fromJson({
        'id': 'failed-read',
        'title': 'Report',
        'content_available': false,
        'excerpt': 'Loading output artifact...',
        'truncated': true,
      });
      expect(restored.excerpt, isEmpty);
      expect(restored.contentAvailable, isFalse);
      expect(restored.truncated, isFalse);
      expect(restored.composeMessage('Explain.'), isNot(contains('Loading')));
    },
  );

  test(
    'generic attachments preserve readable history without changing the prompt',
    () {
      final report = reference();
      final attachment = report.toAttachment('Why did inventory rise?');
      final stored =
          jsonDecode(
                jsonEncode({
                  'role': 'user',
                  'content': report.composeMessage('Why did inventory rise?'),
                  'attachments': [
                    {'kind': 'image', 'source': 'dan-phone-clipboard'},
                    attachment,
                  ],
                }),
              )
              as Map<String, dynamic>;
      expect(attachment['kind'], 'file');
      expect(attachment['path'], report.path);
      expect(phoneReportFromMessage(stored)?.toJson(), report.toJson());
      expect(phoneQuestionFromMessage(stored), 'Why did inventory rise?');
      expect(stored['content'], contains('Inventory rose by 12%.'));
      expect(phoneReportFromMessage({'attachments': []}), isNull);
      expect(phoneQuestionFromMessage({'content': 'ordinary message'}), isNull);
      expect(
        phoneReportFromMessage({
          'attachments': [
            {'source': 'other', 'report_reference': report.toJson()},
          ],
        }),
        isNull,
      );
    },
  );

  test(
    'report drafts survive restart isolated by computer and conversation',
    () async {
      final first = PhoneDeviceState.draftKey(
        'https://first.test',
        'work',
        'a',
        '',
      );
      final otherChat = PhoneDeviceState.draftKey(
        'https://first.test',
        'work',
        'b',
        '',
      );
      final otherHost = PhoneDeviceState.draftKey(
        'https://second.test',
        'work',
        'a',
        '',
      );
      final state = PhoneDeviceState(
        drafts: {first: 'Keep my question'},
        reportDrafts: {
          first: reference(),
          otherChat: reference(id: 'report-b'),
          otherHost: reference(id: 'other-computer-report'),
        },
      );
      final storage = MemoryPhoneStatePersistence();
      await storage.save(state.toJson());
      final restored = PhoneDeviceState.fromJson(await storage.load());
      expect(restored.reportDrafts[first]?.id, 'report-a');
      expect(restored.reportDrafts[otherChat]?.id, 'report-b');
      expect(restored.reportDrafts[otherHost]?.id, 'other-computer-report');
      expect(restored.drafts[first], 'Keep my question');
      restored.reportDrafts.remove(first);
      expect(restored.reportDrafts[otherChat]?.id, 'report-b');
      expect(restored.reportDrafts[otherHost]?.id, 'other-computer-report');
    },
  );

  test(
    'older phone state loads without references and ignores incomplete entries',
    () {
      expect(PhoneDeviceState.fromJson({'version': 1}).reportDrafts, isEmpty);
      final restored = PhoneDeviceState.fromJson({
        'report_drafts': {
          'invalid': 'unexpected value',
          'missing-id': {'title': 'No identity'},
          'valid': reference().toJson(),
        },
      });
      expect(restored.reportDrafts.keys, ['valid']);
      final link = PhoneReportReference.fromPreview(
        id: 'remote',
        title: 'Remote report',
        url: 'https://example.test/report',
      );
      expect(
        link.composeMessage('Explain.'),
        contains('URL: "https://example.test/report"'),
      );
      expect(link.composeMessage('Explain.'), contains('Reference only'));
      expect(link.toAttachment('Explain.')['path'], isNull);
    },
  );
}
