import 'dart:convert';

class PhoneDeliveryItem {
  const PhoneDeliveryItem({
    required this.id,
    required this.kind,
    required this.text,
    required this.apiBase,
    this.status = 'queued',
    this.error = '',
    this.threadId = '',
    this.workflowId = '_scratch',
    this.runId = '',
    required this.createdAt,
    this.payload = const {},
  });

  final String id,
      kind,
      text,
      status,
      error,
      threadId,
      workflowId,
      apiBase,
      runId;
  final DateTime createdAt;
  final Map<String, Object?> payload;

  bool get canRetry => ['queued', 'uncertain', 'failed'].contains(status);
  bool get isPending =>
      !['accepted', 'expired', 'needs_review', 'rejected'].contains(status);
  String get statusLabel => switch (status) {
    'queued' => 'Saved on this phone',
    'sending' => 'Waiting for acknowledgement',
    'uncertain' => 'Delivery unconfirmed',
    'failed' => 'Not delivered',
    'expired' => 'No longer available',
    'needs_review' => 'Check on your computer',
    'rejected' => 'Action not applied',
    'accepted' => 'Accepted by computer',
    _ => status,
  };

  PhoneDeliveryItem copyWith({
    String? status,
    String? error,
    String? threadId,
    String? workflowId,
    String? runId,
    Map<String, Object?>? payload,
  }) => PhoneDeliveryItem(
    id: id,
    kind: kind,
    text: text,
    apiBase: apiBase,
    createdAt: createdAt,
    status: status ?? this.status,
    error: error ?? this.error,
    threadId: threadId ?? this.threadId,
    workflowId: workflowId ?? this.workflowId,
    runId: runId ?? this.runId,
    payload: payload ?? this.payload,
  );

  Map<String, Object?> toJson() => {
    'id': id,
    'kind': kind,
    'text': text,
    'api_base': apiBase,
    'status': status,
    'error': error,
    'thread_id': threadId,
    'workflow_id': workflowId,
    'run_id': runId,
    'created_at': createdAt.toUtc().toIso8601String(),
    'payload': payload,
  };

  factory PhoneDeliveryItem.fromJson(Map<String, dynamic> json) {
    // A killed app never knows whether an in-flight write reached its host.
    final savedStatus = '${json['status'] ?? 'queued'}';
    return PhoneDeliveryItem(
      id: '${json['id'] ?? ''}',
      kind: '${json['kind'] ?? 'send'}',
      text: '${json['text'] ?? ''}',
      apiBase: '${json['api_base'] ?? ''}',
      status: savedStatus == 'sending' ? 'uncertain' : savedStatus,
      error: '${json['error'] ?? ''}',
      threadId: '${json['thread_id'] ?? ''}',
      workflowId: '${json['workflow_id'] ?? '_scratch'}',
      runId: '${json['run_id'] ?? ''}',
      createdAt:
          DateTime.tryParse('${json['created_at']}') ??
          DateTime.fromMillisecondsSinceEpoch(0),
      payload: json['payload'] is Map
          ? Map<String, Object?>.from(json['payload'] as Map)
          : const {},
    );
  }
}

class PhoneApprovalRequest {
  const PhoneApprovalRequest({
    required this.id,
    required this.runId,
    this.workerId = '',
    required this.method,
    this.params = const {},
    this.status = 'pending',
    this.error = '',
  });

  final String id, runId, workerId, method, status, error;
  final Map<String, dynamic> params;
  String get key => '$runId/$workerId/$id';
  bool get canAnswer => status == 'pending';
  List<Map<String, dynamic>> get questions =>
      (params['questions'] is List ? params['questions'] as List : const [])
          .whereType<Map>()
          .map((value) => Map<String, dynamic>.from(value))
          .toList();
  bool get isQuestion => questions.isNotEmpty;
  String get title => isQuestion
      ? 'Diane needs your answer'
      : '${params['tool_name'] ?? (method.contains('commandExecution') ? 'Run command' : 'Action')} needs permission';

  PhoneApprovalRequest copyWith({String? status, String? error}) =>
      PhoneApprovalRequest(
        id: id,
        runId: runId,
        workerId: workerId,
        method: method,
        params: params,
        status: status ?? this.status,
        error: error ?? this.error,
      );

