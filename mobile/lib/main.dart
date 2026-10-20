import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'phone_state.dart';
import 'phone_transport.dart';

export 'phone_state.dart';
export 'phone_transport.dart';

part 'phone_views.dart';

const String compiledDefaultApiBase = String.fromEnvironment(
  'DAN_API_BASE',
  defaultValue: 'http://10.77.77.2:8000',
);

const List<String> fallbackApiBases = [
  'http://10.77.77.2:8000',
  'http://10.13.187.214:8000',
  'http://10.0.2.2:8000',
];

const MethodChannel wireGuardChannel = MethodChannel('dan/wireguard');

const String compiledPhoneWireGuardConfigB64 = String.fromEnvironment(
  'DAN_PHONE_WIREGUARD_CONFIG_B64',
  defaultValue: '',
);

const bool compiledPhoneWireGuardAutoStart = bool.fromEnvironment(
  'DAN_PHONE_WIREGUARD_AUTOSTART',
  defaultValue: false,
);

void main() {
  runApp(const DanPhoneApp());
}

enum WorkspaceMode { work, notes }

enum WorkPage { chat, files, preview }

enum WorkRunMode { auto, review }

enum NotesPage { pages, edit, read }

enum PhoneChatRole { user, assistant, status }

extension WorkRunModeLabel on WorkRunMode {
  String get label => this == WorkRunMode.review ? 'Review' : 'Auto';

  String get description => this == WorkRunMode.review
      ? 'Pause on ambiguous attention choices.'
      : 'Let Diane choose the next action.';
}

class WireGuardStatus {
  const WireGuardStatus({
    required this.status,
    required this.mode,
    required this.interfaceName,
    required this.configPresent,
    required this.mutatingActionsEnabled,
    required this.safeActions,
    required this.conflictPolicy,
  });

  final String status;
  final String mode;
  final String interfaceName;
  final bool configPresent;
  final bool mutatingActionsEnabled;
  final List<String> safeActions;
  final String conflictPolicy;

  factory WireGuardStatus.fromJson(Map<String, dynamic> json) {
    return WireGuardStatus(
      status: _asString(json['status'], 'unknown'),
      mode: _asString(json['mode'], 'unknown'),
      interfaceName: _asString(json['interface'], 'unknown'),
      configPresent: json['config_present'] == true,
      mutatingActionsEnabled: json['mutating_actions_enabled'] == true,
      safeActions: (json['safe_actions'] as List<dynamic>? ?? const [])
          .map((value) => value.toString())
          .toList(),
      conflictPolicy: _asString(
        json['conflict_policy'],
        'Read-only status; no WireGuard mutations are attempted.',
      ),
    );
  }
}

class PhoneWireGuardStatus {
  const PhoneWireGuardStatus({
    required this.supported,
    required this.name,
    required this.state,
    this.message = '',
  });

  final bool supported;
  final String name;
  final String state;
  final String message;

  factory PhoneWireGuardStatus.fromMap(Map<Object?, Object?> value) {
    return PhoneWireGuardStatus(
      supported: value['supported'] == true,
      name: _asString(value['name'], 'dan-phone'),
      state: _asString(value['state'], 'unknown'),
      message: _asString(value['message'], ''),
    );
  }
}

class WorkspaceNoteSummary {
  const WorkspaceNoteSummary({
    required this.path,
    required this.relativePath,
    required this.title,
    required this.section,
    required this.tags,
    required this.categories,
    required this.pageId,
  });

  final String path;
  final String relativePath;
  final String title;
  final String section;
  final List<String> tags;
  final List<String> categories;
  final String pageId;

  factory WorkspaceNoteSummary.fromJson(Map<String, dynamic> json) {
    return WorkspaceNoteSummary(
      path: _asString(json['path'], ''),
      relativePath: _asString(json['relative_path'], ''),
      title: _asString(json['title'], 'Untitled'),
      section: _asString(json['section'], 'root'),
      tags: _asStringList(json['tags']),
      categories: _asStringList(json['categories']),
      pageId: _asString(json['page_id'], ''),
    );
  }
}

class WorkspaceNotePreview {
  const WorkspaceNotePreview({
    required this.path,
    required this.title,
    required this.bodyMarkdown,
    required this.previewMarkdown,
    required this.compiledHtml,
    required this.compiler,
    required this.cacheKey,
    required this.routePath,
    required this.compiledAt,
  });

  final String path;
  final String title;
  final String bodyMarkdown;
  final String previewMarkdown;
  final String compiledHtml;
  final String compiler;
  final String cacheKey;
  final String routePath;
  final String compiledAt;

  factory WorkspaceNotePreview.fromJson(Map<String, dynamic> json) {
    final note = json['note'] is Map<String, dynamic>
        ? json['note'] as Map<String, dynamic>
        : const <String, dynamic>{};
    return WorkspaceNotePreview(
      path: _asString(note['path'], ''),
      title: _asString(note['title'], 'Preview'),
      bodyMarkdown: _asString(json['body_markdown'], ''),
      previewMarkdown: _asString(
        json['preview_markdown'],
        _asString(json['body_markdown'], ''),
      ),
      compiledHtml: _asString(json['compiled_html'], ''),
      compiler: _asString(json['compiler'], 'markdown'),
      cacheKey: _asString(json['cache_key'], ''),
      routePath: _asString(json['route_path'], ''),
      compiledAt: _asString(json['compiled_at'], ''),
    );
  }
}

class WorkspaceFileEntry {
  const WorkspaceFileEntry({
    required this.path,
    required this.relativePath,
    required this.name,
    required this.parent,
    required this.isDirectory,
    required this.size,
    required this.depth,
  });

  final String path;
  final String relativePath;
  final String name;
  final String parent;
  final bool isDirectory;
  final int size;
  final int depth;

  factory WorkspaceFileEntry.fromJson(Map<String, dynamic> json) {
    return WorkspaceFileEntry(
      path: _asString(json['path'], ''),
      relativePath: _asString(json['relative_path'], ''),
      name: _asString(json['name'], 'Untitled'),
      parent: _asString(json['parent'], ''),
      isDirectory: json['is_directory'] == true,
      size: _asInt(json['size']),
      depth: _asInt(json['depth']),
    );
  }
}

class WorkspaceRootSuggestion {
  const WorkspaceRootSuggestion({
    required this.path,
    required this.name,
    required this.label,
    required this.kind,
  });

  final String path;
  final String name;
  final String label;
  final String kind;

  factory WorkspaceRootSuggestion.fromJson(Map<String, dynamic> json) {
    return WorkspaceRootSuggestion(
      path: _asString(json['path'], ''),
      name: _asString(json['name'], ''),
      label: _asString(json['label'], ''),
      kind: _asString(json['kind'], 'workspace'),
    );
  }
}

class PhoneChatMessage {
  const PhoneChatMessage({
    required this.id,
    required this.role,
    required this.text,
    this.status = '',
    this.record = const {},
  });

  final String id;
  final PhoneChatRole role;
  final String text;
  final String status;
  final Map<String, dynamic> record;

  PhoneChatMessage copyWith({String? text, String? status}) {
    return PhoneChatMessage(
      id: id,
      role: role,
      text: text ?? this.text,
      status: status ?? this.status,
      record: record,
    );
  }
}

class PhoneAttachmentDraft {
  const PhoneAttachmentDraft({
    required this.id,
    required this.name,
    required this.mimeType,
    required this.sizeBytes,
    required this.dataUrl,
  });

  final String id;
  final String name;
  final String mimeType;
  final int sizeBytes;
  final String dataUrl;

  Map<String, Object?> toPayload() {
    return {
      'id': id,
      'kind': 'image',
      'name': name,
      'display_name': name,
      'mime_type': mimeType,
      'size_bytes': sizeBytes,
      'source': 'dan-phone-clipboard',
      'data_url': dataUrl,
    };
  }
}

class PhonePreviewArtifact {
  const PhonePreviewArtifact({
    required this.id,
    required this.title,
    required this.path,
    required this.url,
    required this.kind,
    required this.source,
  });

  final String id;
  final String title;
  final String path;
  final String url;
  final String kind;
  final String source;

  factory PhonePreviewArtifact.fromJson(Map<String, dynamic> json) {
    final path = _asString(json['path'], _asString(json['local_path'], ''));
    final url = _asString(json['url'], _asString(json['uri'], ''));
    final displayName = _asString(
      json['display_name'],
      _asString(json['name'], ''),
    );
    final title = displayName.isNotEmpty
        ? displayName
        : _baseName(path.isNotEmpty ? path : url);
    final fallbackId = [
      path,
      url,
      title,
      _asString(json['kind'], ''),
    ].where((value) => value.isNotEmpty).join('|');
    return PhonePreviewArtifact(
      id: _asString(json['id'], fallbackId),
      title: title.isEmpty ? 'Output artifact' : title,
      path: path,
      url: url,
      kind: _asString(json['kind'], 'artifact'),
      source: _asString(json['source'], 'run'),
    );
  }

  @override
  bool operator ==(Object other) {
    return other is PhonePreviewArtifact &&
        other.id == id &&
        other.title == title &&
        other.path == path &&
        other.url == url &&
        other.kind == kind &&
        other.source == source;
  }

  @override
  int get hashCode => Object.hash(id, title, path, url, kind, source);
}

class PhoneSessionSummary {
  const PhoneSessionSummary({
    required this.id,
    required this.workflowId,
    required this.title,
    required this.messageCount,
    required this.updatedAt,
    required this.archived,
    required this.mode,
  });

  final String id;
  final String workflowId;
  final String title;
  final int messageCount;
  final String updatedAt;
  final bool archived;
  final String mode;

  String get displayTitle {
    final cleaned = title.trim();
    return cleaned.isEmpty ? 'New Diane Session' : cleaned;
  }

  factory PhoneSessionSummary.fromJson(Map<String, dynamic> json) {
    return PhoneSessionSummary(
      id: _asString(json['id'], ''),
      workflowId: _asString(json['workflow_id'], '_scratch'),
      title: _asString(json['title'], 'New Diane Session'),
      messageCount: _asInt(json['message_count']),
      updatedAt: _asString(json['updated_at'], ''),
      archived: json['archived'] == true,
      mode: _asString(json['mode'], 'agent'),
    );
  }
}

class PhoneTaskSummary {
  const PhoneTaskSummary({
    required this.taskId,
    required this.threadId,
    required this.status,
    required this.latestProgress,
    required this.workspaceRoot,
    required this.workspaceId,
    required this.activeRunId,
    required this.title,
  });

  final String taskId;
  final String threadId;
  final String status;
  final String latestProgress;
  final String workspaceRoot;
  final String workspaceId;
  final String activeRunId;
  final String title;

  bool get isLive {
    final normalized = status.toLowerCase();
    return normalized == 'queued' ||
        normalized == 'running' ||
        normalized == 'active' ||
        normalized == 'waiting' ||
        normalized == 'paused';
  }

  factory PhoneTaskSummary.fromJson(Map<String, dynamic> json) {
    final metadata =
        _recordValue(json['metadata']) ?? const <String, dynamic>{};
    return PhoneTaskSummary(
      taskId: _asString(json['task_id'], ''),
      threadId: _asString(json['thread_id'], ''),
      status: _asString(json['status'], 'unknown'),
      latestProgress: _asString(json['latest_progress'], ''),
      workspaceRoot: _asString(
        metadata['workspace_root'],
        _asString(json['workspace_root'], ''),
      ),
      workspaceId: _asString(
        metadata['workspace_id'],
        _asString(json['workspace_id'], ''),
      ),
      activeRunId: _asString(metadata['active_run_id'], ''),
      title: _asString(json['title'], ''),
    );
  }
}

class PhoneWorkspaceSummary {
  const PhoneWorkspaceSummary({required this.root, required this.name});

  final String root;
  final String name;

  String get id => root.isEmpty ? '_scratch' : root;
}

class PhoneWorkspaceSessionGroup {
  const PhoneWorkspaceSessionGroup({
    required this.root,
    required this.name,
    required this.sessions,
    required this.archived,
  });

  final String root;
  final String name;
  final List<PhoneSessionSummary> sessions;
  final bool archived;
}

String _asString(Object? value, String fallback) {
  final text = value?.toString().trim();
  return text == null || text.isEmpty ? fallback : text;
}

int _asInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '') ?? 0;
}

String _baseName(String path) {
  final cleaned = path.split('?').first.split('#').first;
  final parts = cleaned
      .split(RegExp(r'[\\/]'))
      .where((part) => part.isNotEmpty)
      .toList();
  return parts.isEmpty ? cleaned : parts.last;
}

String _normalizeAccessToken(String value) {
  var text = value.trim();
  if (text.toLowerCase().startsWith('bearer ')) {
    text = text.substring(7).trim();
  }
  return text;
}

List<String> _asStringList(Object? value) {
  if (value is! List<dynamic>) return const [];
  return value
      .map((item) => item.toString().trim())
      .where((item) => item.isNotEmpty)
      .toList();
}

List<String> _decodeStringListSetting(String value) {
  if (value.trim().isEmpty) return const [];
  try {
    final decoded = jsonDecode(value);
    return _asStringList(decoded);
  } catch (_) {
    return const [];
  }
}

Map<String, String> _decodeStringMapSetting(String value) {
  if (value.trim().isEmpty) return const {};
  try {
    final decoded = jsonDecode(value);
    if (decoded is! Map) return const {};
    return decoded.map(
      (key, item) => MapEntry(key.toString(), item.toString().trim()),
    )..removeWhere((key, item) => key.trim().isEmpty || item.isEmpty);
  } catch (_) {
    return const {};
  }
}

String _sessionWorkspaceKey(String workflowId, String threadId) {
  return '${workflowId.trim().isEmpty ? '_scratch' : workflowId.trim()}:$threadId';
}

String _workspaceDisplayName(String root) {
  if (root.trim().isEmpty) return 'Project: Scratch';
  final base = _baseName(root);
  return base.isEmpty ? root : base;
}

