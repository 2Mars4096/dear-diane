import 'dart:convert';
import 'dart:io';

import 'package:diane_phone/phone_transport.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late HttpServer server;
  late String base;
  setUp(() async {
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    base = 'http://127.0.0.1:${server.port}';
  });
  tearDown(() async => server.close(force: true));

  test(
    'server failure after a write remains uncertain and retains bearer scope',
    () async {
      String? authorization;
      Map<String, dynamic>? received;
      server.listen((request) async {
        authorization = request.headers.value(HttpHeaders.authorizationHeader);
        received =
            jsonDecode(await utf8.decodeStream(request))
                as Map<String, dynamic>;
        request.response.statusCode = 503;
        request.response.write(
          jsonEncode({
            'detail': {'message': 'Acknowledgement unavailable'},
          }),
        );
        await request.response.close();
      });
      await expectLater(
        HttpPhoneApiClient().request(
          base,
          '/api/phone/requests',
          method: 'POST',
          body: {'request_id': 'stable'},
          token: 'test-token',
        ),
        throwsA(
          isA<PhoneApiFailure>()
              .having((failure) => failure.uncertain, 'uncertain', isTrue)
              .having((failure) => failure.statusCode, 'status', 503),
        ),
      );
      expect(authorization, 'Bearer test-token');
      expect(received, {'request_id': 'stable'});
    },
  );

  test('a setup conflict is different from an expired remote action', () async {
    var requests = 0;
    server.listen((request) async {
      request.response.statusCode = 409;
      request.response.write(
        jsonEncode({
          'detail': {
            'code': requests++ == 0
                ? 'execution_context_unavailable'
                : 'approval_expired',
            'message': 'Review current computer state',
          },
        }),
      );
      await request.response.close();
    });
    final api = HttpPhoneApiClient();
    await expectLater(
      api.request(base, '/action', method: 'POST'),
      throwsA(
        isA<PhoneApiFailure>()
            .having((failure) => failure.isExpired, 'isExpired', isFalse)
            .having((failure) => failure.uncertain, 'uncertain', isFalse),
      ),
    );
    await expectLater(
      api.request(base, '/action', method: 'POST'),
      throwsA(
        isA<PhoneApiFailure>().having(
          (failure) => failure.isExpired,
          'isExpired',
          isTrue,
        ),
      ),
    );
  });

  test('request timeout includes an unfinished response body', () async {
    server.listen((request) async {
      request.response.headers.contentType = ContentType.json;
      request.response.write('{"delivery_status":');
      await request.response.flush();
      // Deliberately keep the response open; the client must bound body reads.
    });
    await expectLater(
      HttpPhoneApiClient().request(
        base,
        '/api/phone/requests',
        method: 'POST',
        timeout: const Duration(milliseconds: 100),
      ),
      throwsA(
        isA<PhoneApiFailure>().having(
          (failure) => failure.uncertain,
          'uncertain',
          isTrue,
        ),
      ),
    );
  });
}