  factory PhoneApprovalRequest.fromJson(Map<String, dynamic> json) =>
      PhoneApprovalRequest(
        id: '${json['request_id'] ?? json['id'] ?? ''}',
        runId: '${json['run_id'] ?? ''}',
        workerId: '${json['worker_id'] ?? ''}',
        method: '${json['method'] ?? ''}',
        params: json['params'] is Map
            ? Map<String, dynamic>.from(json['params'] as Map)
            : const {},
      );
}

/// Device state is namespaced by host. A selected session or queued command
/// from one computer must never be applied to another computer.
class PhoneDeviceState {
  PhoneDeviceState({
    Map<String, Map<String, dynamic>>? selections,
    Map<String, String>? drafts,
    List<PhoneDeliveryItem>? deliveries,
  }) : selections = selections ?? {},
       drafts = drafts ?? {},
       deliveries = deliveries ?? [];

  final Map<String, Map<String, dynamic>> selections;
  final Map<String, String> drafts;
  final List<PhoneDeliveryItem> deliveries;

  static String draftKey(
    String base,
    String workflow,
    String thread,
    String workspace,
  ) => jsonEncode([base, workflow, thread.isEmpty ? 'new:$workspace' : thread]);

  Map<String, Object?> toJson() => {
    'version': 1,
    'selections': selections,
    'drafts': drafts,
    'deliveries': deliveries.map((item) => item.toJson()).toList(),
  };

  factory PhoneDeviceState.fromJson(Map<String, dynamic> json) {
    final selections = <String, Map<String, dynamic>>{};
    if (json['selections'] is Map) {
      for (final entry in (json['selections'] as Map).entries) {
        if (entry.value is Map) {
          selections['${entry.key}'] = Map<String, dynamic>.from(
            entry.value as Map,
          );
        }
      }
    }
    return PhoneDeviceState(
      selections: selections,
      drafts: json['drafts'] is Map
          ? (json['drafts'] as Map).map(
              (key, value) => MapEntry('$key', '$value'),
            )
          : {},
      deliveries:
          (json['deliveries'] is List ? json['deliveries'] as List : const [])
              .whereType<Map>()
              .map(
                (item) =>
                    PhoneDeliveryItem.fromJson(Map<String, dynamic>.from(item)),
              )
              .where((item) => item.id.isNotEmpty && item.apiBase.isNotEmpty)
              .toList(),
    );
  }
}

abstract class PhoneStatePersistence {
  Future<Map<String, dynamic>> load();
  Future<void> save(Map<String, Object?> state);
}

class MemoryPhoneStatePersistence implements PhoneStatePersistence {
  MemoryPhoneStatePersistence([Map<String, dynamic>? value])
    : _value = value ?? {};
  Map<String, dynamic> _value;
  @override
  Future<Map<String, dynamic>> load() async =>
      Map<String, dynamic>.from(jsonDecode(jsonEncode(_value)) as Map);
  @override
  Future<void> save(Map<String, Object?> state) async {
    _value = Map<String, dynamic>.from(jsonDecode(jsonEncode(state)) as Map);
  }
}

/// Non-overlapping polling uses this delay after each completed request.
Duration phoneReconnectDelay(int failures) => Duration(
  seconds: failures <= 0 ? 2 : (2 << (failures - 1).clamp(0, 4)).clamp(2, 30),
);

/// Admission is distinct from successful command dispatch. Old and current
/// receipt schemas can both describe a rejected or ambiguous action.
String phoneReceiptDeliveryStatus(Map<String, dynamic> receipt) {
  final phase = '${receipt['phase'] ?? ''}';
  final status = '${receipt['status'] ?? ''}';
  final delivery = '${receipt['delivery_status'] ?? ''}';
  final code = '${receipt['error_code'] ?? ''}';
  if (phase == 'needs_review' ||
      status == 'needs_review' ||
      delivery == 'needs_review' ||
      code == 'receipt_needs_review') {
    return 'needs_review';
  }
  if (phase == 'rejected' || delivery == 'rejected') {
    return ['approval_expired', 'run_not_active'].contains(code)
        ? 'expired'
        : 'rejected';
  }
  if (delivery != 'accepted') return 'uncertain';
  if (phase.isNotEmpty && phase != 'submitted') return 'uncertain';
  return 'accepted';
}