String _compactDateTimeLabel(String value) {
  if (value.trim().isEmpty) return '';
  try {
    final parsed = DateTime.parse(value).toLocal();
    final now = DateTime.now();
    final sameDay =
        parsed.year == now.year &&
        parsed.month == now.month &&
        parsed.day == now.day;
    final hour = parsed.hour.toString().padLeft(2, '0');
    final minute = parsed.minute.toString().padLeft(2, '0');
    if (sameDay) return '$hour:$minute';
    return '${parsed.month}/${parsed.day} $hour:$minute';
  } catch (_) {
    return value;
  }
}

List<PhoneWorkspaceSummary> _mergeWorkspaceRoots(
  List<PhoneWorkspaceSummary> current,
  Iterable<String> roots,
) {
  final byRoot = <String, PhoneWorkspaceSummary>{
    for (final item in current) item.root: item,
  };
  for (final rawRoot in roots) {
    final root = rawRoot.trim();
    if (root.isEmpty || byRoot.containsKey(root)) continue;
    byRoot[root] = PhoneWorkspaceSummary(
      root: root,
      name: _workspaceDisplayName(root),
    );
  }
  return byRoot.values.toList()
    ..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
}

String _imageMimeTypeFromDataUrl(String value) {
  final match = RegExp(
    r'^data:(image/[A-Za-z0-9.+-]+);base64,',
  ).firstMatch(value.trim());
  return match?.group(1)?.toLowerCase() ?? '';
}

bool _looksLikeImageDataUrl(String value) {
  return _imageMimeTypeFromDataUrl(value).isNotEmpty;
}

int _estimatedDataUrlBytes(String value) {
  final comma = value.indexOf(',');
  if (comma < 0) return 0;
  final encoded = value.substring(comma + 1).replaceAll(RegExp(r'\s+'), '');
  if (encoded.isEmpty) return 0;
  final padding = encoded.endsWith('==')
      ? 2
      : encoded.endsWith('=')
      ? 1
      : 0;
  return ((encoded.length * 3) ~/ 4) - padding;
}

String _attachmentExtensionForMimeType(String mimeType) {
  return switch (mimeType.toLowerCase()) {
    'image/jpeg' => 'jpg',
    'image/webp' => 'webp',
    'image/gif' => 'gif',
    _ => 'png',
  };
}

String _formatBytes(int bytes) {
  if (bytes <= 0) return '';
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).round()} KB';
  return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
}

bool _isGenericAgentReceipt(String value) {
  final normalized = value.trim().toLowerCase();
  return {
    'completed.',
    'completed',
    'run finished',
    'finished',
    'super dan accepted the request.',
    'diane accepted the request.',
  }.contains(normalized);
}

Map<String, dynamic>? _recordValue(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map((key, item) => MapEntry(key.toString(), item));
  }
  return null;
}

String _scalarDetailText(Object? value) {
  if (value == null) return '';
  if (value is String) return value.trim();
  if (value is num || value is bool) return value.toString();
  final record = _recordValue(value);
  if (record != null) {
    for (final key in [
      'summary',
      'message',
      'text',
      'title',
      'path',
      'relative_path',
      'name',
      'error',
      'reason',
    ]) {
      final text = _scalarDetailText(record[key]);
      if (text.isNotEmpty) return text;
    }
  }
  return '';
}

List<String> _detailItemsFromValue(Object? value) {
  if (value == null) return const [];
  if (value is List) {
    return value
        .expand(_detailItemsFromValue)
        .where((item) => item.isNotEmpty)
        .toSet()
        .toList();
  }
  final record = _recordValue(value);
  if (record != null) {
    final path = _scalarDetailText(record['path']).isNotEmpty
        ? _scalarDetailText(record['path'])
        : _scalarDetailText(record['relative_path']);
    if (path.isNotEmpty) return [path];
    final label = _scalarDetailText(record);
    return label.isEmpty ? const [] : [label];
  }
  final scalar = _scalarDetailText(value);
  return scalar.isEmpty ? const [] : [scalar];
}

List<String> _detailItemsForKeys(
  Map<String, dynamic> record,
  List<String> keys,
) {
  return keys
      .expand((key) => _detailItemsFromValue(record[key]))
      .where((item) => item.trim().isNotEmpty)
      .toSet()
      .toList();
}

bool _looksLikeOptionalContinuationItem(String value) {
  final normalized = value.toLowerCase();
  return normalized.contains('if you want') ||
      normalized.contains('optional') ||
      normalized.contains('could also') ||
      normalized.contains('next step') ||
      normalized.contains('follow-up') ||
      normalized.contains('follow up');
}

String _sectionMarkdown(String title, List<String> items) {
  if (items.isEmpty) return '';
  final body = items.length == 1
      ? items.first
      : items.map((item) => '- $item').join('\n');
  return '**$title**\n$body';
}

String _structuredAgentDisplayFromValue(
  Object? value, [
  Set<Object?>? seen,
  int depth = 0,
]) {
  if (value == null || depth > 4) return '';
  seen ??= <Object?>{};
  if (seen.contains(value)) return '';
  final record = _recordValue(value);
  if (record != null) {
    seen.add(value);
    final direct = _structuredAgentDisplayFromRecord(record);
    if (direct.isNotEmpty) return direct;
    for (final key in [
      'result',
      'final',
      'output',
      'outputs',
      'response',
      'data',
      'payload',
      'text',
    ]) {
      final nested = _structuredAgentDisplayFromValue(
        record[key],
        seen,
        depth + 1,
      );
      if (nested.isNotEmpty) return nested;
    }
  }
  if (value is List) {
    seen.add(value);
    for (final item in value) {
      final nested = _structuredAgentDisplayFromValue(item, seen, depth + 1);
      if (nested.isNotEmpty) return nested;
    }
  }
  return '';
}

String _structuredAgentDisplayFromRecord(Map<String, dynamic> data) {
  final finalAnswerItems = _detailItemsForKeys(data, [
    'answer',
    'final_answer',
    'public_response',
    'final_response',
    'response',
  ]);
  final changeItems = _detailItemsForKeys(data, [
    'summary',
    'change_summary',
    'completion_summary',
    'outcome',
    'message',
    'result_summary',
  ]);
  final created = _detailItemsForKeys(data, [
    'files_created',
    'created_files',
    'artifacts_created',
  ]);
  final changed = _detailItemsForKeys(data, [
    'files_changed',
    'changed_files',
    'files_modified',
    'modified_files',
  ]).where((path) => !created.contains(path)).toList();
  final artifacts = _detailItemsForKeys(data, ['artifacts', 'artifact_refs'])
      .where((path) => !created.contains(path) && !changed.contains(path))
      .toList();
  final fileItems = [
    ...created.map((path) => 'Created: `$path`'),
    ...changed.map((path) => 'Changed: `$path`'),
    ...artifacts.map((path) => 'Artifact: `$path`'),
  ];
  final checkItems = _detailItemsForKeys(data, [
    'validation',
    'validation_summary',
    'checks',
    'tests',
  ]);
  final riskItems = _detailItemsForKeys(data, ['risks', 'risk', 'warnings']);
  final optionalSeedItems = _detailItemsForKeys(data, [
    'next_steps',
    'next_step',
    'optional_next_steps',
    'follow_up',
    'follow_ups',
    'suggestions',
  ]);
  final remainingSeedItems = _detailItemsForKeys(data, [
    'remaining_work',
    'remaining',
    'blockers',
    'blocked_on',
    'attention_needed',
    'needs_attention',
  ]);
  final optionalItems = {
    ...optionalSeedItems,
    ...remainingSeedItems.where(_looksLikeOptionalContinuationItem),
    ...riskItems.where(_looksLikeOptionalContinuationItem),
  }.toList();
  final attentionItems = {
    ...remainingSeedItems.where(
      (item) => !_looksLikeOptionalContinuationItem(item),
    ),
    ...riskItems.where((item) => !_looksLikeOptionalContinuationItem(item)),
  }.toList();
  final summaryItems = finalAnswerItems.isNotEmpty
      ? finalAnswerItems
      : changeItems.isNotEmpty
      ? changeItems.take(2).toList()
      : fileItems.isNotEmpty
      ? fileItems.take(2).toList()
      : checkItems.isNotEmpty
      ? checkItems.take(2).toList()
      : attentionItems.take(2).toList();
  final sections = [
    _sectionMarkdown('Summary', summaryItems),
    if (finalAnswerItems.isNotEmpty)
      _sectionMarkdown('What changed', changeItems),
    _sectionMarkdown('Files', fileItems),
    _sectionMarkdown('Checks', checkItems),
    _sectionMarkdown('Optional next steps', optionalItems),
    _sectionMarkdown('Needs attention', attentionItems),
  ].where((section) => section.isNotEmpty).toList();
  return sections.join('\n\n').trim();
}

String decodedDefaultPhoneWireGuardConfig() {
  final encoded = compiledPhoneWireGuardConfigB64.trim();
  if (encoded.isEmpty) return '';
  for (final codec in [base64, base64Url]) {
    try {
      return utf8.decode(codec.decode(codec.normalize(encoded)));
    } catch (_) {
      // Try the next base64 variant.
    }
  }
  return '';
}

bool isWireGuardApiBase(String value) {
  final host = Uri.tryParse(value)?.host;
  return host == '10.77.77.2' || (host?.startsWith('10.77.77.') ?? false);
}

class _PlatformPhoneStatePersistence implements PhoneStatePersistence {
  final MemoryPhoneStatePersistence _fallback = MemoryPhoneStatePersistence();

  @override
  Future<Map<String, dynamic>> load() async {
    if (!Platform.isAndroid) return _fallback.load();
    final raw = await wireGuardChannel.invokeMethod<String>('loadPhoneState');
    final value = jsonDecode(raw ?? '{}');
    return value is Map<String, dynamic> ? value : {};
  }

  @override
  Future<void> save(Map<String, Object?> state) async {
    if (!Platform.isAndroid) return _fallback.save(state);
    final saved = await wireGuardChannel.invokeMethod<bool>('savePhoneState', {
      'state': jsonEncode(state),
    });
    if (saved != true) throw StateError('Could not save work on this phone.');
  }
}

class DanPhoneApp extends StatelessWidget {
  const DanPhoneApp({
    super.key,
    this.apiClient,
    this.persistence,
    this.initialApiBase,
  });
  final PhoneApiClient? apiClient;
  final PhoneStatePersistence? persistence;
  final String? initialApiBase;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Dear Diane Phone',
      theme: buildPhoneTheme(Brightness.light),
      darkTheme: buildPhoneTheme(Brightness.dark),
      themeMode: ThemeMode.system,
      home: WorkspaceHome(
        apiClient: apiClient,
        persistence: persistence,
        initialApiBase: initialApiBase,
      ),
    );
  }
}

class WorkspaceHome extends StatefulWidget {
  const WorkspaceHome({
    super.key,
    this.apiClient,
    this.persistence,
    this.initialApiBase,
  });
  final PhoneApiClient? apiClient;
  final PhoneStatePersistence? persistence;
  final String? initialApiBase;

  @override
  State<WorkspaceHome> createState() => _WorkspaceHomeState();
}

