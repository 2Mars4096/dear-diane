import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// A failed request is not an acknowledgement. Mutating requests with no
/// response must be reconciled using their original request identifier.
class PhoneApiFailure implements Exception {
  const PhoneApiFailure(
    this.message, {
    this.statusCode,
    this.uncertain = false,
    this.code = '',
  });

  final String message;
  final int? statusCode;
  final bool uncertain;
  final String code;

  bool get isExpired =>
      ['approval_expired', 'run_not_active'].contains(code) ||
      statusCode == 410;
  bool get retryable =>
      statusCode == null ||
      statusCode == 408 ||
      statusCode == 429 ||
      (statusCode ?? 0) >= 500;

  @override
  String toString() => message;
}

abstract class PhoneApiClient {
  Future<Map<String, dynamic>> request(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    String token = '',
    Duration timeout = const Duration(seconds: 15),
  });
}

class HttpPhoneApiClient implements PhoneApiClient {
  HttpPhoneApiClient({HttpClient Function()? clientFactory})
    : _clientFactory = clientFactory ?? HttpClient.new;

  final HttpClient Function() _clientFactory;

  @override
  Future<Map<String, dynamic>> request(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    String token = '',
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final uri = Uri.tryParse('$base$path');
    if (uri == null ||
        !['http', 'https'].contains(uri.scheme) ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty) {
      throw const PhoneApiFailure('The computer address is not valid.');
    }
    final client = _clientFactory();
    client.connectionTimeout = const Duration(seconds: 8);
    final mutating = method != 'GET';
    try {
      return await (() async {
        final request = await client.openUrl(method, uri);
        request.followRedirects = false;
        if (token.isNotEmpty) {
          request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
        }
        if (body != null) {
          request.headers.contentType = ContentType.json;
          request.write(jsonEncode(body));
        }
        final response = await request.close();
        final text = await utf8.decodeStream(response);
        Map<String, dynamic>? value;
        try {
          final parsed = text.isEmpty ? <String, dynamic>{} : jsonDecode(text);
          if (parsed is Map<String, dynamic>) value = parsed;
        } on FormatException {
          // Status still conveys a rejected request even with a non-JSON body.
        }
        if (response.statusCode < 200 || response.statusCode >= 300) {
          final detail = value?['detail'];
          final message = detail is String
              ? detail
              : detail is Map
              ? '${detail['message'] ?? detail['error'] ?? 'Request rejected'}'
              : response.statusCode == 401 || response.statusCode == 403
              ? 'Reconnect this computer with a valid access token.'
              : 'The computer rejected this request (${response.statusCode}).';
          throw PhoneApiFailure(
            message,
            statusCode: response.statusCode,
            code: detail is Map ? '${detail['code'] ?? ''}' : '',
            uncertain: mutating && response.statusCode >= 500,
          );
        }
        if (value == null) {
          throw PhoneApiFailure(
            'The computer returned an unreadable response.',
            uncertain: mutating,
          );
        }
        return value;
      })().timeout(timeout);
    } on PhoneApiFailure {
      rethrow;
    } on TimeoutException {
      throw PhoneApiFailure(
        'No acknowledgement yet. Check the connection and retry.',
        uncertain: mutating,
      );
    } on SocketException {
      throw PhoneApiFailure(
        'The computer could not be reached.',
        uncertain: mutating,
      );
    } on HttpException {
      throw PhoneApiFailure(
        'The connection ended before acknowledgement.',
        uncertain: mutating,
      );
    } on HandshakeException {
      throw const PhoneApiFailure(
        'The computer’s secure connection could not be verified.',
      );
    } finally {
      client.close(force: true);
    }
  }
}