class _WorkspaceHomeState extends State<WorkspaceHome>
    with WidgetsBindingObserver {
  late final PhoneApiClient _api;
  late final PhoneStatePersistence _persistence;
  PhoneDeviceState _deviceState = PhoneDeviceState();
  Future<void> _saveChain = Future<void>.value();
  final Set<String> _delivering = {};
  final Map<String, String> _savingDrafts = {};
  bool _flushing = false;
  bool _foreground = true;
  bool _restoringDraft = false;
  bool _deviceLoaded = false;
  bool _savedStateReadable = true;
  String _storageWarning = '';
  int _generation = 0;
  int _connectionGeneration = 0;
  int _pollFailures = 0;
  int _pollSubscription = 0;
  int _pollCycles = 0;
  Timer? _draftSaveTimer;
  int _phoneTab = 0;
  int _libraryTab = 0;
  String conversationTitle = '';
  DateTime? lastSyncedAt;
  Map<String, dynamic> executionContext = {};
  List<PhoneApprovalRequest> pendingApprovals = const [];

  List<PhoneDeliveryItem> get _deliveries => _deviceState.deliveries
      .where(
        (item) => item.apiBase == activeApiBase && item.status != 'accepted',
      )
      .toList();
  List<PhoneDeliveryItem> get _conversationDeliveries => _deliveries
      .where(
        (item) =>
            item.threadId == activeThreadId ||
            (item.threadId.isEmpty && activeThreadId.isEmpty),
      )
      .toList();

  WorkspaceMode mode = WorkspaceMode.work;
  WorkPage workPage = WorkPage.chat;
  WorkRunMode runMode = WorkRunMode.auto;
  NotesPage notesPage = NotesPage.pages;
  WireGuardStatus? wireGuard;
  String health = 'checking';
  String activeApiBase = compiledDefaultApiBase;
  String statusDetail = 'Checking backend';
  String accessToken = '';
  String phoneWireGuardConfig = decodedDefaultPhoneWireGuardConfig();
  String phoneWireGuardDetail = decodedDefaultPhoneWireGuardConfig().isEmpty
      ? 'No Dear Diane phone VPN config loaded'
      : 'Dear Diane phone VPN config loaded';
  List<WorkspaceNoteSummary> workspaceNotes = const [];
  WorkspaceNoteSummary? activeNote;
  final Map<String, WorkspaceNotePreview> notePreviewCache = {};
  final Map<String, double> noteReadScrollOffsets = {};
  final Map<String, double> noteEditScrollOffsets = {};
  String notesRoot = '';
  String noteContent = '';
  String noteStatus = 'Loading knowledge base';
  String notePreviewStatus = 'Preview not loaded';
  String workspaceRoot = '';
  List<WorkspaceRootSuggestion> workspaceRootSuggestions = const [];
  bool loadingWorkspaceRootSuggestions = false;
  List<PhoneWorkspaceSummary> phoneWorkspaces = const [];
  List<PhoneSessionSummary> phoneSessions = const [];
  Map<String, PhoneTaskSummary> phoneTasksByThread = const {};
  Map<String, String> sessionWorkspaceRoots = const {};
  bool loadingSessions = false;
  List<WorkspaceFileEntry> workspaceFiles = const [];
  WorkspaceFileEntry? activeFile;
  String activeFileContent = '';
  String fileStatus = 'Set a workspace root to browse files';
  bool loadingFiles = false;
  List<PhonePreviewArtifact> previewArtifacts = const [];
  bool previewArtifactsExpanded = true;
  String previewTitle = 'Preview';
  String previewStatus = 'Select a file, output artifact, or prompt log';
  String previewContent = '';
  PhonePreviewArtifact? activePreviewArtifact;
  bool loadingPromptLog = false;
  int promptLogEntryCount = 0;
  String promptLogPath = '';
  List<PhoneChatMessage> chatMessages = const [
    PhoneChatMessage(
      id: 'welcome',
      role: PhoneChatRole.status,
      text:
          'Continue a conversation from Work, or ask Diane to start something new.',
    ),
  ];
  String activeWorkflowId = '_scratch';
  String activeThreadId = '';
  String activeRunId = '';
  String activeTaskId = '';
  bool activeRunLive = false;
  bool chatBusy = false;
  String chatStatus = 'Ready';
  List<PhoneAttachmentDraft> composerAttachments = const [];
  Timer? agentPollTimer;
  Timer? rootSuggestionDebounce;
  Timer? noteAutosaveTimer;
  bool noteSaving = false;
  bool suppressNoteEditListener = false;
  String noteSaveStatus = 'Clean';
  PhoneWireGuardStatus? phoneWireGuard;
  bool phoneWireGuardBusy = false;
  bool loadingWireGuard = true;
  bool loadingNotes = true;
  late final TextEditingController apiBaseController;
  late final TextEditingController accessTokenController;
  late final TextEditingController wireGuardConfigController;
  late final TextEditingController noteEditController;
  late final TextEditingController workspaceRootController;
  late final TextEditingController chatController;
  late final ScrollController noteReadScrollController;
  late final ScrollController noteEditScrollController;
  final scaffoldKey = GlobalKey<ScaffoldState>();

  @override
  void initState() {
    super.initState();
    _api = widget.apiClient ?? HttpPhoneApiClient();
    _persistence = widget.persistence ?? _PlatformPhoneStatePersistence();
    WidgetsBinding.instance.addObserver(this);
    activeApiBase = _normalizeApiBase(
      widget.initialApiBase ?? compiledDefaultApiBase,
    );
    apiBaseController = TextEditingController(text: activeApiBase);
    accessTokenController = TextEditingController();
    wireGuardConfigController = TextEditingController(
      text: phoneWireGuardConfig,
    );
    noteEditController = TextEditingController();
    noteEditController.addListener(_handleNoteEditChanged);
    workspaceRootController = TextEditingController();
    workspaceRootController.addListener(_handleWorkspaceRootInputChanged);
    chatController = TextEditingController()..addListener(_captureDraft);
    noteReadScrollController = ScrollController()
      ..addListener(_saveActiveNoteReadScroll);
    noteEditScrollController = ScrollController()
      ..addListener(_saveActiveNoteEditScroll);
    if (Platform.isAndroid) {
      wireGuardChannel.setMethodCallHandler(_handlePhoneWireGuardEvent);
      unawaited(_refreshPhoneWireGuardStatus());
    }
    unawaited(_initializePhone());
  }

  Future<void> _initializePhone() async {
    await _loadPhoneSettings();
    try {
      _deviceState = PhoneDeviceState.fromJson(await _persistence.load());
    } catch (_) {
      _savedStateReadable = false;
      if (mounted) {
        setState(
          () => _storageWarning =
              'Saved phone work could not be loaded. Restart to try again; new actions are paused.',
        );
      }
    }
    if (!mounted) return;
    _deviceLoaded = true;
    _restoreSelectedConversation();
    await _refreshStatus();
  }

  void _captureDraft() {
    if (_restoringDraft || !_deviceLoaded) return;
    final key = PhoneDeviceState.draftKey(
      activeApiBase,
      activeWorkflowId,
      activeThreadId,
      workspaceRoot,
    );
    _deviceState.drafts[key] = _savingDrafts[key] == chatController.text
        ? ''
        : chatController.text;
    _draftSaveTimer?.cancel();
    _draftSaveTimer = Timer(const Duration(milliseconds: 250), () {
      unawaited(
        _persistDeviceState().catchError((Object error) {
          if (mounted) {
            setState(
              () => chatStatus = 'Draft could not be saved on this phone.',
            );
          }
        }),
      );
    });
  }

  void _restoreDraft() {
    _restoringDraft = true;
    chatController.text =
        _deviceState.drafts[PhoneDeviceState.draftKey(
          activeApiBase,
          activeWorkflowId,
          activeThreadId,
          workspaceRoot,
        )] ??
        '';
    _restoringDraft = false;
  }

  void _captureSelection() {
    _deviceState.selections[activeApiBase] = {
      'workflow_id': activeWorkflowId,
      'thread_id': activeThreadId,
      'workspace_root': workspaceRoot,
      'title': conversationTitle,
      'messages': chatMessages
          .skip(max(0, chatMessages.length - 160))
          .map(
            (message) => {
              'id': message.id,
              'role': message.role == PhoneChatRole.user ? 'user' : 'assistant',
              'content': message.text.length > 12000
                  ? '${message.text.substring(0, 12000)}\n\n[Connect to load the full message.]'
                  : message.text,
              'phone_status': message.status,
              if (message.record['timestamp'] != null)
                'timestamp': message.record['timestamp'],
              if (message.record['task_run_ref'] != null)
                'task_run_ref': message.record['task_run_ref'],
            },
          )
          .toList(),
      'execution_context': executionContext,
      'last_synced_at': lastSyncedAt?.toUtc().toIso8601String(),
      'attachments': composerAttachments
          .map((item) => item.toPayload())
          .toList(),
    };
  }

  Future<void> _persistDeviceState() {
    if (!_deviceLoaded) return Future<void>.value();
    if (!_savedStateReadable) {
      return Future<void>.error(
        StateError('Saved phone state is unavailable.'),
      );
    }
    _draftSaveTimer?.cancel();
    _captureSelection();
    // Capture before queueing; writes are serialized so a slow older save can
    // never replace a newer draft or delivery receipt.
    final snapshot =
        jsonDecode(jsonEncode(_deviceState.toJson())) as Map<String, dynamic>;
    final next = _saveChain
        .catchError((Object _) {})
        .then((_) => _persistence.save(snapshot));
    _saveChain = next;
    return next;
  }

  void _restoreSelectedConversation() {
    final saved = _deviceState.selections[activeApiBase];
    activeWorkflowId = _asString(saved?['workflow_id'], '_scratch');
    activeThreadId = _asString(saved?['thread_id'], '');
    workspaceRoot = _asString(saved?['workspace_root'], workspaceRoot);
    workspaceRootController.text = workspaceRoot;
    conversationTitle = _asString(saved?['title'], '');
    chatMessages = saved == null
        ? const []
        : _phoneMessagesFromThreadPayload(saved);
    executionContext = _recordValue(saved?['execution_context']) ?? {};
    lastSyncedAt = DateTime.tryParse(_asString(saved?['last_synced_at'], ''));
    composerAttachments =
        (saved?['attachments'] is List
                ? saved!['attachments'] as List
                : const [])
            .whereType<Map>()
            .map(
              (item) => PhoneAttachmentDraft(
                id: '${item['id'] ?? ''}',
                name: '${item['name'] ?? 'Screenshot'}',
                mimeType: '${item['mime_type'] ?? 'image/png'}',
                sizeBytes: _asInt(item['size_bytes']),
                dataUrl: '${item['data_url'] ?? ''}',
              ),
            )
            .where((item) => item.id.isNotEmpty && item.dataUrl.isNotEmpty)
            .toList();
    _restoreDraft();
    if (mounted) setState(() {});
  }

  void _invalidateLiveView() {
    _generation += 1;
    _pollSubscription += 1;
    agentPollTimer?.cancel();
    agentPollTimer = null;
    _pollFailures = 0;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
    _invalidateLiveView();
    if (_foreground && _deviceLoaded) {
      unawaited(_refreshStatus());
    } else if (_deviceLoaded) {
      unawaited(_persistDeviceState().catchError((Object _) {}));
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _invalidateLiveView();
    _draftSaveTimer?.cancel();
    apiBaseController.dispose();
    accessTokenController.dispose();
    wireGuardConfigController.dispose();
    noteEditController.dispose();
    workspaceRootController.dispose();
    chatController.dispose();
    noteReadScrollController.dispose();
    noteEditScrollController.dispose();
    agentPollTimer?.cancel();
    rootSuggestionDebounce?.cancel();
    noteAutosaveTimer?.cancel();
    super.dispose();
  }

  void _saveActiveNoteReadScroll() {
    final path = activeNote?.path;
    if (path == null || path.isEmpty || !noteReadScrollController.hasClients) {
      return;
    }
    noteReadScrollOffsets[path] = noteReadScrollController.offset;
  }

  void _saveActiveNoteEditScroll() {
    final path = activeNote?.path;
    if (path == null || path.isEmpty || !noteEditScrollController.hasClients) {
      return;
    }
    noteEditScrollOffsets[path] = noteEditScrollController.offset;
  }

  void _restoreNoteScroll(NotesPage page, String path) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || path.isEmpty) return;
      final controller = page == NotesPage.read
          ? noteReadScrollController
          : noteEditScrollController;
      final offset = page == NotesPage.read
          ? noteReadScrollOffsets[path] ?? 0
          : noteEditScrollOffsets[path] ?? 0;
      if (!controller.hasClients) return;
      final bounded = offset.clamp(0.0, controller.position.maxScrollExtent);
      controller.jumpTo(bounded);
    });
  }

  void _handleWorkspaceRootInputChanged() {
    rootSuggestionDebounce?.cancel();
    rootSuggestionDebounce = Timer(const Duration(milliseconds: 220), () {
      unawaited(_refreshWorkspaceRootSuggestions(workspaceRootController.text));
    });
  }

  Future<void> _refreshWorkspaceRootSuggestions(String query) async {
    if (!mounted || health == 'offline') return;
    setState(() => loadingWorkspaceRootSuggestions = true);
    final suggestions = await _fetchWorkspaceRootSuggestions(query);
    if (!mounted) return;
    setState(() {
      workspaceRootSuggestions = suggestions;
      loadingWorkspaceRootSuggestions = false;
    });
  }

  Future<List<WorkspaceRootSuggestion>> _fetchWorkspaceRootSuggestions(
    String query,
  ) async {
    final trimmed = query.trim();
    final suffix = trimmed.isEmpty
        ? ''
        : '?query=${Uri.encodeComponent(trimmed)}';
    final payload = await _getJson(
      activeApiBase,
      '/api/workspace-roots$suffix',
    );
    return (payload?['suggestions'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(WorkspaceRootSuggestion.fromJson)
        .where((item) => item.path.isNotEmpty)
        .toList();
  }

  String _sessionWorkspaceRootFor(PhoneSessionSummary session) {
    final taskRoot = phoneTasksByThread[session.id]?.workspaceRoot ?? '';
    if (taskRoot.isNotEmpty) return taskRoot;
    final boundRoot =
        sessionWorkspaceRoots[_sessionWorkspaceKey(
          session.workflowId,
          session.id,
        )] ??
        '';
    if (boundRoot.isNotEmpty) return boundRoot;
    return session.id == activeThreadId ? workspaceRoot : '';
  }

  List<PhoneWorkspaceSessionGroup> _buildSessionGroups() {
    final grouped = <String, List<PhoneSessionSummary>>{};
    final archived = <PhoneSessionSummary>[];
    for (final session in phoneSessions) {
      if (session.archived) {
        archived.add(session);
        continue;
      }
      final root = _sessionWorkspaceRootFor(session);
      grouped.putIfAbsent(root, () => []).add(session);
    }

    final roots = _mergeWorkspaceRoots(phoneWorkspaces, [
      if (workspaceRoot.trim().isNotEmpty) workspaceRoot.trim(),
      ...grouped.keys.where((root) => root.isNotEmpty),
    ]);
    final groups = <PhoneWorkspaceSessionGroup>[
      for (final workspace in roots)
        PhoneWorkspaceSessionGroup(
          root: workspace.root,
          name: workspace.name,
          sessions: grouped.remove(workspace.root) ?? const [],
          archived: false,
        ),
    ];
    if (grouped.containsKey('') && grouped['']!.isNotEmpty) {
      groups.add(
        PhoneWorkspaceSessionGroup(
          root: '',
          name: 'Project: Scratch',
          sessions: grouped.remove('')!,
          archived: false,
        ),
      );
    }
    for (final entry in grouped.entries) {
      if (entry.value.isEmpty) continue;
      groups.add(
        PhoneWorkspaceSessionGroup(
          root: entry.key,
          name: _workspaceDisplayName(entry.key),
          sessions: entry.value,
          archived: false,
        ),
      );
    }
    if (archived.isNotEmpty) {
      groups.add(
        PhoneWorkspaceSessionGroup(
          root: '',
          name: 'Archived',
          sessions: archived,
          archived: true,
        ),
      );
    }
    return groups;
  }

  Future<void> _refreshSessions() async {
    if (!mounted || health == 'offline' || loadingSessions) return;
    final base = activeApiBase;
    final epoch = _generation;
    setState(() => loadingSessions = true);
    final threadPayload = await _getJson(base, '/api/chats');
    final taskPayload = await _getJson(base, '/api/v2/tasks?limit=160');
    if (!mounted || base != activeApiBase || epoch != _generation) {
      if (mounted) setState(() => loadingSessions = false);
      return;
    }
    if (threadPayload == null || taskPayload == null) {
      setState(() => loadingSessions = false);
      return;
    }
    final sessions = (threadPayload['threads'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(PhoneSessionSummary.fromJson)
        .where((session) => session.id.isNotEmpty)
        .toList();
    final tasks = (taskPayload['tasks'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(PhoneTaskSummary.fromJson)
        .where((task) => task.threadId.isNotEmpty)
        .toList();
    final tasksByThread = <String, PhoneTaskSummary>{};
    for (final task in tasks) {
      tasksByThread.putIfAbsent(task.threadId, () => task);
    }
    final roots = [
      if (workspaceRoot.trim().isNotEmpty) workspaceRoot.trim(),
      ...tasks
          .map((task) => task.workspaceRoot)
          .where((root) => root.isNotEmpty),
      ...sessionWorkspaceRoots.values.where((root) => root.trim().isNotEmpty),
    ];
    setState(() {
      phoneSessions = sessions;
      phoneTasksByThread = tasksByThread;
      phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, roots);
      loadingSessions = false;
    });
    unawaited(_savePhoneSettings());
  }

  List<PhoneChatMessage> _phoneMessagesFromThreadPayload(
    Map<String, dynamic>? payload,
  ) {
    final messages = payload?['messages'];
    if (messages is! List<dynamic>) return const [];
    final parsed = <PhoneChatMessage>[];
    for (final item in messages.whereType<Map<String, dynamic>>()) {
      final role = _asString(item['role'], '');
      final text = _asString(item['content'], '');
      if (text.isEmpty) continue;
      final phoneRole = switch (role) {
        'user' => PhoneChatRole.user,
        'assistant' => PhoneChatRole.assistant,
        _ => PhoneChatRole.status,
      };
      if (phoneRole == PhoneChatRole.status) continue;
      final taskRef = _recordValue(item['task_run_ref']);
      parsed.add(
        PhoneChatMessage(
          id: _asString(item['id'], _newId('history')),
          role: phoneRole,
          text: text,
          status: _asString(
            item['phone_status'],
            _asString(taskRef?['status'], ''),
          ),
          record: Map<String, dynamic>.from(item),
        ),
      );
    }
    return parsed;
  }

  Future<void> _selectWorkspaceRootFromDrawer(String root) async {
    final nextRoot = root.trim();
    setState(() {
      workspaceRoot = nextRoot;
      workspaceRootController.text = nextRoot;
      phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [nextRoot]);
      mode = WorkspaceMode.work;
      workPage = WorkPage.files;
      _phoneTab = 2;
      _libraryTab = 0;
      fileStatus = nextRoot.isEmpty
          ? 'Loading default workspace'
          : 'Loading $nextRoot';
    });
    await _savePhoneSettings();
    await _refreshWorkspaceFiles(root: nextRoot);
  }

  Future<void> _selectPhoneSession(
    PhoneSessionSummary session, {
    String workspaceRootOverride = '',
  }) async {
    if (session.archived) {
      setState(
        () =>
            chatStatus = 'Restore this archived conversation on the computer.',
      );
      return;
    }
    _captureDraft();
    _captureSelection();
    _invalidateLiveView();
    final epoch = _generation;
    final root = workspaceRootOverride.isNotEmpty
        ? workspaceRootOverride
        : _sessionWorkspaceRootFor(session);
    setState(() {
      activeWorkflowId = session.workflowId;
      activeThreadId = session.id;
      conversationTitle = session.displayTitle;
      chatBusy = false;
      activeTaskId = '';
      activeRunId = '';
      activeRunLive = false;
      pendingApprovals = const [];
      executionContext = {};
      chatMessages = const [];
      composerAttachments = const [];
      previewArtifacts = const [];
      promptLogPath = '';
      promptLogEntryCount = 0;
      chatStatus = 'Connecting to conversation';
      _phoneTab = 0;
      mode = WorkspaceMode.work;
      workPage = WorkPage.chat;
      workspaceRoot = root;
      workspaceRootController.text = root;
    });
    _restoreDraft();
    await _persistDeviceState().catchError((Object _) {});
    if (!mounted || epoch != _generation) return;
    _startSessionPolling();
  }

  Future<void> _createSessionForWorkspace(String root) async {
    _captureDraft();
    _captureSelection();
    _invalidateLiveView();
    setState(() {
      activeWorkflowId = '_scratch';
      activeThreadId = '';
      conversationTitle = '';
      chatBusy = false;
      activeRunId = '';
      activeTaskId = '';
      activeRunLive = false;
      pendingApprovals = const [];
      executionContext = {};
      chatMessages = const [];
      composerAttachments = const [];
      previewArtifacts = const [];
      workspaceRoot = root.trim();
      workspaceRootController.text = workspaceRoot;
      phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [workspaceRoot]);
      chatStatus = 'Ready';
      _phoneTab = 0;
      mode = WorkspaceMode.work;
      workPage = WorkPage.chat;
    });
    _restoreDraft();
    await _persistDeviceState().catchError((Object _) {});
    // A server conversation is created idempotently with the first delivered
    // request. Offline capture therefore never creates duplicate empty chats.
    _startSessionPolling();
  }

  Future<void> _openNewWorkspaceDialog() async {
    final controller = TextEditingController(
      text: workspaceRootController.text.trim(),
    );
    var suggestions = workspaceRootSuggestions;
    Timer? debounce;
    try {
      await showDialog<void>(
        context: context,
        builder: (context) {
          return StatefulBuilder(
            builder: (context, setDialogState) {
              Future<void> refreshSuggestions(String value) async {
                final next = await _fetchWorkspaceRootSuggestions(value);
                if (context.mounted) {
                  setDialogState(() => suggestions = next);
                }
              }

              void scheduleSuggestions(String value) {
                debounce?.cancel();
                debounce = Timer(
                  const Duration(milliseconds: 220),
                  () => unawaited(refreshSuggestions(value)),
                );
              }

              Future<void> createWorkspace(String value) async {
                final root = value.trim();
                if (root.isEmpty) return;
                Navigator.of(context).pop();
                await _selectWorkspaceRootFromDrawer(root);
                unawaited(_refreshSessions());
              }

              return AlertDialog(
                title: const Text('New workspace'),
                content: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      TextField(
                        controller: controller,
                        onChanged: scheduleSuggestions,
                        onSubmitted: (value) =>
                            unawaited(createWorkspace(value)),
                        decoration: const InputDecoration(
                          labelText: 'Workspace root',
                          hintText: '/path/to/project',
                        ),
                      ),
                      if (suggestions.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        DecoratedBox(
                          decoration: BoxDecoration(
                            color: Colors.white,
                            border: Border.all(color: const Color(0xffded7ca)),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Column(
                            children: [
                              for (final suggestion in suggestions.take(5))
                                ListTile(
                                  dense: true,
                                  leading: const Icon(Icons.folder_outlined),
                                  title: Text(
                                    suggestion.label.isEmpty
                                        ? suggestion.path
                                        : suggestion.label,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                  subtitle: Text(
                                    suggestion.path,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                  onTap: () => unawaited(
                                    createWorkspace(suggestion.path),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('Cancel'),
                  ),
                  FilledButton.icon(
                    onPressed: () =>
                        unawaited(createWorkspace(controller.text)),
                    icon: const Icon(Icons.add),
                    label: const Text('Create'),
                  ),
                ],
              );
            },
          );
        },
      );
    } finally {
      debounce?.cancel();
      controller.dispose();
    }
  }

  void _handleNoteEditChanged() {
    if (suppressNoteEditListener) return;
    final note = activeNote;
    if (note == null || note.path.isEmpty) return;
    noteAutosaveTimer?.cancel();
    noteAutosaveTimer = Timer(const Duration(seconds: 2), () {
      unawaited(_saveActiveNote());
    });
    if (mounted && noteSaveStatus != 'Editing') {
      setState(() => noteSaveStatus = 'Editing');
    }
  }

  Future<void> _saveActiveNote() async {
    final note = activeNote;
    if (note == null || note.path.isEmpty || noteSaving) return;
    setState(() {
      noteSaving = true;
      noteSaveStatus = 'Saving';
    });
    final content = noteEditController.text;
    final payload = await _requestJson(
      activeApiBase,
      '/api/workspace-notes/write',
      method: 'PUT',
      body: {'path': note.path, 'content': content},
      timeout: const Duration(seconds: 8),
    );
    if (!mounted) return;
    if (payload == null) {
      setState(() {
        noteSaving = false;
        noteSaveStatus = 'Save failed';
      });
      return;
    }
    setState(() {
      noteContent = content;
      noteSaving = false;
      noteSaveStatus = 'Saved';
      notePreviewStatus = 'Compiling preview';
    });
    unawaited(_refreshNotePreview(note));
  }

  Future<void> _loadPhoneSettings() async {
    if (!Platform.isAndroid) return;
    try {
      final result = await wireGuardChannel.invokeMethod<Object?>(
        'loadSettings',
      );
      if (!mounted || result is! Map<Object?, Object?>) return;
      final savedApiBase = _asString(result['apiBase'], '');
      final savedToken = _normalizeAccessToken(
        _asString(result['accessToken'], ''),
      );
      final savedWireGuardConfig = _asString(result['wireGuardConfig'], '');
      final savedWorkspaceRoot = _asString(result['workspaceRoot'], '');
      final savedRunMode = _asString(result['runMode'], '');
      final savedWorkspaceRoots = _decodeStringListSetting(
        _asString(result['workspaceRoots'], ''),
      );
      final savedSessionWorkspaceRoots = _decodeStringMapSetting(
        _asString(result['sessionWorkspaceRoots'], ''),
      );
      setState(() {
        if (savedApiBase.isNotEmpty) {
          activeApiBase = _normalizeApiBase(savedApiBase);
          apiBaseController.text = activeApiBase;
        }
        if (savedWorkspaceRoot.isNotEmpty) {
          workspaceRoot = savedWorkspaceRoot;
          workspaceRootController.text = savedWorkspaceRoot;
        }
        accessToken = savedToken;
        accessTokenController.text = savedToken;
        if (savedWireGuardConfig.isNotEmpty) {
          phoneWireGuardConfig = savedWireGuardConfig;
          wireGuardConfigController.text = savedWireGuardConfig;
          if (phoneWireGuardDetail == 'No Dear Diane phone VPN config loaded') {
            phoneWireGuardDetail = 'Dear Diane phone VPN config loaded';
          }
        }
        if (savedRunMode == 'review') {
          runMode = WorkRunMode.review;
        } else if (savedRunMode == 'auto') {
          runMode = WorkRunMode.auto;
        }
        phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [
          ...savedWorkspaceRoots,
          if (savedWorkspaceRoot.isNotEmpty) savedWorkspaceRoot,
        ]);
        sessionWorkspaceRoots = savedSessionWorkspaceRoots;
      });
    } on MissingPluginException {
      return;
    } on PlatformException {
      return;
    }
  }

  Future<void> _savePhoneSettings() async {
    phoneWireGuardConfig = wireGuardConfigController.text.trim();
    final workspaceRoots = _mergeWorkspaceRoots(phoneWorkspaces, [
      if (workspaceRoot.trim().isNotEmpty) workspaceRoot.trim(),
    ]).map((workspace) => workspace.root).toList();
    if (!Platform.isAndroid) return;
    try {
      await wireGuardChannel.invokeMethod<Object?>('saveSettings', {
        'apiBase': activeApiBase,
        'accessToken': accessToken,
        'wireGuardConfig': phoneWireGuardConfig,
        'workspaceRoot': workspaceRoot,
        'runMode': runMode.name,
        'workspaceRoots': jsonEncode(workspaceRoots),
        'sessionWorkspaceRoots': jsonEncode(sessionWorkspaceRoots),
      });
    } on MissingPluginException {
      return;
    } on PlatformException {
      return;
    }
  }

  Future<void> _refreshStatus({String? preferredApiBase}) async {
    if (!mounted || !_foreground) return;
    final requestedBase = _normalizeApiBase(preferredApiBase ?? activeApiBase);
    if (requestedBase != activeApiBase) {
      await _changeComputer(requestedBase);
      return;
    }
    final connectionEpoch = ++_connectionGeneration;
    final epoch = _generation;
    setState(() {
      loadingWireGuard = true;
      health = 'checking';
      statusDetail = 'Connecting to your computer';
    });
    try {
      await _preparePhoneWireGuardFor([
        requestedBase,
      ]).timeout(const Duration(seconds: 4), onTimeout: () {});
      await _api.request(
        requestedBase,
        '/api/health',
        token: accessToken,
        timeout: const Duration(seconds: 8),
      );
      if (!mounted ||
          epoch != _generation ||
          connectionEpoch != _connectionGeneration ||
          requestedBase != activeApiBase) {
        return;
      }
      setState(() {
        health = 'ready';
        lastSyncedAt = DateTime.now();
        statusDetail = 'Connected';
      });
      unawaited(_refreshSessions());
      unawaited(_refreshNotes(requestedBase));
      unawaited(_refreshWorkspaceFiles(apiBase: requestedBase));
      _startSessionPolling();
      unawaited(_flushOutbox());
    } catch (error) {
      if (!mounted ||
          epoch != _generation ||
          connectionEpoch != _connectionGeneration) {
        return;
      }
      setState(() {
        health = 'offline';
        statusDetail = error.toString();
        loadingNotes = false;
        noteStatus = 'Computer is offline';
      });
      _startSessionPolling();
    } finally {
      if (mounted &&
          epoch == _generation &&
          connectionEpoch == _connectionGeneration) {
        setState(() => loadingWireGuard = false);
      }
    }
  }

  Future<void> _changeComputer(String base) async {
    _captureDraft();
    await _persistDeviceState().catchError((Object _) {});
    _invalidateLiveView();
    ++_connectionGeneration;
    setState(() {
      activeApiBase = base;
      chatBusy = false;
      apiBaseController.text = base;
      phoneSessions = const [];
      phoneTasksByThread = const {};
      phoneWorkspaces = const [];
      pendingApprovals = const [];
      previewArtifacts = const [];
      activeRunId = '';
      activeTaskId = '';
      activeRunLive = false;
      workspaceFiles = const [];
      workspaceNotes = const [];
      activeNote = null;
      activeFile = null;
      notePreviewCache.clear();
      lastSyncedAt = null;
      health = 'checking';
      workspaceRoot = '';
      workspaceRootController.clear();
    });
    _restoreSelectedConversation();
    await _savePhoneSettings();
    await _refreshStatus();
  }

  String _normalizeApiBase(String value) {
    var text = value.trim();
    if (text.isEmpty) return compiledDefaultApiBase;
    if (!text.startsWith('http://') && !text.startsWith('https://')) {
      text = 'http://$text';
    }
    while (text.endsWith('/')) {
      text = text.substring(0, text.length - 1);
    }
    return text;
  }

  Future<Map<String, dynamic>?> _getJson(String base, String path) async {
    return _requestJson(base, path);
  }

  Future<Map<String, dynamic>?> _requestJson(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    Duration timeout = const Duration(seconds: 10),
  }) async {
    // Legacy file/note readers use a nullable result. Work-changing commands
    // exclusively use the typed, receipt-backed delivery path below.
    try {
      if (base != activeApiBase) return null;
      final response = await _api.request(
        base,
        path,
        method: method,
        body: body,
        token: accessToken,
        timeout: timeout,
      );
      return base == activeApiBase ? response : null;
    } on PhoneApiFailure {
      return null;
    }
  }

  Future<void> _refreshNotes([String? apiBase]) async {
    final base = _normalizeApiBase(apiBase ?? activeApiBase);
    if (!mounted) return;
    setState(() {
      loadingNotes = true;
      noteStatus = 'Loading knowledge base';
    });
    final payload = await _getJson(base, '/api/workspace-notes?limit=500');
    if (!mounted) return;
    if (payload == null) {
      setState(() {
        loadingNotes = false;
        noteStatus = 'Could not load knowledge-base pages';
      });
      return;
    }
    final notes = (payload['notes'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(WorkspaceNoteSummary.fromJson)
        .where((note) => note.path.isNotEmpty)
        .toList();
    final currentPath = activeNote?.path;
    final nextActive = notes.firstWhere(
      (note) => note.path == currentPath,
      orElse: () => notes.isNotEmpty
          ? notes.first
          : const WorkspaceNoteSummary(
              path: '',
              relativePath: '',
              title: 'No pages found',
              section: '',
              tags: [],
              categories: [],
              pageId: '',
            ),
    );
    setState(() {
      workspaceNotes = notes;
      notesRoot = _asString(payload['root'], '');
      activeNote = nextActive.path.isEmpty ? null : nextActive;
      loadingNotes = false;
      noteStatus = notes.isEmpty
          ? 'No knowledge-base pages found'
          : '${notes.length} pages loaded';
    });
    if (nextActive.path.isNotEmpty) {
      unawaited(_selectNote(nextActive, base: base, switchToRead: false));
    }
  }

  Future<void> _selectNote(
    WorkspaceNoteSummary note, {
    String? base,
    bool switchToRead = true,
  }) async {
    if (!mounted || note.path.isEmpty) return;
    _saveActiveNoteReadScroll();
    _saveActiveNoteEditScroll();
    setState(() {
      activeNote = note;
      noteStatus = 'Loading ${note.title}';
      notePreviewStatus = notePreviewCache.containsKey(note.path)
          ? 'Cached preview'
          : 'Compiling preview';
      if (switchToRead) {
        mode = WorkspaceMode.notes;
        notesPage = NotesPage.read;
      }
    });
    final payload = await _getJson(
      _normalizeApiBase(base ?? activeApiBase),
      '/api/workspace-notes/read?path=${Uri.encodeComponent(note.path)}',
    );
    if (!mounted) return;
    if (payload == null) {
      setState(() {
        noteStatus = 'Could not read ${note.title}';
      });
      return;
    }
    final content = _asString(payload['content'], '');
    suppressNoteEditListener = true;
    noteEditController.text = content;
    suppressNoteEditListener = false;
    setState(() {
      noteContent = content;
      noteSaveStatus = 'Clean';
      noteStatus = note.relativePath.isNotEmpty
          ? note.relativePath
          : note.title;
    });
    _restoreNoteScroll(switchToRead ? NotesPage.read : notesPage, note.path);
    unawaited(_refreshNotePreview(note, base: base));
  }

  Future<void> _refreshNotePreview(
    WorkspaceNoteSummary note, {
    String? base,
  }) async {
    if (note.path.isEmpty) return;
    final payload = await _getJson(
      _normalizeApiBase(base ?? activeApiBase),
      '/api/workspace-notes/preview?path=${Uri.encodeComponent(note.path)}',
    );
    if (!mounted || payload == null) {
      if (mounted) {
        setState(() => notePreviewStatus = 'Preview compile failed');
      }
      return;
    }
    final preview = WorkspaceNotePreview.fromJson(payload);
    setState(() {
      notePreviewCache[preview.path] = preview;
      notePreviewStatus =
          'Live preview · ${preview.compiler}${preview.routePath.isNotEmpty ? ' · ${preview.routePath}' : ''}';
    });
    if (activeNote?.path == preview.path) {
      _restoreNoteScroll(NotesPage.read, preview.path);
    }
  }

  Future<void> _applyWorkspaceRoot([String? value]) async {
    final nextRoot = (value ?? workspaceRootController.text).trim();
    setState(() {
      workspaceRoot = nextRoot;
      workspaceRootController.text = nextRoot;
      phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [nextRoot]);
      fileStatus = 'Loading workspace';
    });
    await _savePhoneSettings();
    await _refreshWorkspaceFiles(root: nextRoot);
    unawaited(_refreshSessions());
  }

  Future<void> _refreshWorkspaceFiles({String? apiBase, String? root}) async {
    final base = _normalizeApiBase(apiBase ?? activeApiBase);
    final requestedRoot = (root ?? workspaceRoot).trim();
    if (!mounted || health == 'offline') return;
    setState(() {
      loadingFiles = true;
      fileStatus = requestedRoot.isEmpty
          ? 'Loading default workspace'
          : 'Loading $requestedRoot';
    });
    final suffix = requestedRoot.isEmpty
        ? ''
        : '?root_path=${Uri.encodeComponent(requestedRoot)}';
    final payload = await _getJson(base, '/api/workspace-files$suffix');
    if (!mounted) return;
    if (payload == null) {
      setState(() {
        loadingFiles = false;
        fileStatus = 'Could not load workspace files';
      });
      return;
    }
    final entries = (payload['entries'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(WorkspaceFileEntry.fromJson)
        .where((entry) => entry.path.isNotEmpty)
        .toList();
    final resolvedRoot = _asString(payload['root'], requestedRoot);
    setState(() {
      workspaceRoot = resolvedRoot;
      workspaceRootController.text = resolvedRoot;
      phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [resolvedRoot]);
      workspaceFiles = entries;
      loadingFiles = false;
      fileStatus = entries.isEmpty
          ? 'No files found in $resolvedRoot'
          : '${entries.length} files loaded';
    });
    if (Platform.isAndroid) unawaited(_savePhoneSettings());
  }

  Future<void> _selectWorkspaceFile(WorkspaceFileEntry entry) async {
    if (entry.isDirectory) {
      await _applyWorkspaceRoot(entry.path);
      return;
    }
    setState(() {
      activeFile = entry;
      activePreviewArtifact = null;
      activeFileContent = '';
      previewTitle = entry.relativePath;
      previewStatus = 'Reading ${entry.relativePath}';
      previewContent = '';
      fileStatus = 'Reading ${entry.relativePath}';
      mode = WorkspaceMode.work;
      workPage = WorkPage.preview;
      _phoneTab = 2;
      _libraryTab = 2;
    });
    final payload = await _getJson(
      activeApiBase,
      '/api/workspace-files/read?path=${Uri.encodeComponent(entry.path)}&root_path=${Uri.encodeComponent(workspaceRoot)}',
    );
    if (!mounted) return;
    if (payload == null) {
      setState(() => fileStatus = 'Could not read ${entry.relativePath}');
      return;
    }
    setState(() {
      activeFileContent = _asString(payload['content'], '');
      previewTitle = entry.relativePath;
      previewStatus = entry.relativePath;
      previewContent = activeFileContent;
      fileStatus = entry.relativePath;
    });
  }

  Future<void> _selectPreviewArtifact(PhonePreviewArtifact artifact) async {
    setState(() {
      activePreviewArtifact = artifact;
      activeFile = null;
      activeFileContent = '';
      previewTitle = artifact.title;
      previewStatus = artifact.path.isNotEmpty ? artifact.path : artifact.url;
      previewContent = 'Loading output artifact...';
      mode = WorkspaceMode.work;
      workPage = WorkPage.preview;
      _phoneTab = 2;
      _libraryTab = 2;
    });
    if (artifact.path.isEmpty) {
      setState(() {
        previewContent =
            'Remote artifact\n\n${artifact.url}\n\nOpen this URL from a browser-capable surface.';
      });
      return;
    }
    final payload = await _getJson(
      activeApiBase,
      '/api/workspace-files/read?path=${Uri.encodeComponent(artifact.path)}&root_path=${Uri.encodeComponent(workspaceRoot)}',
    );
    if (!mounted) return;
    setState(() {
      previewContent = _asString(
        payload?['content'],
        'Artifact recorded at:\n${artifact.path}\n\nThis output could not be read as text from the current workspace root.',
      );
      previewStatus = artifact.path;
    });
  }

  Future<void> _openPromptLogPreview() async {
    if (activeThreadId.isEmpty || loadingPromptLog) {
      setState(() {
        chatStatus = activeThreadId.isEmpty
            ? 'Start a Work session before opening prompt logs'
            : 'Prompt log is already loading';
      });
      return;
    }
    setState(() {
      loadingPromptLog = true;
      activePreviewArtifact = null;
      activeFile = null;
      activeFileContent = '';
      previewTitle = 'Prompt log';
      previewStatus = 'Loading prompt log';
      previewContent = '';
      mode = WorkspaceMode.work;
      workPage = WorkPage.preview;
      _phoneTab = 2;
      _libraryTab = 2;
    });
    final payload = await _getJson(
      activeApiBase,
      '/api/v2/threads/${Uri.encodeComponent(activeThreadId)}/prompt-log',
    );
    if (!mounted) return;
    setState(() {
      loadingPromptLog = false;
      promptLogEntryCount = _asInt(payload?['entry_count']);
      promptLogPath = _asString(payload?['path'], '');
      previewStatus = payload == null
          ? 'Could not load prompt log'
          : '$promptLogEntryCount model ${promptLogEntryCount == 1 ? 'call' : 'calls'} recorded';
      previewContent = _asString(
        payload?['content'],
        'No prompt log content was returned.',
      );
      if (promptLogPath.isNotEmpty) {
        previewStatus = '$previewStatus\n$promptLogPath';
      }
    });
  }

  String _newId(String prefix) {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    final hex = bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
    return '$prefix-${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
  }

  Future<void> _sendChatMessage({String? action}) async {
    final draftText = chatController.text;
    final typed = draftText.trim();
    final attachments = List<PhoneAttachmentDraft>.from(composerAttachments);
    if ((typed.isEmpty && attachments.isEmpty) || chatBusy) return;
    if (activeThreadId.isEmpty &&
        _deliveries.any((item) => item.threadId.isEmpty && item.isPending)) {
      setState(
        () => chatStatus =
            'Your first message is saved. Deliver it before adding another message to this conversation.',
      );
      return;
    }
    final text = typed.isNotEmpty
        ? typed
        : 'Please review the attached screenshot${attachments.length > 1 ? 's' : ''}.';
    final id = _newId('request');
    final messageId = _newId('message');
    final kind = action ?? (activeRunLive ? 'steer' : 'send');
    final item = PhoneDeliveryItem(
      id: id,
      kind: kind,
      text: text,
      apiBase: activeApiBase,
      workflowId: activeWorkflowId,
      threadId: activeThreadId,
      runId: activeRunId,
      createdAt: DateTime.now(),
      payload: {
        'request_id': id,
        'action': kind,
        'workflow_id': activeWorkflowId,
        'thread_id': activeThreadId,
        'message_id': messageId,
        'text': text,
        if (kind == 'steer' || kind == 'queue') 'run_id': activeRunId,
        if (activeThreadId.isEmpty && workspaceRoot.isNotEmpty)
          'workspace_root': workspaceRoot,
        'attachments': attachments
            .map((attachment) => attachment.toPayload())
            .toList(),
      },
    );
    final originEpoch = _generation;
    final originBase = activeApiBase;
    final originThread = activeThreadId;
    bool originVisible() =>
        mounted &&
        originEpoch == _generation &&
        originBase == activeApiBase &&
        originThread == activeThreadId;
    final draftKey = PhoneDeviceState.draftKey(
      activeApiBase,
      activeWorkflowId,
      activeThreadId,
      workspaceRoot,
    );
    _savingDrafts[draftKey] = draftText;
    _deviceState.drafts[draftKey] = '';
    _deviceState.deliveries.add(item);
    setState(() {
      chatBusy = true;
      chatMessages = [
        ...chatMessages,
        PhoneChatMessage(
          id: messageId,
          role: PhoneChatRole.user,
          text: text,
          status: item.statusLabel,
          record: {
            'id': messageId,
            'role': 'user',
            'content': text,
            'attachments': attachments
                .map((attachment) => attachment.toPayload())
                .toList(),
            'timestamp': DateTime.now().toUtc().toIso8601String(),
          },
        ),
      ];
      chatStatus = 'Saving on this phone';
    });
    try {
      await _persistDeviceState();
    } catch (_) {
      _deviceState.deliveries.removeWhere((value) => value.id == id);
      _savingDrafts.remove(draftKey);
      if ((_deviceState.drafts[draftKey] ?? '').isEmpty) {
        _deviceState.drafts[draftKey] = draftText;
      }
      if (originVisible()) {
        setState(() {
          chatMessages = chatMessages
              .where((message) => message.id != messageId)
              .toList();
          chatBusy = false;
          chatStatus =
              'Could not save this message. Your draft is still here; nothing was sent.';
        });
      }
      unawaited(_persistDeviceState().catchError((Object _) {}));
      return;
    }
    _savingDrafts.remove(draftKey);
    if (!originVisible()) {
      if (mounted && originBase == activeApiBase && health == 'ready') {
        unawaited(_deliverRequest(id, originEpoch: originEpoch));
      }
      return;
    }
    if (chatController.text == draftText) {
      _restoringDraft = true;
      chatController.clear();
      _restoringDraft = false;
    }
    setState(() {
      final sentAttachmentIds = attachments
          .map((attachment) => attachment.id)
          .toSet();
      composerAttachments = composerAttachments
          .where((attachment) => !sentAttachmentIds.contains(attachment.id))
          .toList();
      chatBusy = false;
      chatStatus = health == 'ready'
          ? 'Waiting for acknowledgement'
          : 'Saved on this phone. Will send when reconnected.';
    });
    if (health == 'ready') {
      await _deliverRequest(id);
    } else {
      _startSessionPolling();
    }
  }

  Future<void> _queueChatMessage() =>
      _sendChatMessage(action: activeRunLive ? 'queue' : 'send');

  void _updateDelivery(PhoneDeliveryItem item) {
    final index = _deviceState.deliveries.indexWhere(
      (existing) => existing.id == item.id,
    );
    if (index < 0) return;
    _deviceState.deliveries[index] = item;
    if (mounted) setState(() {});
  }

  Future<void> _retryDelivery(String id) async {
    final candidates = _deviceState.deliveries.where((item) => item.id == id);
    if (candidates.isEmpty || !candidates.first.canRetry) return;
    await _deliverRequest(id);
  }

  Future<void> _deliverRequest(String id, {int? originEpoch}) async {
    if (_delivering.contains(id) || !_foreground) return;
    final candidates = _deviceState.deliveries.where((item) => item.id == id);
    if (candidates.isEmpty) return;
    var item = candidates.first;
    if (item.apiBase != activeApiBase || !item.canRetry) return;
    final base = item.apiBase;
    final token = accessToken;
    final epoch = originEpoch ?? _generation;
    final originMatches = activeThreadId == item.threadId;
    final wasUncertain = item.status == 'uncertain';
    var awaitingReconciliation = wasUncertain;
    _delivering.add(id);
    _updateDelivery(item.copyWith(status: 'sending', error: ''));
    try {
      await _persistDeviceState();
      Map<String, dynamic>? receipt;
      if (wasUncertain) {
        try {
          receipt = await _api.request(
            base,
            '/api/phone/requests/${Uri.encodeComponent(id)}',
            token: token,
          );
          awaitingReconciliation = false;
        } on PhoneApiFailure catch (failure) {
          if (failure.statusCode != 404) rethrow;
          awaitingReconciliation = false;
        }
      }
      receipt ??= await _api.request(
        base,
        '/api/phone/requests',
        method: 'POST',
        body: item.payload,
        token: token,
        timeout: const Duration(seconds: 25),
      );
      final deliveryState = phoneReceiptDeliveryStatus(receipt);
      if (deliveryState != 'accepted') {
        _updateDelivery(
          item.copyWith(
            status: deliveryState,
            error: _asString(
              receipt['summary'],
              deliveryState == 'needs_review'
                  ? 'Review this request on your computer before taking another action.'
                  : deliveryState == 'expired' || deliveryState == 'rejected'
                  ? 'This action was not applied. Refresh to see the current state.'
                  : 'The computer has not confirmed this action.',
            ),
          ),
        );
      } else {
        final thread = _asString(receipt['thread_id'], item.threadId);
        final workflow = _asString(receipt['workflow_id'], item.workflowId);
        item = item.copyWith(
          status: 'accepted',
          error: '',
          threadId: thread,
          workflowId: workflow,
          runId: _asString(receipt['run_id'], item.runId),
        );
        _updateDelivery(item);
        // The envelope in item.payload remains immutable. In particular, a
        // replay of an initially blank thread retains the original body.
        if (mounted &&
            epoch == _generation &&
            base == activeApiBase &&
            (activeThreadId == thread ||
                (activeThreadId.isEmpty && originMatches))) {
          final previousDraftKey = PhoneDeviceState.draftKey(
            base,
            activeWorkflowId,
            activeThreadId,
            workspaceRoot,
          );
          final currentDraft = chatController.text;
          setState(() {
            activeThreadId = thread;
            activeWorkflowId = workflow;
            // A completed control receipt means the decision/message was
            // delivered. Canonical session state determines whether its run
            // has finished, including when a newer run has already begun.
            if (item.kind == 'send') {
              activeRunId = item.runId;
              activeTaskId = _asString(receipt!['task_id'], activeTaskId);
              final state = _asString(receipt['status'], 'accepted');
              activeRunLive =
                  ![
                    'completed',
                    'failed',
                    'stopped',
                    'interrupted',
                  ].contains(state) &&
                  activeRunId.isNotEmpty;
            }
            chatStatus = _asString(
              receipt!['summary'],
              'Accepted by your computer',
            );
            health = 'ready';
            lastSyncedAt = DateTime.now();
            final root = _asString(receipt['workspace_root'], workspaceRoot);
            workspaceRoot = root;
            workspaceRootController.text = root;
          });
          if (item.payload['thread_id'] == '') {
            _deviceState.drafts[previousDraftKey] = '';
            _deviceState.drafts[PhoneDeviceState.draftKey(
                  base,
                  activeWorkflowId,
                  activeThreadId,
                  workspaceRoot,
                )] =
                currentDraft;
          }
          _startSessionPolling();
          unawaited(_refreshSessions());
        }
      }
    } on PhoneApiFailure catch (failure) {
      final staleAction =
          !awaitingReconciliation &&
          ['answer', 'stop', 'steer', 'queue'].contains(item.kind) &&
          failure.isExpired;
      final uncertain =
          awaitingReconciliation ||
          failure.uncertain ||
          (failure.statusCode ?? 0) >= 500;
      _updateDelivery(
        item.copyWith(
          status: failure.code == 'receipt_needs_review'
              ? 'needs_review'
              : staleAction
              ? 'expired'
              : uncertain
              ? 'uncertain'
              : 'failed',
          error: failure.message,
        ),
      );
      if (mounted && epoch == _generation && base == activeApiBase) {
        setState(() {
          chatStatus = staleAction
              ? 'This action is no longer available. Refresh to see the current state.'
              : failure.message;
          if (failure.statusCode == null) health = 'offline';
        });
      }
    } catch (_) {
      _updateDelivery(
        item.copyWith(
          status: 'uncertain',
          error: 'Delivery is unconfirmed. Retry safely with the same request.',
        ),
      );
    } finally {
      _delivering.remove(id);
      await _persistDeviceState().catchError((Object _) {
        if (mounted) {
          setState(
            () => chatStatus =
                'Phone storage is unavailable. Reconnect to check delivery.',
          );
        }
      });
    }
  }

  Future<void> _flushOutbox() async {
    if (_flushing || !_foreground || health != 'ready') return;
    _flushing = true;
    final base = activeApiBase;
    try {
      final pending = _deviceState.deliveries
          .where(
            (item) =>
                item.apiBase == base &&
                ['queued', 'uncertain'].contains(item.status),
          )
          .map((item) => item.id)
          .toList();
      for (final id in pending) {
        if (!mounted ||
            !_foreground ||
            base != activeApiBase ||
            health != 'ready') {
          break;
        }
        await _deliverRequest(id);
        final current = _deviceState.deliveries
            .where((item) => item.id == id)
            .first;
        if (current.status == 'uncertain') break;
      }
    } finally {
      _flushing = false;
    }
  }

  Future<void> _submitControl(
    String kind,
    String text,
    Map<String, Object?> fields,
  ) async {
    final originEpoch = _generation;
    final originBase = activeApiBase;
    final id = _newId('request');
    final item = PhoneDeliveryItem(
      id: id,
      kind: kind,
      text: text,
      apiBase: activeApiBase,
      workflowId: activeWorkflowId,
      threadId: activeThreadId,
      runId: activeRunId,
      createdAt: DateTime.now(),
      payload: {
        'request_id': id,
        'action': kind,
        'workflow_id': activeWorkflowId,
        'thread_id': activeThreadId,
        'run_id': activeRunId,
        ...fields,
      },
    );
    _deviceState.deliveries.add(item);
    try {
      await _persistDeviceState();
    } catch (_) {
      _deviceState.deliveries.removeWhere((value) => value.id == id);
      if (mounted &&
          originEpoch == _generation &&
          originBase == activeApiBase) {
        setState(
          () => chatStatus = 'Could not save this action. Nothing was sent.',
        );
      }
      return;
    }
    if (mounted && originEpoch == _generation && originBase == activeApiBase) {
      setState(
        () => chatStatus = 'Action saved; awaiting computer acknowledgement',
      );
    }
    if (health == 'ready') await _deliverRequest(id, originEpoch: originEpoch);
  }

  Future<void> _replyToApproval(
    PhoneApprovalRequest request,
    Map<String, Object?> body,
  ) async {
    if (!request.canAnswer || request.runId != activeRunId) return;
    final duplicate = _deviceState.deliveries.any(
      (item) =>
          item.apiBase == activeApiBase &&
          item.kind == 'answer' &&
          item.payload['approval_request_id'] == request.id &&
          '${item.payload['worker_id'] ?? ''}' == request.workerId &&
          item.runId == request.runId &&
          !['expired', 'failed'].contains(item.status),
    );
    if (duplicate) return;
    setState(() {
      pendingApprovals = pendingApprovals
          .map(
            (item) => item.key == request.key
                ? item.copyWith(status: 'sending')
                : item,
          )
          .toList();
    });
    await _submitControl(
      'answer',
      request.isQuestion
          ? 'Answer Diane’s question'
          : 'Respond to permission request',
      {
        'run_id': request.runId,
        if (request.workerId.isNotEmpty) 'worker_id': request.workerId,
        'approval_request_id': request.id,
        'decision': body['decision'] ?? 'accept',
        'answers': body['answers'] ?? <String, String>{},
      },
    );
    if (mounted) _startSessionPolling();
  }

  Future<void> _pasteScreenshotAttachment() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (!_looksLikeImageDataUrl(text)) {
      if (mounted) {
        setState(() {
          chatStatus =
              'Clipboard does not contain a screenshot data URL. Copy an image data URL or attach from desktop.';
        });
      }
      return;
    }
    final mimeType = _imageMimeTypeFromDataUrl(text);
    final sizeBytes = _estimatedDataUrlBytes(text);
    final extension = _attachmentExtensionForMimeType(mimeType);
    final attachment = PhoneAttachmentDraft(
      id: _newId('phone-image'),
      name: 'phone-screenshot-${composerAttachments.length + 1}.$extension',
      mimeType: mimeType,
      sizeBytes: sizeBytes,
      dataUrl: text,
    );
    setState(() {
      composerAttachments = [...composerAttachments, attachment];
      chatStatus = 'Screenshot attached';
    });
    await _persistDeviceState().catchError((Object _) {
      if (mounted) {
        setState(
          () => chatStatus = 'Screenshot could not be saved on this phone.',
        );
      }
    });
  }

  void _removeComposerAttachment(String id) {
    setState(() {
      composerAttachments = composerAttachments
          .where((attachment) => attachment.id != id)
          .toList();
    });
    unawaited(_persistDeviceState().catchError((Object _) {}));
  }

  Future<void> _changeRunMode(WorkRunMode next) async {
    if (runMode == next) return;
    setState(() {
      runMode = next;
      chatStatus = '${next.label} mode selected';
    });
    await _savePhoneSettings();
  }

  Future<void> _stopActiveRun() async {
    if (!activeRunLive || activeRunId.isEmpty) return;
    if (_deviceState.deliveries.any(
      (item) =>
          item.apiBase == activeApiBase &&
          item.runId == activeRunId &&
          item.kind == 'stop' &&
          !['failed', 'expired'].contains(item.status),
    )) {
      setState(
        () => chatStatus =
            'The stop request is already saved. Waiting for the computer’s state.',
      );
      return;
    }
    await _submitControl('stop', 'Stop current task', {'run_id': activeRunId});
  }

  void _startSessionPolling() {
    agentPollTimer?.cancel();
    final subscription = ++_pollSubscription;
    if (!mounted || !_foreground || !_deviceLoaded) return;
    unawaited(_pollSession(_generation, subscription));
  }

  bool _scopeCurrent(int epoch, int subscription, String base, String thread) =>
      mounted &&
      _foreground &&
      epoch == _generation &&
      subscription == _pollSubscription &&
      base == activeApiBase &&
      thread == activeThreadId;

  Future<void> _pollSession(int epoch, int subscription) async {
    final base = activeApiBase;
    final thread = activeThreadId;
    final workflow = activeWorkflowId;
    final token = accessToken;
    try {
      if (thread.isEmpty) {
        await _api.request(base, '/api/health', token: token);
        if (!_scopeCurrent(epoch, subscription, base, thread)) return;
      } else {
        final payload = await _api.request(
          base,
          '/api/phone/sessions/${Uri.encodeComponent(workflow)}/${Uri.encodeComponent(thread)}/state',
          token: token,
        );
        if (!_scopeCurrent(epoch, subscription, base, thread)) return;
        _applySessionState(payload);
        if (activeRunId.isNotEmpty) {
          final run = _recordValue(payload['run']) ?? {};
          final task = _recordValue(payload['task']) ?? {};
          // The phone state endpoint already supplies compact progress and
          // artifacts. Do not repeatedly download the complete event log.
          _applyRunEvents(activeRunId, {
            'events': [
              {
                'summary': _asString(
                  run['latest_summary'],
                  _asString(task['latest_progress'], ''),
                ),
                'artifact_refs': task['artifacts'] ?? const [],
              },
            ],
          });
        }
      }
      if (!_scopeCurrent(epoch, subscription, base, thread)) return;
      _pollFailures = 0;
      setState(() {
        health = 'ready';
        lastSyncedAt = DateTime.now();
        statusDetail = 'Connected';
      });
      await _persistDeviceState().catchError((Object _) {});
      if (_scopeCurrent(epoch, subscription, base, thread)) {
        unawaited(_flushOutbox());
        _pollCycles += 1;
        if (_pollCycles % 6 == 0) unawaited(_refreshSessions());
      }
    } on PhoneApiFailure catch (failure) {
      if (!_scopeCurrent(epoch, subscription, base, thread)) return;
      _pollFailures += 1;
      setState(() {
        health = 'offline';
        statusDetail = failure.message;
        chatStatus = failure.statusCode == 404 && thread.isNotEmpty
            ? 'This conversation is unavailable. Open another conversation from Work.'
            : 'Connection interrupted. Your saved work is still here.';
      });
    } catch (_) {
      if (!_scopeCurrent(epoch, subscription, base, thread)) return;
      _pollFailures += 1;
      setState(() {
        health = 'offline';
        statusDetail = 'Connection interrupted';
      });
    } finally {
      if (_scopeCurrent(epoch, subscription, base, thread)) {
        agentPollTimer = Timer(
          phoneReconnectDelay(_pollFailures),
          () => unawaited(_pollSession(epoch, subscription)),
        );
      }
    }
  }

  void _applySessionState(Map<String, dynamic> payload) {
    final thread = _recordValue(payload['thread']) ?? {};
    final run = _recordValue(payload['run']) ?? {};
    final task = _recordValue(payload['task']) ?? {};
    final metadata = _recordValue(task['metadata']) ?? {};
    final canonical = _phoneMessagesFromThreadPayload(thread);
    final ids = canonical.map((message) => message.id).toSet();
    final localIds = _conversationDeliveries
        .map((item) => '${item.payload['message_id'] ?? ''}')
        .toSet();
    final pending = chatMessages
        .where(
          (message) =>
              localIds.contains(message.id) && !ids.contains(message.id),
        )
        .toList();
    final runStatus = _asString(run['status'], _asString(task['status'], ''));
    final approvals =
        (payload['pending_approvals'] is List
                ? payload['pending_approvals'] as List
                : const [])
            .whereType<Map>()
            .map(
              (value) => PhoneApprovalRequest.fromJson(
                Map<String, dynamic>.from(value),
              ),
            )
            .toList();
    final shownApprovals = <PhoneApprovalRequest>[];
    for (final request in approvals) {
      final receipts = _deviceState.deliveries.where(
        (item) =>
            item.apiBase == activeApiBase &&
            item.kind == 'answer' &&
            item.runId == request.runId &&
            '${item.payload['worker_id'] ?? ''}' == request.workerId &&
            item.payload['approval_request_id'] == request.id,
      );
      final latest = receipts.isEmpty ? null : receipts.last;
      if (latest?.status == 'accepted') continue;
      shownApprovals.add(
        latest == null
            ? request
            : request.copyWith(
                status:
                    [
                      'expired',
                      'needs_review',
                      'rejected',
                    ].contains(latest.status)
                    ? latest.status
                    : latest.status == 'failed'
                    ? 'pending'
                    : 'sending',
                error: latest.error,
              ),
      );
    }
    setState(() {
      conversationTitle = _asString(thread['title'], conversationTitle);
      chatMessages = [...canonical, ...pending];
      activeRunId = _asString(
        run['run_id'],
        _asString(metadata['active_run_id'], ''),
      );
      activeTaskId = _asString(task['task_id'], _asString(run['task_id'], ''));
      activeRunLive =
          activeRunId.isNotEmpty &&
          ![
            'completed',
            'failed',
            'stopped',
            'interrupted',
            'cancelled',
            'blocked',
          ].contains(runStatus);
      executionContext =
          _recordValue(payload['execution_context']) ?? executionContext;
      pendingApprovals = shownApprovals;
      final root = _asString(
        executionContext['workspace_root'],
        _asString(
          run['workspace_root'],
          _asString(metadata['workspace_root'], workspaceRoot),
        ),
      );
      if (_phoneTab == 0 && root.isNotEmpty) {
        workspaceRoot = root;
        workspaceRootController.text = root;
      }
      chatStatus = pendingApprovals.isNotEmpty
          ? 'Needs your decision'
          : activeRunLive
          ? _asString(task['latest_progress'], 'Working on your computer')
          : 'Ready';
      if (task.isNotEmpty) {
        phoneTasksByThread = {
          ...phoneTasksByThread,
          activeThreadId: PhoneTaskSummary.fromJson({
            ...task,
            'thread_id': activeThreadId,
            'workspace_root': root,
            'metadata': {'workspace_root': root, 'active_run_id': activeRunId},
          }),
        };
      }
    });
  }

  void _applyRunEvents(String run, Map<String, dynamic> payload) {
    final events =
        (payload['events'] is List ? payload['events'] as List : const [])
            .whereType<Map<String, dynamic>>()
            .toList();
    if (events.isEmpty) return;
    final artifacts = events.expand(_previewArtifactsFromEvent).toList();
    if (artifacts.isNotEmpty) _mergePreviewArtifacts(artifacts);
    final text = _bestAgentEventText(events);
    if (text.isEmpty ||
        chatMessages.any(
          (message) =>
              message.role == PhoneChatRole.assistant &&
              (message.record['task_run_ref'] is Map &&
                  (message.record['task_run_ref'] as Map)['run_id'] == run),
        )) {
      return;
    }
    final id = 'phone-progress-$run';
    // A transient view of recorded events, never a PUT of authoritative history.
    setState(() {
      chatMessages = [
        ...chatMessages.where((message) => message.id != id),
        PhoneChatMessage(
          id: id,
          role: PhoneChatRole.assistant,
          text: text,
          status: activeRunLive ? 'Working' : '',
          record: {'phone_projection': true},
        ),
      ];
    });
  }

  Iterable<PhonePreviewArtifact> _previewArtifactsFromEvent(
    Map<String, dynamic> event,
  ) sync* {
    final refs = <Object?>[
      event['artifact_refs'],
      if (event['payload'] is Map<String, dynamic>)
        (event['payload'] as Map<String, dynamic>)['artifact_refs'],
      if (event['payload'] is Map<String, dynamic>)
        (event['payload'] as Map<String, dynamic>)['artifacts'],
    ];
    for (final refList in refs) {
      if (refList is! List<dynamic>) continue;
      for (final item in refList) {
        if (item is Map<String, dynamic>) {
          final artifact = PhonePreviewArtifact.fromJson(item);
          if (artifact.path.isNotEmpty || artifact.url.isNotEmpty) {
            yield artifact;
          }
        } else if (item is Map) {
          final mapped = item.map(
            (key, value) => MapEntry(key.toString(), value),
          );
          final artifact = PhonePreviewArtifact.fromJson(mapped);
          if (artifact.path.isNotEmpty || artifact.url.isNotEmpty) {
            yield artifact;
          }
        } else if (item is String && item.trim().isNotEmpty) {
          final value = item.trim();
          yield PhonePreviewArtifact(
            id: value,
            title: _baseName(value).isEmpty
                ? 'Output artifact'
                : _baseName(value),
            path: value.startsWith('http://') || value.startsWith('https://')
                ? ''
                : value,
            url: value.startsWith('http://') || value.startsWith('https://')
                ? value
                : '',
            kind: 'artifact',
            source: 'run',
          );
        }
      }
    }
  }

  void _mergePreviewArtifacts(List<PhonePreviewArtifact> artifacts) {
    final byId = <String, PhonePreviewArtifact>{
      for (final artifact in previewArtifacts) artifact.id: artifact,
    };
    for (final artifact in artifacts) {
      byId[artifact.id] = artifact;
    }
    final next = byId.values.toList();
    if (next.length == previewArtifacts.length &&
        next.every((artifact) => previewArtifacts.contains(artifact))) {
      return;
    }
    setState(() => previewArtifacts = next);
  }

  String _bestAgentEventText(List<Map<String, dynamic>> events) {
    for (final event in events.reversed) {
      final text = _agentEventText(event);
      if (text.isNotEmpty && !_isGenericAgentReceipt(text)) return text;
    }
    return _agentEventText(events.last);
  }

  String _agentEventText(Map<String, dynamic>? event) {
    if (event == null) return '';
    final payload = event['payload'] is Map<String, dynamic>
        ? event['payload'] as Map<String, dynamic>
        : const <String, dynamic>{};
    final structured = _structuredAgentDisplayFromValue(payload);
    if (structured.isNotEmpty) return structured;
    final summary = _asString(event['summary'], '');
    if (summary.isNotEmpty) return summary;
    for (final key in ['final_answer', 'answer', 'message', 'text', 'error']) {
      final value = _asString(payload[key], '');
      if (value.isNotEmpty) return value;
    }
    final type = _asString(event['type'], '');
    if (type == 'completed') return 'Completed.';
    if (type == 'failed') return 'Run failed.';
    if (type == 'blocked') return 'Run blocked.';
    return type.isEmpty ? '' : type;
  }

  Future<dynamic> _handlePhoneWireGuardEvent(MethodCall call) async {
    if (call.method != 'statusChanged') return null;
    final status = _phoneWireGuardStatusFromResult(call.arguments);
    if (!mounted || status == null) return null;
    setState(() {
      phoneWireGuard = status;
      phoneWireGuardDetail = status.message.isNotEmpty
          ? status.message
          : 'Phone VPN ${status.state} (${status.name})';
    });
    if (status.state == 'up') {
      unawaited(_refreshStatus());
    }
    return null;
  }

  PhoneWireGuardStatus? _phoneWireGuardStatusFromResult(Object? result) {
    if (result is Map<Object?, Object?>) {
      return PhoneWireGuardStatus.fromMap(result);
    }
    return null;
  }

  Future<void> _preparePhoneWireGuardFor(List<String> candidates) async {
    if (!Platform.isAndroid || !candidates.any(isWireGuardApiBase)) return;
    final status = await _refreshPhoneWireGuardStatus(showBusy: false);
    if (status?.state == 'up') return;
    if (phoneWireGuardConfig.trim().isEmpty) {
      if (!mounted) return;
      setState(() {
        phoneWireGuardDetail = 'Add a Dear Diane phone VPN config in Settings';
      });
      return;
    }
    if (!compiledPhoneWireGuardAutoStart) {
      if (!mounted) return;
      setState(() {
        phoneWireGuardDetail = 'Connect Dear Diane Phone VPN from Settings';
      });
      return;
    }
    await _startPhoneWireGuard(showBusy: false);
  }

  Future<PhoneWireGuardStatus?> _refreshPhoneWireGuardStatus({
    bool showBusy = true,
  }) {
    return _phoneWireGuardCall('status', showBusy: showBusy);
  }

  Future<PhoneWireGuardStatus?> _startPhoneWireGuard({
    bool showBusy = true,
  }) async {
    phoneWireGuardConfig = wireGuardConfigController.text.trim();
    if (phoneWireGuardConfig.isEmpty) {
      if (mounted) {
        setState(() {
          phoneWireGuardDetail = health == 'ready'
              ? 'Phone VPN is optional while the API is connected'
              : 'Add a Dear Diane phone VPN config to start app-managed VPN';
        });
      }
      return null;
    }
    return _phoneWireGuardCall(
      'start',
      arguments: {'config': phoneWireGuardConfig},
      showBusy: showBusy,
    );
  }

  Future<PhoneWireGuardStatus?> _forceStartPhoneWireGuard({
    bool showBusy = true,
  }) async {
    phoneWireGuardConfig = wireGuardConfigController.text.trim();
    if (phoneWireGuardConfig.isEmpty) {
      if (mounted) {
        setState(() {
          phoneWireGuardDetail =
              'Paste a Dear Diane phone VPN config before forcing the tunnel on';
        });
      }
      return null;
    }
    if (showBusy && mounted) {
      setState(() {
        phoneWireGuardBusy = true;
        phoneWireGuardDetail = 'Force starting Dear Diane Phone VPN tunnel';
      });
    }
    try {
      await _phoneWireGuardCall('stop', showBusy: false);
      final status = await _phoneWireGuardCall(
        'start',
        arguments: {'config': phoneWireGuardConfig},
        showBusy: false,
      );
      if (status?.state == 'up') {
        unawaited(_refreshStatus());
      }
      return status;
    } finally {
      if (showBusy && mounted) {
        setState(() => phoneWireGuardBusy = false);
      }
    }
  }

  Future<void> _pastePhoneWireGuardConfig() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (!text.contains('[Interface]') || !text.contains('[Peer]')) {
      if (mounted) {
        setState(() {
          phoneWireGuardDetail =
              'Clipboard does not contain a WireGuard config';
        });
      }
      return;
    }
    wireGuardConfigController.text = text;
    phoneWireGuardConfig = text;
    await _savePhoneSettings();
    if (mounted) {
      setState(() {
        phoneWireGuardDetail = 'Dear Diane phone VPN config saved';
      });
    }
  }

  Future<PhoneWireGuardStatus?> _forceStopPhoneWireGuard({
    bool showBusy = true,
  }) async {
    if (showBusy && mounted) {
      setState(() {
        phoneWireGuardBusy = true;
        phoneWireGuardDetail = 'Force off requested for Dear Diane Phone VPN';
      });
    }
    try {
      final status = await _phoneWireGuardCall('stop', showBusy: false);
      if (mounted) {
        setState(() {
          phoneWireGuardDetail =
              'Dear Diane Phone VPN forced off (${status?.name ?? 'dan-phone'})';
        });
      }
      unawaited(_refreshStatus());
      return status;
    } finally {
      if (showBusy && mounted) {
        setState(() => phoneWireGuardBusy = false);
      }
    }
  }

  Future<PhoneWireGuardStatus?> _phoneWireGuardCall(
    String method, {
    Map<String, Object?> arguments = const {},
    bool showBusy = true,
  }) async {
    if (!Platform.isAndroid) {
      final status = const PhoneWireGuardStatus(
        supported: false,
        name: 'dan-phone',
        state: 'unavailable',
        message: 'Phone VPN is available in the Android APK',
      );
      if (mounted) {
        setState(() {
          phoneWireGuard = status;
          phoneWireGuardDetail = status.message;
        });
      }
      return status;
    }
    if (showBusy && mounted) {
      setState(() => phoneWireGuardBusy = true);
    }
    try {
      final result = await wireGuardChannel
          .invokeMethod<Object?>(method, arguments)
          .timeout(const Duration(seconds: 5));
      final status = _phoneWireGuardStatusFromResult(result);
      if (mounted && status != null) {
        setState(() {
          phoneWireGuard = status;
          phoneWireGuardDetail = status.message.isNotEmpty
              ? status.message
              : 'Phone VPN ${status.state} (${status.name})';
        });
      }
      return status;
    } on MissingPluginException {
      final status = const PhoneWireGuardStatus(
        supported: false,
        name: 'dan-phone',
        state: 'unavailable',
        message: 'Native WireGuard bridge is not loaded',
      );
      if (mounted) {
        setState(() {
          phoneWireGuard = status;
          phoneWireGuardDetail = status.message;
        });
      }
      return status;
    } on PlatformException catch (error) {
      if (mounted) {
        setState(() {
          phoneWireGuardDetail =
              error.message ?? 'Phone VPN command failed: ${error.code}';
        });
      }
      return null;
    } catch (error) {
      if (mounted) {
        setState(() {
          phoneWireGuardDetail =
              'Phone VPN command timed out or failed: $error';
        });
      }
      return null;
    } finally {
      if (showBusy && mounted) {
        setState(() => phoneWireGuardBusy = false);
      }
    }
  }

  Future<void> _openBackendDialog() async {
    apiBaseController.text = activeApiBase;
    accessTokenController.text = accessToken;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, refreshSheet) {
          Future<void> reconnect() async {
            await _refreshStatus();
            if (context.mounted) refreshSheet(() {});
          }

          Future<void> saveConnection() async {
            final next = _normalizeApiBase(apiBaseController.text);
            accessToken = _normalizeAccessToken(accessTokenController.text);
            if (next != activeApiBase) {
              await _changeComputer(next);
            } else {
              await _savePhoneSettings();
              await _refreshStatus();
            }
            if (context.mounted) refreshSheet(() {});
          }

          return Padding(
            padding: EdgeInsets.only(
              bottom: MediaQuery.viewInsetsOf(context).bottom,
            ),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                maxHeight: MediaQuery.sizeOf(context).height * .86,
              ),
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(24, 6, 24, 24),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Your computer',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Icon(
                          health == 'ready'
                              ? Icons.check_circle_outline
                              : Icons.cloud_off_outlined,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            health == 'ready'
                                ? 'Connected and ready'
                                : health == 'checking'
                                ? 'Connecting…'
                                : 'Waiting for connection',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      health == 'ready'
                          ? 'Work runs on your computer. You can leave this screen and return later.'
                          : 'Messages stay saved on this phone until your computer acknowledges them.',
                    ),
                    if (lastSyncedAt != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text(
                          'Last synced ${lastSyncedAt!.toLocal().hour.toString().padLeft(2, '0')}:${lastSyncedAt!.toLocal().minute.toString().padLeft(2, '0')}',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ),
                    if (_deliveries.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 12),
                        child: Text(
                          '${_deliveries.length} saved ${_deliveries.length == 1 ? 'action needs' : 'actions need'} delivery or review.',
                        ),
                      ),
                    const SizedBox(height: 20),
                    FilledButton.icon(
                      onPressed: health == 'checking' ? null : reconnect,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Reconnect'),
                    ),
                    const SizedBox(height: 16),
                    ExpansionTile(
                      tilePadding: EdgeInsets.zero,
                      title: const Text('Connection details'),
                      subtitle: const Text(
                        'Address, access token and private network',
                      ),
                      children: [
                        const SizedBox(height: 8),
                        TextField(
                          controller: apiBaseController,
                          keyboardType: TextInputType.url,
                          autocorrect: false,
                          enableSuggestions: false,
                          onChanged: (value) {
                            if (_normalizeApiBase(value) != activeApiBase) {
                              accessTokenController.clear();
                            }
                          },
                          decoration: const InputDecoration(
                            labelText: 'Computer address',
                            hintText: 'https://your-computer.example',
                          ),
                        ),
                        const SizedBox(height: 12),
                        TextField(
                          controller: accessTokenController,
                          obscureText: true,
                          autocorrect: false,
                          enableSuggestions: false,
                          decoration: const InputDecoration(
                            labelText: 'Access token',
                          ),
                        ),
                        const SizedBox(height: 12),
                        OutlinedButton(
                          onPressed: saveConnection,
                          child: const Text('Save and connect'),
                        ),
                        const SizedBox(height: 12),
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: const Icon(Icons.vpn_lock_outlined),
                          title: const Text('Private network'),
                          subtitle: Text(phoneWireGuardDetail),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () {
                            Navigator.of(sheetContext).pop();
                            unawaited(_openPhoneWireGuardDialog());
                          },
                        ),
                        if (health != 'ready')
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            child: Text(
                              statusDetail,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                      ],
                    ),
                    TextButton(
                      onPressed: () => Navigator.of(sheetContext).pop(),
                      child: const Text('Done'),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
    if (mounted) {
      apiBaseController.text = activeApiBase;
      accessTokenController.text = accessToken;
    }
  }

  Future<void> _openPhoneWireGuardDialog() async {
    wireGuardConfigController.text = phoneWireGuardConfig;
    await showDialog<void>(
      context: context,
      builder: (context) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            Future<void> runAndRefresh(
              Future<Object?> Function() action,
            ) async {
              await action();
              if (context.mounted) setDialogState(() {});
            }

            return AlertDialog(
              title: const Text('Phone VPN'),
              content: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        _StatusChip(
                          icon: Icons.phone_android_outlined,
                          label: 'Tunnel',
                          value: phoneWireGuard?.name ?? 'dan-phone',
                        ),
                        _StatusChip(
                          icon: Icons.power_settings_new_outlined,
                          label: 'State',
                          value: phoneWireGuardBusy
                              ? 'working'
                              : phoneWireGuard?.state ?? 'unknown',
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: wireGuardConfigController,
                      minLines: 6,
                      maxLines: 10,
                      decoration: const InputDecoration(
                        labelText: 'Dear Diane phone WireGuard config',
                        hintText: '[Interface]\nPrivateKey = ...',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    if (phoneWireGuardConfig.trim().isEmpty) ...[
                      Text(
                        'No phone VPN config saved. Paste a Dear Diane phone peer config before using Force start.',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                      const SizedBox(height: 8),
                    ],
                    Text(
                      phoneWireGuardDetail,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('Close'),
                ),
                TextButton(
                  onPressed: phoneWireGuardBusy
                      ? null
                      : () =>
                            runAndRefresh(() => _refreshPhoneWireGuardStatus()),
                  child: const Text('Status'),
                ),
                TextButton(
                  onPressed: phoneWireGuardBusy
                      ? null
                      : () => runAndRefresh(_pastePhoneWireGuardConfig),
                  child: const Text('Paste config'),
                ),
                TextButton(
                  onPressed: phoneWireGuardBusy
                      ? null
                      : () => runAndRefresh(_forceStopPhoneWireGuard),
                  child: const Text('Force off'),
                ),
                FilledButton(
                  onPressed:
                      phoneWireGuardBusy || phoneWireGuardConfig.trim().isEmpty
                      ? null
                      : () => runAndRefresh(_forceStartPhoneWireGuard),
                  child: const Text('Force start'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  Widget _workBody(WorkPage page) => _WorkSurface(
    page: page,
    runMode: runMode,
    workspaceRoot: workspaceRoot,
    workspaceRootController: workspaceRootController,
    files: workspaceFiles,
    activeFile: activeFile,
    activeFileContent: activeFileContent,
    previewTitle: previewTitle,
    previewStatus: previewStatus,
    previewContent: previewContent,
    previewArtifacts: previewArtifacts,
    previewArtifactsExpanded: previewArtifactsExpanded,
    fileStatus: fileStatus,
    loadingFiles: loadingFiles,
    chatMessages: chatMessages,
    chatController: chatController,
    chatStatus: _storageWarning.isEmpty ? chatStatus : _storageWarning,
    chatBusy: chatBusy,
    activeRunLive: activeRunLive,
    composerAttachments: composerAttachments,
    loadingPromptLog: loadingPromptLog,
    onRunModeChanged: _changeRunMode,
    onPasteScreenshot: _pasteScreenshotAttachment,
    onRemoveAttachment: _removeComposerAttachment,
    onStopRun: _stopActiveRun,
    onOpenPromptLog: _openPromptLogPreview,
    onTogglePreviewArtifacts: () =>
        setState(() => previewArtifactsExpanded = !previewArtifactsExpanded),
    onFetchRootSuggestions: _fetchWorkspaceRootSuggestions,
    onApplyWorkspaceRoot: _applyWorkspaceRoot,
    onRefreshFiles: _refreshWorkspaceFiles,
    onSelectFile: _selectWorkspaceFile,
    onSelectArtifact: _selectPreviewArtifact,
    onSendChat: _sendChatMessage,
    onQueueChat: _queueChatMessage,
    deliveries: _conversationDeliveries,
    pendingApprovals: pendingApprovals,
    onRetryDelivery: _retryDelivery,
    onReplyApproval: _replyToApproval,
    conversationTitle: conversationTitle,
    connectionState: health,
    conversationId: activeThreadId,
  );

  Widget _libraryBody() {
    final notes = Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: SegmentedButton<NotesPage>(
            showSelectedIcon: false,
            segments: const [
              ButtonSegment(value: NotesPage.pages, label: Text('Pages')),
              ButtonSegment(value: NotesPage.edit, label: Text('Edit')),
              ButtonSegment(value: NotesPage.read, label: Text('Read')),
            ],
            selected: {notesPage},
            onSelectionChanged: (selection) {
              _saveActiveNoteReadScroll();
              _saveActiveNoteEditScroll();
              setState(() => notesPage = selection.first);
              if (selection.first == NotesPage.read && activeNote != null) {
                unawaited(_refreshNotePreview(activeNote!));
              }
            },
          ),
        ),
        Expanded(
          child: _NotesSurface(
            page: notesPage,
            notes: workspaceNotes,
            activeNote: activeNote,
            activePreview: activeNote == null
                ? null
                : notePreviewCache[activeNote!.path],
            notesRoot: notesRoot,
            noteContent: noteContent,
            noteStatus: noteStatus,
            notePreviewStatus: notePreviewStatus,
            noteSaveStatus: noteSaveStatus,
            loading: loadingNotes,
            editController: noteEditController,
            readScrollController: noteReadScrollController,
            editScrollController: noteEditScrollController,
            onSaveNote: _saveActiveNote,
            onSelectNote: _selectNote,
            onRefresh: () => _refreshNotes(),
          ),
        ),
      ],
    );
    return PhoneLibraryView(
      selectedIndex: _libraryTab,
      onSelected: (index) => setState(() => _libraryTab = index),
      child: _libraryTab == 1
          ? notes
          : _workBody(_libraryTab == 0 ? WorkPage.files : WorkPage.preview),
    );
  }

  @override
  Widget build(BuildContext context) {
    return PhoneShell(
      scaffoldKey: scaffoldKey,
      drawer: _SessionDrawer(
        groups: _buildSessionGroups(),
        tasksByThread: phoneTasksByThread,
        activeThreadId: activeThreadId,
        activeWorkspaceRoot: workspaceRoot,
        loading: loadingSessions,
        onRefresh: _refreshSessions,
        onCreateWorkspace: _openNewWorkspaceDialog,
        onSelectWorkspace: _selectWorkspaceRootFromDrawer,
        onCreateSession: _createSessionForWorkspace,
        onSelectSession: _selectPhoneSession,
      ),
      selectedIndex: _phoneTab,
      onDestinationSelected: (index) {
        setState(() => _phoneTab = index);
        if (index == 1) unawaited(_refreshSessions());
      },
      body: _phoneTab == 0
          ? _workBody(WorkPage.chat)
          : _phoneTab == 1
          ? PhoneWorkOverview(
              groups: _buildSessionGroups(),
              tasksByThread: phoneTasksByThread,
              loading: loadingSessions,
              onRefresh: _refreshSessions,
              onSelectSession: _selectPhoneSession,
              deliveries: _deliveries,
              onRetryDelivery: _retryDelivery,
              approvals: pendingApprovals,
              onReplyApproval: _replyToApproval,
              onOpenDiane: () => setState(() => _phoneTab = 0),
            )
          : _libraryBody(),
      connectionState: health,
      computerLabel: 'Your computer',
      onConfigureBackend: _openBackendDialog,
      attentionCount:
          phoneTasksByThread.values
              .where(
                (task) => [
                  'needs_input',
                  'blocked',
                  'failed',
                  'paused',
                  'interrupted',
                ].contains(task.status),
              )
              .length +
          _deliveries.where((item) => item.isPending).length,
      conversationTitle: conversationTitle,
      lastSyncedAt: lastSyncedAt,
      onReconnect: _refreshStatus,
    );
  }
}
