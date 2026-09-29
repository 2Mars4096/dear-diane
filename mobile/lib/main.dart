import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

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
  });

  final String id;
  final PhoneChatRole role;
  final String text;
  final String status;

  PhoneChatMessage copyWith({String? text, String? status}) {
    return PhoneChatMessage(
      id: id,
      role: role,
      text: text ?? this.text,
      status: status ?? this.status,
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

class DanPhoneApp extends StatelessWidget {
  const DanPhoneApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Dear Diane Phone',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xff176b5b),
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xfff5f2eb),
        useMaterial3: true,
      ),
      home: const WorkspaceHome(),
    );
  }
}

class WorkspaceHome extends StatefulWidget {
  const WorkspaceHome({super.key});

  @override
  State<WorkspaceHome> createState() => _WorkspaceHomeState();
}

class _WorkspaceHomeState extends State<WorkspaceHome> {
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
          'Set a workspace, then send a Work request. Diane will run through the same Agent-run backend as the desktop GUI.',
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
    activeApiBase = _normalizeApiBase(compiledDefaultApiBase);
    apiBaseController = TextEditingController(text: activeApiBase);
    accessTokenController = TextEditingController();
    wireGuardConfigController = TextEditingController(
      text: phoneWireGuardConfig,
    );
    noteEditController = TextEditingController();
    noteEditController.addListener(_handleNoteEditChanged);
    workspaceRootController = TextEditingController();
    workspaceRootController.addListener(_handleWorkspaceRootInputChanged);
    chatController = TextEditingController();
    noteReadScrollController = ScrollController()
      ..addListener(_saveActiveNoteReadScroll);
    noteEditScrollController = ScrollController()
      ..addListener(_saveActiveNoteEditScroll);
    if (Platform.isAndroid) {
      wireGuardChannel.setMethodCallHandler(_handlePhoneWireGuardEvent);
      unawaited(_refreshPhoneWireGuardStatus());
      unawaited(_loadPhoneSettings());
    }
    unawaited(_refreshStatus());
  }

  @override
  void dispose() {
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
    if (!mounted || health == 'offline') return;
    setState(() => loadingSessions = true);
    final threadPayload = await _getJson(activeApiBase, '/api/chats');
    final taskPayload = await _getJson(
      activeApiBase,
      '/api/v2/tasks?limit=160',
    );
    if (!mounted) return;
    final sessions = (threadPayload?['threads'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(PhoneSessionSummary.fromJson)
        .where((session) => session.id.isNotEmpty)
        .toList();
    final tasks = (taskPayload?['tasks'] as List<dynamic>? ?? const [])
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
          status: _asString(taskRef?['status'], ''),
        ),
      );
    }
    return parsed;
  }

  List<Map<String, Object?>> _messagesToThreadPayload(
    List<PhoneChatMessage> messages,
  ) {
    return messages
        .where(
          (message) =>
              message.role == PhoneChatRole.user ||
              message.role == PhoneChatRole.assistant,
        )
        .map(
          (message) => {
            'id': message.id,
            'role': message.role == PhoneChatRole.user ? 'user' : 'assistant',
            'content': message.text,
          },
        )
        .toList();
  }

  Future<void> _saveActiveThreadMessages([
    List<PhoneChatMessage>? source,
  ]) async {
    if (activeThreadId.isEmpty || activeWorkflowId.isEmpty) return;
    final payloadMessages = _messagesToThreadPayload(source ?? chatMessages);
    await _requestJson(
      activeApiBase,
      '/api/chats/${Uri.encodeComponent(activeWorkflowId)}/${Uri.encodeComponent(activeThreadId)}',
      method: 'PUT',
      body: {'mode': 'agent', 'messages': payloadMessages},
      timeout: const Duration(seconds: 8),
    );
    unawaited(_refreshSessions());
  }

  Future<void> _selectWorkspaceRootFromDrawer(String root) async {
    final nextRoot = root.trim();
    setState(() {
      workspaceRoot = nextRoot;
      workspaceRootController.text = nextRoot;
      phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [nextRoot]);
      mode = WorkspaceMode.work;
      workPage = WorkPage.files;
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
            chatStatus = 'Restore archived sessions from the desktop workspace',
      );
      return;
    }
    agentPollTimer?.cancel();
    final targetRoot = workspaceRootOverride.trim().isNotEmpty
        ? workspaceRootOverride.trim()
        : _sessionWorkspaceRootFor(session).trim();
    setState(() {
      activeWorkflowId = session.workflowId;
      activeThreadId = session.id;
      activeRunId = '';
      activeTaskId = '';
      activeRunLive = false;
      chatBusy = false;
      chatStatus = 'Loading session';
      chatMessages = const [];
      composerAttachments = const [];
      previewArtifacts = const [];
      promptLogEntryCount = 0;
      promptLogPath = '';
      mode = WorkspaceMode.work;
      workPage = WorkPage.chat;
      if (targetRoot.isNotEmpty) {
        workspaceRoot = targetRoot;
        workspaceRootController.text = targetRoot;
        phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [targetRoot]);
      }
    });
    if (targetRoot.isNotEmpty) {
      setState(() {
        sessionWorkspaceRoots = {
          ...sessionWorkspaceRoots,
          _sessionWorkspaceKey(session.workflowId, session.id): targetRoot,
        };
      });
      unawaited(_refreshWorkspaceFiles(root: targetRoot));
    }
    final threadPayload = await _getJson(
      activeApiBase,
      '/api/chats/${Uri.encodeComponent(session.workflowId)}/${Uri.encodeComponent(session.id)}',
    );
    final taskPayload = await _getJson(
      activeApiBase,
      '/api/v2/threads/${Uri.encodeComponent(session.id)}/tasks?limit=20',
    );
    if (!mounted || activeThreadId != session.id) return;
    final threadMessages = _phoneMessagesFromThreadPayload(threadPayload);
    final threadTasks = (taskPayload?['tasks'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(PhoneTaskSummary.fromJson)
        .where((task) => task.threadId == session.id)
        .toList();
    final latestTask = threadTasks.isNotEmpty
        ? threadTasks.first
        : phoneTasksByThread[session.id];
    final recoveredRoot = latestTask?.workspaceRoot ?? targetRoot;
    final fallbackMessages = threadMessages.isNotEmpty
        ? threadMessages
        : latestTask != null &&
              (latestTask.latestProgress.isNotEmpty || latestTask.isLive)
        ? [
            PhoneChatMessage(
              id: _newId('assistant'),
              role: PhoneChatRole.assistant,
              text: latestTask.latestProgress.isNotEmpty
                  ? latestTask.latestProgress
                  : 'Diane is working in this session.',
              status: latestTask.status,
            ),
          ]
        : const <PhoneChatMessage>[];
    setState(() {
      chatMessages = fallbackMessages;
      activeTaskId = latestTask?.taskId ?? '';
      activeRunId = latestTask?.activeRunId ?? '';
      activeRunLive = latestTask?.isLive == true && activeRunId.isNotEmpty;
      chatStatus = fallbackMessages.isEmpty
          ? 'Ready'
          : latestTask?.status == 'completed'
          ? 'Ready'
          : latestTask?.status ?? 'Ready';
      if (recoveredRoot.isNotEmpty) {
        workspaceRoot = recoveredRoot;
        workspaceRootController.text = recoveredRoot;
        phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [
          recoveredRoot,
        ]);
        sessionWorkspaceRoots = {
          ...sessionWorkspaceRoots,
          _sessionWorkspaceKey(session.workflowId, session.id): recoveredRoot,
        };
      }
      if (latestTask != null) {
        phoneTasksByThread = {...phoneTasksByThread, session.id: latestTask};
      }
    });
    await _savePhoneSettings();
    if (activeRunLive && activeRunId.isNotEmpty) {
      _startAgentPolling(
        activeRunId,
        fallbackMessages.isNotEmpty
            ? fallbackMessages.last.id
            : _newId('assistant'),
      );
    }
  }

  Future<void> _createSessionForWorkspace(String root) async {
    final targetRoot = root.trim();
    setState(() {
      chatStatus = 'Creating session';
      if (targetRoot.isNotEmpty) {
        workspaceRoot = targetRoot;
        workspaceRootController.text = targetRoot;
        phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [targetRoot]);
      }
      mode = WorkspaceMode.work;
      workPage = WorkPage.chat;
    });
    if (targetRoot.isNotEmpty) {
      unawaited(_refreshWorkspaceFiles(root: targetRoot));
    }
    final payload = await _postJson(activeApiBase, '/api/chats/_scratch', {
      'title': 'New Diane Session',
      'mode': 'agent',
    });
    if (!mounted) return;
    if (payload == null) {
      setState(() => chatStatus = 'Session create failed');
      return;
    }
    final session = PhoneSessionSummary.fromJson(payload);
    if (session.id.isEmpty) {
      setState(() => chatStatus = 'Session create failed');
      return;
    }
    setState(() {
      activeWorkflowId = session.workflowId;
      activeThreadId = session.id;
      activeRunId = '';
      activeTaskId = '';
      activeRunLive = false;
      chatBusy = false;
      chatStatus = 'Ready';
      chatMessages = const [];
      previewArtifacts = const [];
      composerAttachments = const [];
      phoneSessions = [
        session,
        ...phoneSessions.where((item) => item.id != session.id),
      ];
      if (targetRoot.isNotEmpty) {
        sessionWorkspaceRoots = {
          ...sessionWorkspaceRoots,
          _sessionWorkspaceKey(session.workflowId, session.id): targetRoot,
        };
      }
    });
    await _savePhoneSettings();
    unawaited(_refreshSessions());
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
      if (savedApiBase.isNotEmpty || savedToken.isNotEmpty) {
        unawaited(_refreshStatus(preferredApiBase: activeApiBase));
      }
      if (savedWorkspaceRoot.isNotEmpty) {
        unawaited(_refreshWorkspaceFiles(root: savedWorkspaceRoot));
      }
    } on MissingPluginException {
      return;
    } on PlatformException {
      return;
    }
  }

  Future<void> _savePhoneSettings() async {
    accessToken = _normalizeAccessToken(accessTokenController.text);
    accessTokenController.text = accessToken;
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
    final requestedBase = _normalizeApiBase(preferredApiBase ?? activeApiBase);
    setState(() {
      loadingWireGuard = true;
      health = 'checking';
      wireGuard = null;
      activeApiBase = requestedBase;
      statusDetail = 'Checking $requestedBase';
    });

    try {
      final candidates = _candidateApiBases(requestedBase);
      await _preparePhoneWireGuardFor(candidates).timeout(
        const Duration(seconds: 4),
        onTimeout: () {
          if (!mounted) return;
          setState(() {
            phoneWireGuardDetail =
                'Phone VPN status timed out; checking backend directly';
          });
        },
      );
      Map<String, dynamic>? healthy;
      Map<String, dynamic>? wg;
      var selectedBase = requestedBase;

      for (final candidate in candidates) {
        healthy = await _getJson(candidate, '/api/health');
        if (healthy == null) continue;
        selectedBase = candidate;
        wg = await _getJson(candidate, '/api/workspace-wireguard');
        break;
      }

      if (!mounted) return;
      final parsedWireGuard = wg is Map<String, dynamic>
          ? WireGuardStatus.fromJson(wg)
          : null;
      final backendReady = healthy is Map<String, dynamic>;
      setState(() {
        wireGuard = parsedWireGuard;
        activeApiBase = selectedBase;
        apiBaseController.text = selectedBase;
        health = backendReady ? 'ready' : 'offline';
        statusDetail = health == 'ready'
            ? 'Connected to $selectedBase'
            : 'No Dear Diane backend at ${candidates.join(', ')}';
        if (phoneWireGuardConfig.trim().isEmpty && backendReady) {
          phoneWireGuardDetail = parsedWireGuard?.status == 'active'
              ? 'Phone VPN not required; backend is reachable and host WG is active'
              : 'Phone VPN config is only needed for app-managed VPN';
        }
      });
      if (health == 'ready') {
        unawaited(_refreshNotes(selectedBase));
        unawaited(_refreshWorkspaceFiles(apiBase: selectedBase));
        unawaited(_refreshSessions());
      } else {
        setState(() {
          loadingNotes = false;
          noteStatus = 'Backend offline';
        });
      }
    } catch (error) {
      if (!mounted) return;
      setState(() {
        health = 'offline';
        statusDetail = 'Status check failed: $error';
        loadingNotes = false;
        noteStatus = 'Backend offline';
      });
    } finally {
      if (mounted) {
        setState(() => loadingWireGuard = false);
      }
    }
  }

  List<String> _candidateApiBases(String preferred) {
    final values = <String>[
      _normalizeApiBase(preferred),
      _normalizeApiBase(compiledDefaultApiBase),
      ...fallbackApiBases.map(_normalizeApiBase),
    ];
    return values.where((value) => value.isNotEmpty).toSet().toList();
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

  Future<Map<String, dynamic>?> _postJson(
    String base,
    String path,
    Map<String, Object?> body, {
    Duration timeout = const Duration(seconds: 10),
  }) {
    return _requestJson(
      base,
      path,
      method: 'POST',
      body: body,
      timeout: timeout,
    );
  }

  Future<Map<String, dynamic>?> _requestJson(
    String base,
    String path, {
    String method = 'GET',
    Map<String, Object?>? body,
    Duration timeout = const Duration(seconds: 3),
  }) async {
    final client = HttpClient();
    client.connectionTimeout = const Duration(seconds: 2);
    try {
      final uri = Uri.parse('$base$path');
      final request =
          await (method == 'POST'
                  ? client.postUrl(uri)
                  : method == 'PUT'
                  ? client.putUrl(uri)
                  : client.getUrl(uri))
              .timeout(timeout);
      final token = _normalizeAccessToken(accessTokenController.text);
      if (token.isNotEmpty) {
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      }
      if (body != null) {
        request.headers.contentType = ContentType.json;
        request.write(jsonEncode(body));
      }
      final response = await request.close().timeout(timeout);
      if (response.statusCode < 200 || response.statusCode >= 300) return null;
      final responseBody = await utf8.decodeStream(response);
      if (responseBody.trim().isEmpty) return const <String, dynamic>{};
      final decoded = jsonDecode(responseBody);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    } finally {
      client.close(force: true);
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
    return '$prefix-${DateTime.now().microsecondsSinceEpoch}';
  }

  void _replaceChatMessage(String id, String text, {String status = ''}) {
    late final List<PhoneChatMessage> nextMessages;
    setState(() {
      nextMessages = chatMessages
          .map(
            (message) => message.id == id
                ? message.copyWith(text: text, status: status)
                : message,
          )
          .toList();
      chatMessages = nextMessages;
    });
    if (activeThreadId.isNotEmpty) {
      unawaited(_saveActiveThreadMessages(nextMessages));
    }
  }

  Future<bool> _ensurePhoneThread(String prompt) async {
    if (activeThreadId.isNotEmpty) return true;
    final title = prompt.length > 64 ? '${prompt.substring(0, 64)}...' : prompt;
    final payload = await _postJson(activeApiBase, '/api/chats/_scratch', {
      'title': title,
      'mode': 'agent',
    });
    if (payload == null) return false;
    activeThreadId = _asString(payload['id'], '');
    activeWorkflowId = _asString(payload['workflow_id'], '_scratch');
    final root = workspaceRoot.trim();
    if (activeThreadId.isNotEmpty && root.isNotEmpty) {
      setState(() {
        phoneWorkspaces = _mergeWorkspaceRoots(phoneWorkspaces, [root]);
        sessionWorkspaceRoots = {
          ...sessionWorkspaceRoots,
          _sessionWorkspaceKey(activeWorkflowId, activeThreadId): root,
        };
      });
      unawaited(_savePhoneSettings());
    }
    unawaited(_refreshSessions());
    return activeThreadId.isNotEmpty;
  }

  Map<String, Object?> _phoneSurfaceContext({
    List<PhoneAttachmentDraft> attachments = const [],
  }) {
    return {
      'identity': {'name': 'Dear Diane Phone', 'role': 'chunk_workspace_phone'},
      'workspace_root': workspaceRoot,
      'workspace_id': workspaceRoot,
      'notes_root': notesRoot,
      'workspace_source': 'chunk_workspace_phone',
      'ui_surface': 'chunk_workspace',
      'surface_profile': 'super_tui',
      'agent_profile': 'super_tui',
      'agent_backend': 'super_dan',
      'autonomy_mode': runMode.name,
      'attention_resolution_mode': runMode.name,
      'gui_for': 'dan super-tui',
      'capabilities': [
        'notes',
        'markdown_preview',
        'phone_page_navigation',
        'background_agent_runs',
        'checkpoint_commands',
        'read_only_wireguard_status',
        'preview_artifacts',
        'prompt_logs',
        'screenshot_data_url_attachments',
      ],
      if (attachments.isNotEmpty)
        'appended_attachments': attachments
            .map((attachment) => attachment.toPayload())
            .toList(),
      'active_note': activeNote == null
          ? null
          : {
              'title': activeNote!.title,
              'path': activeNote!.path,
              'relative_path': activeNote!.relativePath,
            },
      'active_file': activeFile == null
          ? null
          : {
              'path': activeFile!.path,
              'relative_path': activeFile!.relativePath,
              'preview': activeFileContent.length > 1400
                  ? activeFileContent.substring(0, 1400)
                  : activeFileContent,
            },
      'wireguard_service': wireGuard == null
          ? null
          : {
              'mode': wireGuard!.mode,
              'interface': wireGuard!.interfaceName,
              'status': wireGuard!.status,
              'conflict_policy': wireGuard!.conflictPolicy,
            },
    };
  }

  Map<String, Object?> _phoneExecutePayload() {
    final attentionMode = runMode.name;
    return {
      'backend': 'super_dan',
      'surface_profile': 'super_tui',
      'background': true,
      'profile_policy': {
        'backend': 'super_dan',
        'surface_profile': 'super_tui',
        'autonomy_mode': attentionMode,
        'attention_resolution_mode': attentionMode,
      },
      'approval_policy': {
        'mode': runMode == WorkRunMode.review
            ? 'ask_on_attention'
            : 'auto_within_workspace',
        'attention_resolution': attentionMode,
      },
      'metadata': {
        'backend': 'super_dan',
        'surface_profile': 'super_tui',
        'compatibility_profile': 'super_tui',
        'surface': 'gui:chunk-workspace',
        'requested_from': 'chunk_workspace_phone',
        'gui_for': 'dan super-tui',
        'selected_backend': 'super_dan',
        'selected_agent': 'native',
        'selected_agent_label': 'Native',
        'autonomy_mode': attentionMode,
        'attention_resolution_mode': attentionMode,
        'attention_resolution_label': runMode.label,
      },
    };
  }

  List<Map<String, String>> _chatHistoryForBackend([
    List<PhoneChatMessage>? source,
  ]) {
    return (source ?? chatMessages)
        .where(
          (message) =>
              message.role == PhoneChatRole.user ||
              message.role == PhoneChatRole.assistant,
        )
        .map(
          (message) => {
            'role': message.role == PhoneChatRole.user ? 'user' : 'assistant',
            'content': message.text,
          },
        )
        .toList();
  }

  Future<void> _sendChatMessage() async {
    final typedPrompt = chatController.text.trim();
    final attachmentsForSend = List<PhoneAttachmentDraft>.from(
      composerAttachments,
    );
    final prompt = typedPrompt.isNotEmpty
        ? typedPrompt
        : attachmentsForSend.length <= 1
        ? 'Please review the attached screenshot.'
        : 'Please review the attached screenshots.';
    if ((typedPrompt.isEmpty && attachmentsForSend.isEmpty) || chatBusy) {
      return;
    }
    chatController.clear();
    final user = PhoneChatMessage(
      id: _newId('user'),
      role: PhoneChatRole.user,
      text: attachmentsForSend.isEmpty
          ? prompt
          : [
              if (typedPrompt.isNotEmpty) typedPrompt,
              attachmentsForSend.length == 1
                  ? 'Attached ${attachmentsForSend.first.name}'
                  : 'Attached ${attachmentsForSend.length} screenshots',
            ].where((value) => value.isNotEmpty).join('\n\n'),
    );
    final assistantId = _newId('assistant');
    final assistant = PhoneChatMessage(
      id: assistantId,
      role: PhoneChatRole.assistant,
      text: activeRunLive
          ? 'Steering the active Diane run...'
          : 'Starting Diane...',
      status: 'working',
    );
    final nextMessages = [...chatMessages, user, assistant];
    final historyForBackend = _chatHistoryForBackend([...chatMessages, user]);
    setState(() {
      chatBusy = true;
      chatStatus = activeRunLive ? 'Steering Diane' : 'Starting Diane';
      chatMessages = nextMessages;
      composerAttachments = const [];
    });

    try {
      if (!_ensureWorkspaceUsableForChat(assistantId)) return;
      final threadReady = await _ensurePhoneThread(prompt);
      if (!threadReady) {
        _replaceChatMessage(
          assistantId,
          'Could not create a phone work session.',
        );
        return;
      }
      unawaited(_saveActiveThreadMessages(nextMessages));

      if (activeRunLive && activeRunId.isNotEmpty && activeTaskId.isNotEmpty) {
        final command = await _postJson(
          activeApiBase,
          '/api/v2/agent-runs/$activeRunId/commands',
          {
            'command': 'append_followup',
            'task_id': activeTaskId,
            'idempotency_key': _newId('phone-append'),
            'payload': {
              'text': prompt,
              'surface_context': _phoneSurfaceContext(
                attachments: attachmentsForSend,
              ),
              'attachments': attachmentsForSend
                  .map((attachment) => attachment.toPayload())
                  .toList(),
              'profile_policy': _phoneExecutePayload()['profile_policy'],
              'approval_policy': _phoneExecutePayload()['approval_policy'],
            },
          },
        );
        final event = command?['event'] is Map<String, dynamic>
            ? command!['event'] as Map<String, dynamic>
            : null;
        final queuedText = _agentEventText(event);
        _replaceChatMessage(
          assistantId,
          queuedText.isEmpty ? 'Steering note queued.' : queuedText,
          status: 'queued',
        );
        setState(() => chatStatus = 'Steering active run');
        return;
      }

      final created = await _postJson(activeApiBase, '/api/v2/agent-runs', {
        'workflow_id': activeWorkflowId,
        'message': prompt,
        'history': historyForBackend,
        'thread_id': activeThreadId,
        'session_id': activeThreadId,
        'mode': 'agent',
        'surface': 'frontend:chunk-workspace',
        'surface_type': 'frontend',
        'surface_id': 'chunk-workspace',
        'surface_context': _phoneSurfaceContext(
          attachments: attachmentsForSend,
        ),
      });
      final control = created?['v2_control_plane'] is Map<String, dynamic>
          ? created!['v2_control_plane'] as Map<String, dynamic>
          : const <String, dynamic>{};
      final taskRef = created?['task_run_ref'] is Map<String, dynamic>
          ? created!['task_run_ref'] as Map<String, dynamic>
          : const <String, dynamic>{};
      final runId = _asString(
        control['run_id'],
        _asString(taskRef['run_id'], ''),
      );
      activeTaskId = _asString(
        control['task_id'],
        _asString(taskRef['task_id'], activeTaskId),
      );
      if (runId.isEmpty) {
        _replaceChatMessage(assistantId, 'Diane queued this request.');
        setState(() => chatStatus = 'Queued');
        return;
      }
      activeRunId = runId;
      activeRunLive = true;
      _replaceChatMessage(
        assistantId,
        'Diane accepted the request.',
        status: 'running',
      );
      setState(() {
        phoneTasksByThread = {
          ...phoneTasksByThread,
          if (activeThreadId.isNotEmpty)
            activeThreadId: PhoneTaskSummary(
              taskId: activeTaskId,
              threadId: activeThreadId,
              status: 'running',
              latestProgress: 'Diane accepted the request.',
              workspaceRoot: workspaceRoot,
              workspaceId: workspaceRoot,
              activeRunId: runId,
              title: prompt,
            ),
        };
      });
      unawaited(_refreshSessions());
      await _postJson(
        activeApiBase,
        '/api/v2/agent-runs/$runId/execute',
        _phoneExecutePayload(),
      );
      setState(() => chatStatus = 'Diane running');
      _startAgentPolling(runId, assistantId);
    } finally {
      if (mounted) setState(() => chatBusy = false);
    }
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
  }

  void _removeComposerAttachment(String id) {
    setState(() {
      composerAttachments = composerAttachments
          .where((attachment) => attachment.id != id)
          .toList();
    });
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
    setState(() {
      chatBusy = true;
      chatStatus = 'Requesting stop';
    });
    final command = await _postJson(
      activeApiBase,
      '/api/v2/agent-runs/$activeRunId/commands',
      {
        'command': 'stop',
        'task_id': activeTaskId,
        'idempotency_key': _newId('phone-stop'),
        'payload': {
          'text': 'Stop requested from Dear Diane Phone.',
          'surface_context': _phoneSurfaceContext(),
        },
      },
    );
    if (!mounted) return;
    final event = command?['event'] is Map<String, dynamic>
        ? command!['event'] as Map<String, dynamic>
        : null;
    final text = _agentEventText(event);
    setState(() {
      chatBusy = false;
      chatStatus = text.isEmpty ? 'Stop requested' : text;
      chatMessages = [
        ...chatMessages,
        PhoneChatMessage(
          id: _newId('status'),
          role: PhoneChatRole.status,
          text: text.isEmpty ? 'Stop requested.' : text,
          status: 'stop_requested',
        ),
      ];
    });
  }

  bool _ensureWorkspaceUsableForChat(String assistantId) {
    if (health != 'ready') {
      _replaceChatMessage(assistantId, 'Dear Diane API is offline. Check Settings.');
      return false;
    }
    return true;
  }

  void _startAgentPolling(String runId, String assistantId) {
    agentPollTimer?.cancel();
    var ticks = 0;
    agentPollTimer = Timer.periodic(const Duration(seconds: 2), (timer) {
      ticks += 1;
      if (ticks > 180) {
        timer.cancel();
        return;
      }
      unawaited(_refreshAgentEvents(runId, assistantId));
    });
    unawaited(_refreshAgentEvents(runId, assistantId));
  }

  Future<void> _refreshAgentEvents(String runId, String assistantId) async {
    final payload = await _getJson(
      activeApiBase,
      '/api/v2/agent-runs/$runId/events',
    );
    if (!mounted || payload == null) return;
    final events = (payload['events'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
    if (events.isEmpty) return;
    final artifacts = events.expand(_previewArtifactsFromEvent).toList();
    if (artifacts.isNotEmpty) {
      _mergePreviewArtifacts(artifacts);
    }
    final latest = events.last;
    final text = _bestAgentEventText(events);
    if (text.isNotEmpty) {
      _replaceChatMessage(
        assistantId,
        text,
        status: _asString(latest['type'], 'running'),
      );
    }
    final type = _asString(latest['type'], '');
    if (activeThreadId.isNotEmpty) {
      setState(() {
        phoneTasksByThread = {
          ...phoneTasksByThread,
          activeThreadId: PhoneTaskSummary(
            taskId: _asString(latest['task_id'], activeTaskId),
            threadId: activeThreadId,
            status: type.isEmpty ? 'running' : type,
            latestProgress: text,
            workspaceRoot: workspaceRoot,
            workspaceId: workspaceRoot,
            activeRunId: runId,
            title: text,
          ),
        };
      });
    }
    if (type == 'completed' ||
        type == 'failed' ||
        type == 'blocked' ||
        type == 'stopped') {
      agentPollTimer?.cancel();
      setState(() {
        activeRunLive = false;
        chatStatus = type == 'completed' ? 'Ready' : type;
      });
      unawaited(_refreshSessions());
    }
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
    var dialogRootSuggestions = workspaceRootSuggestions;
    Timer? dialogRootSuggestionDebounce;
    try {
      await showDialog<void>(
        context: context,
        builder: (context) {
          return StatefulBuilder(
            builder: (context, setDialogState) {
              Future<void> refreshDialogRootSuggestions(String value) async {
                final suggestions = await _fetchWorkspaceRootSuggestions(value);
                if (!context.mounted) return;
                setDialogState(() => dialogRootSuggestions = suggestions);
              }

              void scheduleDialogRootSuggestions(String value) {
                dialogRootSuggestionDebounce?.cancel();
                dialogRootSuggestionDebounce = Timer(
                  const Duration(milliseconds: 220),
                  () => unawaited(refreshDialogRootSuggestions(value)),
                );
              }

              Future<void> runAndRefresh(
                Future<Object?> Function() action,
              ) async {
                await action();
                if (context.mounted) setDialogState(() {});
              }

              return AlertDialog(
                title: const Text('Settings'),
                content: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      TextField(
                        controller: apiBaseController,
                        keyboardType: TextInputType.url,
                        decoration: const InputDecoration(
                          labelText: 'Dear Diane API base',
                          hintText: 'http://10.77.77.2:8000',
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: accessTokenController,
                        obscureText: true,
                        enableSuggestions: false,
                        autocorrect: false,
                        decoration: const InputDecoration(
                          labelText: 'Access token',
                          hintText: 'Paste token if your backend requires one',
                        ),
                      ),
                      const SizedBox(height: 16),
                      TextField(
                        controller: workspaceRootController,
                        onChanged: scheduleDialogRootSuggestions,
                        decoration: const InputDecoration(
                          labelText: 'Workspace root',
                          hintText:
                              '/Users/lizhi/Downloads/local_projects/deep-agent-network',
                        ),
                      ),
                      if (dialogRootSuggestions.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        DecoratedBox(
                          decoration: BoxDecoration(
                            color: Colors.white,
                            border: Border.all(color: const Color(0xffded7ca)),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Column(
                            children: [
                              for (final suggestion
                                  in dialogRootSuggestions.take(4))
                                ListTile(
                                  dense: true,
                                  leading: const Icon(
                                    Icons.folder_open_outlined,
                                  ),
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
                                  onTap: () => runAndRefresh(() async {
                                    workspaceRootController.text =
                                        suggestion.path;
                                    dialogRootSuggestions = const [];
                                    await _applyWorkspaceRoot(suggestion.path);
                                    return null;
                                  }),
                                ),
                            ],
                          ),
                        ),
                      ],
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              workspaceRoot.isEmpty
                                  ? 'Default backend workspace'
                                  : workspaceRoot,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                          TextButton.icon(
                            onPressed: () => runAndRefresh(() async {
                              await _applyWorkspaceRoot(
                                workspaceRootController.text,
                              );
                              return null;
                            }),
                            icon: const Icon(Icons.folder_open_outlined),
                            label: const Text('Set'),
                          ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          _StatusChip(
                            icon: Icons.vpn_key_outlined,
                            label: 'WG',
                            value: _wireGuardLabel,
                          ),
                          _StatusChip(
                            icon: Icons.dns_outlined,
                            label: 'API',
                            value: health,
                          ),
                          _StatusChip(
                            icon: Icons.route_outlined,
                            label: 'Mode',
                            value: wireGuard?.mode ?? 'dan-phone',
                          ),
                          _StatusChip(
                            icon: Icons.link_outlined,
                            label: 'Host',
                            value:
                                Uri.tryParse(activeApiBase)?.host ??
                                activeApiBase,
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Text(statusDetail),
                      if (wireGuard != null) ...[
                        const SizedBox(height: 8),
                        Text(
                          wireGuard!.conflictPolicy,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                      const SizedBox(height: 16),
                      Text(
                        'Phone VPN',
                        style: Theme.of(context).textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        crossAxisAlignment: WrapCrossAlignment.center,
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
                                : phoneWireGuard?.state ?? 'not-started',
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      if (phoneWireGuardConfig.trim().isEmpty) ...[
                        Text(
                          'No phone VPN config saved. Force start needs a Dear Diane phone WireGuard peer config first.',
                          style: Theme.of(context).textTheme.bodySmall
                              ?.copyWith(
                                color: Theme.of(context).colorScheme.error,
                              ),
                        ),
                        const SizedBox(height: 8),
                      ],
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          FilledButton.icon(
                            onPressed:
                                phoneWireGuardBusy ||
                                    phoneWireGuardConfig.trim().isEmpty
                                ? null
                                : () => runAndRefresh(
                                    () => _forceStartPhoneWireGuard(),
                                  ),
                            icon: const Icon(Icons.power_settings_new),
                            label: const Text('Force start'),
                          ),
                          OutlinedButton.icon(
                            onPressed: phoneWireGuardBusy
                                ? null
                                : () => runAndRefresh(
                                    () => _forceStopPhoneWireGuard(),
                                  ),
                            icon: const Icon(Icons.power_off_outlined),
                            label: const Text('Force off'),
                          ),
                          TextButton.icon(
                            onPressed: phoneWireGuardBusy
                                ? null
                                : () => runAndRefresh(
                                    () => _refreshPhoneWireGuardStatus(),
                                  ),
                            icon: const Icon(Icons.refresh),
                            label: const Text('VPN status'),
                          ),
                          TextButton.icon(
                            onPressed: phoneWireGuardBusy
                                ? null
                                : () => runAndRefresh(
                                    () => _pastePhoneWireGuardConfig(),
                                  ),
                            icon: const Icon(Icons.content_paste),
                            label: const Text('Paste config'),
                          ),
                          TextButton.icon(
                            onPressed: () {
                              Navigator.of(context).pop();
                              unawaited(_savePhoneSettings());
                              unawaited(_openPhoneWireGuardDialog());
                            },
                            icon: const Icon(Icons.vpn_lock_outlined),
                            label: const Text('Config'),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
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
                    child: const Text('Cancel'),
                  ),
                  TextButton(
                    onPressed: () {
                      Navigator.of(context).pop();
                      unawaited(_savePhoneSettings());
                      unawaited(_refreshStatus());
                    },
                    child: const Text('Refresh'),
                  ),
                  FilledButton(
                    onPressed: () {
                      final next = _normalizeApiBase(apiBaseController.text);
                      activeApiBase = next;
                      workspaceRoot = workspaceRootController.text.trim();
                      unawaited(_savePhoneSettings());
                      Navigator.of(context).pop();
                      unawaited(_refreshStatus(preferredApiBase: next));
                    },
                    child: const Text('Test'),
                  ),
                ],
              );
            },
          );
        },
      );
    } finally {
      dialogRootSuggestionDebounce?.cancel();
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

  String get _wireGuardLabel {
    if (loadingWireGuard) {
      return phoneWireGuard == null ? 'checking' : 'api-check';
    }
    if (health != 'ready') {
      return phoneWireGuard?.state == 'up' ? 'api-offline' : 'no-api';
    }
    return wireGuard?.status ?? 'unknown';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: scaffoldKey,
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
      body: SafeArea(
        child: Column(
          children: [
            _WorkspaceHeader(
              mode: mode,
              onModeChanged: (next) => setState(() => mode = next),
              onOpenSessions: () => scaffoldKey.currentState?.openDrawer(),
              onConfigureBackend: _openBackendDialog,
            ),
            Expanded(
              child: mode == WorkspaceMode.work
                  ? _WorkSurface(
                      page: workPage,
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
                      chatStatus: chatStatus,
                      chatBusy: chatBusy,
                      activeRunLive: activeRunLive,
                      composerAttachments: composerAttachments,
                      loadingPromptLog: loadingPromptLog,
                      onRunModeChanged: _changeRunMode,
                      onPasteScreenshot: _pasteScreenshotAttachment,
                      onRemoveAttachment: _removeComposerAttachment,
                      onStopRun: _stopActiveRun,
                      onOpenPromptLog: _openPromptLogPreview,
                      onTogglePreviewArtifacts: () => setState(
                        () => previewArtifactsExpanded =
                            !previewArtifactsExpanded,
                      ),
                      onFetchRootSuggestions: _fetchWorkspaceRootSuggestions,
                      onApplyWorkspaceRoot: _applyWorkspaceRoot,
                      onRefreshFiles: _refreshWorkspaceFiles,
                      onSelectFile: _selectWorkspaceFile,
                      onSelectArtifact: _selectPreviewArtifact,
                      onSendChat: _sendChatMessage,
                    )
                  : _NotesSurface(
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
        ),
      ),
      bottomNavigationBar: mode == WorkspaceMode.work
          ? NavigationBar(
              selectedIndex: workPage.index,
              onDestinationSelected: (index) {
                setState(() => workPage = WorkPage.values[index]);
              },
              destinations: const [
                NavigationDestination(
                  icon: Icon(Icons.chat_bubble_outline),
                  selectedIcon: Icon(Icons.chat_bubble),
                  label: 'Chat',
                ),
                NavigationDestination(
                  icon: Icon(Icons.folder_outlined),
                  selectedIcon: Icon(Icons.folder),
                  label: 'Files',
                ),
                NavigationDestination(
                  icon: Icon(Icons.visibility_outlined),
                  selectedIcon: Icon(Icons.visibility),
                  label: 'Preview',
                ),
              ],
            )
          : NavigationBar(
              selectedIndex: notesPage.index,
              onDestinationSelected: (index) {
                _saveActiveNoteReadScroll();
                _saveActiveNoteEditScroll();
                final nextPage = NotesPage.values[index];
                if (notesPage == NotesPage.edit && nextPage != NotesPage.edit) {
                  noteAutosaveTimer?.cancel();
                  unawaited(_saveActiveNote());
                }
                setState(() => notesPage = nextPage);
                final path = activeNote?.path;
                if (path != null && path.isNotEmpty) {
                  _restoreNoteScroll(nextPage, path);
                }
              },
              destinations: const [
                NavigationDestination(
                  icon: Icon(Icons.menu_book_outlined),
                  selectedIcon: Icon(Icons.menu_book),
                  label: 'Pages',
                ),
                NavigationDestination(
                  icon: Icon(Icons.edit_note_outlined),
                  selectedIcon: Icon(Icons.edit_note),
                  label: 'Edit',
                ),
                NavigationDestination(
                  icon: Icon(Icons.article_outlined),
                  selectedIcon: Icon(Icons.article),
                  label: 'Read',
                ),
              ],
            ),
    );
  }
}

class _WorkspaceHeader extends StatelessWidget {
  const _WorkspaceHeader({
    required this.mode,
    required this.onModeChanged,
    required this.onOpenSessions,
    required this.onConfigureBackend,
  });

  final WorkspaceMode mode;
  final ValueChanged<WorkspaceMode> onModeChanged;
  final VoidCallback onOpenSessions;
  final VoidCallback onConfigureBackend;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
      decoration: const BoxDecoration(
        color: Color(0xfffcfaf5),
        border: Border(bottom: BorderSide(color: Color(0xffded7ca))),
      ),
      child: Row(
        children: [
          IconButton(
            onPressed: onOpenSessions,
            tooltip: 'Sessions',
            icon: const Icon(Icons.menu),
          ),
          Expanded(
            child: Center(
              child: SegmentedButton<WorkspaceMode>(
                segments: const [
                  ButtonSegment(
                    value: WorkspaceMode.work,
                    icon: Icon(Icons.work_outline),
                    label: Text('Work'),
                  ),
                  ButtonSegment(
                    value: WorkspaceMode.notes,
                    icon: Icon(Icons.notes_outlined),
                    label: Text('Notes'),
                  ),
                ],
                selected: {mode},
                onSelectionChanged: (selection) =>
                    onModeChanged(selection.first),
              ),
            ),
          ),
          IconButton(
            onPressed: onConfigureBackend,
            tooltip: 'Settings',
            icon: const Icon(Icons.settings_outlined),
          ),
        ],
      ),
    );
  }
}

class _SessionDrawer extends StatefulWidget {
  const _SessionDrawer({
    required this.groups,
    required this.tasksByThread,
    required this.activeThreadId,
    required this.activeWorkspaceRoot,
    required this.loading,
    required this.onRefresh,
    required this.onCreateWorkspace,
    required this.onSelectWorkspace,
    required this.onCreateSession,
    required this.onSelectSession,
  });

  final List<PhoneWorkspaceSessionGroup> groups;
  final Map<String, PhoneTaskSummary> tasksByThread;
  final String activeThreadId;
  final String activeWorkspaceRoot;
  final bool loading;
  final Future<void> Function() onRefresh;
  final Future<void> Function() onCreateWorkspace;
  final Future<void> Function(String root) onSelectWorkspace;
  final Future<void> Function(String root) onCreateSession;
  final Future<void> Function(
    PhoneSessionSummary session, {
    String workspaceRootOverride,
  })
  onSelectSession;

  @override
  State<_SessionDrawer> createState() => _SessionDrawerState();
}

class _SessionDrawerState extends State<_SessionDrawer> {
  String query = '';
  final Set<String> expandedRoots = {};

  @override
  void didUpdateWidget(covariant _SessionDrawer oldWidget) {
    super.didUpdateWidget(oldWidget);
    for (final group in widget.groups) {
      if (group.root == widget.activeWorkspaceRoot || group.archived) {
        expandedRoots.add(_groupKey(group));
      }
    }
  }

  String _groupKey(PhoneWorkspaceSessionGroup group) {
    return group.archived ? 'archived' : group.root;
  }

  bool _sessionMatchesQuery(PhoneSessionSummary session, String normalized) {
    if (normalized.isEmpty) return true;
    final task = widget.tasksByThread[session.id];
    return session.displayTitle.toLowerCase().contains(normalized) ||
        session.id.toLowerCase().contains(normalized) ||
        session.workflowId.toLowerCase().contains(normalized) ||
        (task?.latestProgress.toLowerCase().contains(normalized) ?? false);
  }

  List<PhoneWorkspaceSessionGroup> _visibleGroups() {
    final normalized = query.trim().toLowerCase();
    if (normalized.isEmpty) return widget.groups;
    return widget.groups
        .map(
          (group) => PhoneWorkspaceSessionGroup(
            root: group.root,
            name: group.name,
            archived: group.archived,
            sessions: group.sessions
                .where((session) => _sessionMatchesQuery(session, normalized))
                .toList(),
          ),
        )
        .where(
          (group) =>
              group.sessions.isNotEmpty ||
              group.name.toLowerCase().contains(normalized) ||
              group.root.toLowerCase().contains(normalized),
        )
        .toList();
  }

  Future<void> _runAndClose(Future<void> Function() action) async {
    Navigator.of(context).pop();
    await action();
  }

  @override
  Widget build(BuildContext context) {
    final visibleGroups = _visibleGroups();
    return Drawer(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Sessions',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: 'Refresh sessions',
                  onPressed: widget.loading
                      ? null
                      : () => unawaited(widget.onRefresh()),
                  icon: widget.loading
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.refresh),
                ),
                IconButton.filledTonal(
                  tooltip: 'New workspace',
                  onPressed: () =>
                      unawaited(_runAndClose(widget.onCreateWorkspace)),
                  icon: const Icon(Icons.create_new_folder_outlined),
                ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              onChanged: (value) => setState(() => query = value),
              decoration: InputDecoration(
                hintText: 'Search sessions',
                prefixIcon: const Icon(Icons.search),
                filled: true,
                fillColor: Colors.white,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            ),
            const SizedBox(height: 12),
            if (widget.loading) const LinearProgressIndicator(),
            if (!widget.loading && visibleGroups.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Text('No sessions found.'),
              ),
            for (final group in visibleGroups)
              _SessionWorkspaceGroupCard(
                group: group,
                tasksByThread: widget.tasksByThread,
                activeThreadId: widget.activeThreadId,
                activeWorkspaceRoot: widget.activeWorkspaceRoot,
                expanded:
                    expandedRoots.contains(_groupKey(group)) ||
                    group.root == widget.activeWorkspaceRoot,
                onExpandedChanged: (expanded) {
                  setState(() {
                    if (expanded) {
                      expandedRoots.add(_groupKey(group));
                    } else {
                      expandedRoots.remove(_groupKey(group));
                    }
                  });
                },
                onSelectWorkspace: group.archived
                    ? null
                    : () => _runAndClose(
                        () => widget.onSelectWorkspace(group.root),
                      ),
                onCreateSession: group.archived
                    ? null
                    : () => _runAndClose(
                        () => widget.onCreateSession(group.root),
                      ),
                onSelectSession: (session) => _runAndClose(
                  () => widget.onSelectSession(
                    session,
                    workspaceRootOverride: group.root,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SessionWorkspaceGroupCard extends StatelessWidget {
  const _SessionWorkspaceGroupCard({
    required this.group,
    required this.tasksByThread,
    required this.activeThreadId,
    required this.activeWorkspaceRoot,
    required this.expanded,
    required this.onExpandedChanged,
    required this.onSelectWorkspace,
    required this.onCreateSession,
    required this.onSelectSession,
  });

  final PhoneWorkspaceSessionGroup group;
  final Map<String, PhoneTaskSummary> tasksByThread;
  final String activeThreadId;
  final String activeWorkspaceRoot;
  final bool expanded;
  final ValueChanged<bool> onExpandedChanged;
  final VoidCallback? onSelectWorkspace;
  final VoidCallback? onCreateSession;
  final ValueChanged<PhoneSessionSummary> onSelectSession;

  @override
  Widget build(BuildContext context) {
    final activeWorkspace =
        !group.archived && group.root == activeWorkspaceRoot;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: activeWorkspace ? const Color(0xffe9f3ed) : Colors.white,
          border: Border.all(
            color: activeWorkspace
                ? const Color(0xff9cb9a9)
                : const Color(0xffded7ca),
          ),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          children: [
            ListTile(
              contentPadding: const EdgeInsets.fromLTRB(12, 4, 8, 4),
              leading: Icon(
                group.archived
                    ? Icons.archive_outlined
                    : Icons.folder_copy_outlined,
              ),
              title: Text(
                group.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              subtitle: Text(
                group.archived
                    ? '${group.sessions.length} archived'
                    : group.root.isEmpty
                    ? '${group.sessions.length} sessions'
                    : group.root,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              onTap: group.archived ? null : onSelectWorkspace,
              trailing: Wrap(
                spacing: 2,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (!group.archived)
                    IconButton(
                      tooltip: 'New session in ${group.name}',
                      onPressed: onCreateSession,
                      icon: const Icon(Icons.add_comment_outlined),
                    ),
                  IconButton(
                    tooltip: expanded
                        ? 'Collapse workspace'
                        : 'Expand workspace',
                    onPressed: () => onExpandedChanged(!expanded),
                    icon: Icon(
                      expanded
                          ? Icons.keyboard_arrow_down
                          : Icons.keyboard_arrow_right,
                    ),
                  ),
                ],
              ),
            ),
            if (expanded) ...[
              if (group.sessions.isEmpty)
                const Padding(
                  padding: EdgeInsets.fromLTRB(16, 0, 16, 12),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text('No sessions yet.'),
                  ),
                ),
              for (final session in group.sessions)
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                  child: _SessionTile(
                    session: session,
                    task: tasksByThread[session.id],
                    active: session.id == activeThreadId,
                    archived: group.archived,
                    onTap: () => onSelectSession(session),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SessionTile extends StatelessWidget {
  const _SessionTile({
    required this.session,
    required this.task,
    required this.active,
    required this.archived,
    required this.onTap,
  });

  final PhoneSessionSummary session;
  final PhoneTaskSummary? task;
  final bool active;
  final bool archived;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final status =
        task?.status ?? (session.messageCount == 0 ? 'blank' : 'saved');
    final updated = _compactDateTimeLabel(session.updatedAt);
    final detail = [
      status,
      if (session.messageCount > 0) '${session.messageCount} messages',
      if (updated.isNotEmpty) updated,
    ].join(' · ');
    final live = task?.isLive == true;
    return ListTile(
      dense: true,
      enabled: !archived,
      selected: active,
      tileColor: Colors.white,
      selectedTileColor: const Color(0xffd7eadf),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(8),
        side: const BorderSide(color: Color(0xffded7ca)),
      ),
      leading: Icon(
        live ? Icons.radio_button_checked : Icons.chat_bubble_outline,
        color: live ? const Color(0xff176b5b) : null,
      ),
      title: Text(
        session.displayTitle,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(detail, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: const Icon(Icons.chevron_right),
      onTap: archived ? null : onTap,
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({
    required this.icon,
    required this.label,
    required this.value,
  });

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xffece6da),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16),
          const SizedBox(width: 6),
          Text(
            '$label: $value',
            style: Theme.of(context).textTheme.labelMedium,
          ),
        ],
      ),
    );
  }
}

class _WorkSurface extends StatelessWidget {
  const _WorkSurface({
    required this.page,
    required this.runMode,
    required this.workspaceRoot,
    required this.workspaceRootController,
    required this.files,
    required this.activeFile,
    required this.activeFileContent,
    required this.previewTitle,
    required this.previewStatus,
    required this.previewContent,
    required this.previewArtifacts,
    required this.previewArtifactsExpanded,
    required this.fileStatus,
    required this.loadingFiles,
    required this.chatMessages,
    required this.chatController,
    required this.chatStatus,
    required this.chatBusy,
    required this.activeRunLive,
    required this.composerAttachments,
    required this.loadingPromptLog,
    required this.onRunModeChanged,
    required this.onPasteScreenshot,
    required this.onRemoveAttachment,
    required this.onStopRun,
    required this.onOpenPromptLog,
    required this.onTogglePreviewArtifacts,
    required this.onFetchRootSuggestions,
    required this.onApplyWorkspaceRoot,
    required this.onRefreshFiles,
    required this.onSelectFile,
    required this.onSelectArtifact,
    required this.onSendChat,
  });

  final WorkPage page;
  final WorkRunMode runMode;
  final String workspaceRoot;
  final TextEditingController workspaceRootController;
  final List<WorkspaceFileEntry> files;
  final WorkspaceFileEntry? activeFile;
  final String activeFileContent;
  final String previewTitle;
  final String previewStatus;
  final String previewContent;
  final List<PhonePreviewArtifact> previewArtifacts;
  final bool previewArtifactsExpanded;
  final String fileStatus;
  final bool loadingFiles;
  final List<PhoneChatMessage> chatMessages;
  final TextEditingController chatController;
  final String chatStatus;
  final bool chatBusy;
  final bool activeRunLive;
  final List<PhoneAttachmentDraft> composerAttachments;
  final bool loadingPromptLog;
  final ValueChanged<WorkRunMode> onRunModeChanged;
  final VoidCallback onPasteScreenshot;
  final ValueChanged<String> onRemoveAttachment;
  final VoidCallback onStopRun;
  final VoidCallback onOpenPromptLog;
  final VoidCallback onTogglePreviewArtifacts;
  final Future<List<WorkspaceRootSuggestion>> Function(String)
  onFetchRootSuggestions;
  final Future<void> Function([String? value]) onApplyWorkspaceRoot;
  final Future<void> Function({String? apiBase, String? root}) onRefreshFiles;
  final ValueChanged<WorkspaceFileEntry> onSelectFile;
  final ValueChanged<PhonePreviewArtifact> onSelectArtifact;
  final VoidCallback onSendChat;

  @override
  Widget build(BuildContext context) {
    return switch (page) {
      WorkPage.chat => _ChatPage(
        workspaceRoot: workspaceRoot,
        messages: chatMessages,
        controller: chatController,
        status: chatStatus,
        busy: chatBusy,
        activeRunLive: activeRunLive,
        runMode: runMode,
        attachments: composerAttachments,
        previewArtifacts: previewArtifacts,
        previewArtifactsExpanded: previewArtifactsExpanded,
        loadingPromptLog: loadingPromptLog,
        onRunModeChanged: onRunModeChanged,
        onPasteScreenshot: onPasteScreenshot,
        onRemoveAttachment: onRemoveAttachment,
        onStopRun: onStopRun,
        onOpenPromptLog: onOpenPromptLog,
        onTogglePreviewArtifacts: onTogglePreviewArtifacts,
        onSelectArtifact: onSelectArtifact,
        onSend: onSendChat,
      ),
      WorkPage.files => _FilesPage(
        workspaceRoot: workspaceRoot,
        controller: workspaceRootController,
        files: files,
        status: fileStatus,
        loading: loadingFiles,
        onFetchRootSuggestions: onFetchRootSuggestions,
        onApplyWorkspaceRoot: onApplyWorkspaceRoot,
        onRefresh: onRefreshFiles,
        onSelectFile: onSelectFile,
      ),
      WorkPage.preview => _FilePreviewPage(
        activeFile: activeFile,
        title: previewTitle,
        content: previewContent.isNotEmpty ? previewContent : activeFileContent,
        status: previewStatus.isNotEmpty ? previewStatus : fileStatus,
        previewArtifacts: previewArtifacts,
        previewArtifactsExpanded: previewArtifactsExpanded,
        onTogglePreviewArtifacts: onTogglePreviewArtifacts,
        onSelectArtifact: onSelectArtifact,
      ),
    };
  }
}

class _NotesSurface extends StatelessWidget {
  const _NotesSurface({
    required this.page,
    required this.notes,
    required this.activeNote,
    required this.activePreview,
    required this.notesRoot,
    required this.noteContent,
    required this.noteStatus,
    required this.notePreviewStatus,
    required this.noteSaveStatus,
    required this.loading,
    required this.editController,
    required this.readScrollController,
    required this.editScrollController,
    required this.onSaveNote,
    required this.onSelectNote,
    required this.onRefresh,
  });

  final NotesPage page;
  final List<WorkspaceNoteSummary> notes;
  final WorkspaceNoteSummary? activeNote;
  final WorkspaceNotePreview? activePreview;
  final String notesRoot;
  final String noteContent;
  final String noteStatus;
  final String notePreviewStatus;
  final String noteSaveStatus;
  final bool loading;
  final TextEditingController editController;
  final ScrollController readScrollController;
  final ScrollController editScrollController;
  final Future<void> Function() onSaveNote;
  final ValueChanged<WorkspaceNoteSummary> onSelectNote;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    return switch (page) {
      NotesPage.pages => _NotesListPage(
        notes: notes,
        activeNote: activeNote,
        notesRoot: notesRoot,
        noteStatus: noteStatus,
        loading: loading,
        onSelectNote: onSelectNote,
        onRefresh: onRefresh,
      ),
      NotesPage.edit => _NoteEditPage(
        controller: editController,
        scrollController: editScrollController,
        notes: notes,
        activeNote: activeNote,
        noteStatus: noteStatus,
        saveStatus: noteSaveStatus,
        onSave: onSaveNote,
      ),
      NotesPage.read => _NoteReadPage(
        activeNote: activeNote,
        preview: activePreview,
        fallbackContent: noteContent,
        status: notePreviewStatus,
        scrollController: readScrollController,
      ),
    };
  }
}

class _NotesListPage extends StatefulWidget {
  const _NotesListPage({
    required this.notes,
    required this.activeNote,
    required this.notesRoot,
    required this.noteStatus,
    required this.loading,
    required this.onSelectNote,
    required this.onRefresh,
  });

  final List<WorkspaceNoteSummary> notes;
  final WorkspaceNoteSummary? activeNote;
  final String notesRoot;
  final String noteStatus;
  final bool loading;
  final ValueChanged<WorkspaceNoteSummary> onSelectNote;
  final VoidCallback onRefresh;

  @override
  State<_NotesListPage> createState() => _NotesListPageState();
}

class _NotesListPageState extends State<_NotesListPage> {
  String query = '';

  @override
  Widget build(BuildContext context) {
    final normalizedQuery = query.trim().toLowerCase();
    final visibleNotes = normalizedQuery.isEmpty
        ? widget.notes
        : widget.notes
              .where(
                (note) =>
                    note.title.toLowerCase().contains(normalizedQuery) ||
                    note.relativePath.toLowerCase().contains(normalizedQuery) ||
                    note.section.toLowerCase().contains(normalizedQuery) ||
                    note.tags.any(
                      (tag) => tag.toLowerCase().contains(normalizedQuery),
                    ),
              )
              .toList();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Pages',
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
            ),
            IconButton(
              tooltip: 'Refresh pages',
              onPressed: widget.onRefresh,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          widget.notesRoot.isEmpty ? widget.noteStatus : widget.notesRoot,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 12),
        TextField(
          onChanged: (value) => setState(() => query = value),
          decoration: InputDecoration(
            hintText: 'Search knowledge base',
            prefixIcon: const Icon(Icons.search),
            filled: true,
            fillColor: Colors.white,
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
          ),
        ),
        const SizedBox(height: 12),
        if (widget.loading) const LinearProgressIndicator(),
        if (!widget.loading && visibleNotes.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(widget.noteStatus),
          ),
        for (final note in visibleNotes)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              selected: widget.activeNote?.path == note.path,
              tileColor: Colors.white,
              selectedTileColor: const Color(0xffd7eadf),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
                side: const BorderSide(color: Color(0xffded7ca)),
              ),
              leading: const Icon(Icons.article_outlined),
              title: Text(
                note.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              subtitle: Text(
                [
                  note.relativePath,
                  if (note.tags.isNotEmpty) note.tags.take(2).join(', '),
                ].where((value) => value.isNotEmpty).join(' · '),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => widget.onSelectNote(note),
            ),
          ),
      ],
    );
  }
}

class _ChatPage extends StatelessWidget {
  const _ChatPage({
    required this.workspaceRoot,
    required this.messages,
    required this.controller,
    required this.status,
    required this.busy,
    required this.activeRunLive,
    required this.runMode,
    required this.attachments,
    required this.previewArtifacts,
    required this.previewArtifactsExpanded,
    required this.loadingPromptLog,
    required this.onRunModeChanged,
    required this.onPasteScreenshot,
    required this.onRemoveAttachment,
    required this.onStopRun,
    required this.onOpenPromptLog,
    required this.onTogglePreviewArtifacts,
    required this.onSelectArtifact,
    required this.onSend,
  });

  final String workspaceRoot;
  final List<PhoneChatMessage> messages;
  final TextEditingController controller;
  final String status;
  final bool busy;
  final bool activeRunLive;
  final WorkRunMode runMode;
  final List<PhoneAttachmentDraft> attachments;
  final List<PhonePreviewArtifact> previewArtifacts;
  final bool previewArtifactsExpanded;
  final bool loadingPromptLog;
  final ValueChanged<WorkRunMode> onRunModeChanged;
  final VoidCallback onPasteScreenshot;
  final ValueChanged<String> onRemoveAttachment;
  final VoidCallback onStopRun;
  final VoidCallback onOpenPromptLog;
  final VoidCallback onTogglePreviewArtifacts;
  final ValueChanged<PhonePreviewArtifact> onSelectArtifact;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Work Chat',
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            workspaceRoot.isEmpty ? 'Default workspace' : workspaceRoot,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: SegmentedButton<WorkRunMode>(
                  showSelectedIcon: false,
                  segments: const [
                    ButtonSegment(
                      value: WorkRunMode.auto,
                      icon: Icon(Icons.auto_awesome_outlined),
                      label: Text('Auto'),
                    ),
                    ButtonSegment(
                      value: WorkRunMode.review,
                      icon: Icon(Icons.rate_review_outlined),
                      label: Text('Review'),
                    ),
                  ],
                  selected: {runMode},
                  onSelectionChanged: busy
                      ? null
                      : (selection) => onRunModeChanged(selection.first),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                tooltip: 'Paste screenshot data URL',
                onPressed: busy ? null : onPasteScreenshot,
                icon: const Icon(Icons.add_photo_alternate_outlined),
              ),
              IconButton.filledTonal(
                tooltip: 'Prompt log',
                onPressed: loadingPromptLog ? null : onOpenPromptLog,
                icon: loadingPromptLog
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.receipt_long_outlined),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            runMode.description,
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          Expanded(
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final message in messages) ...[
                    _MessageBubble(
                      title: switch (message.role) {
                        PhoneChatRole.user => 'You',
                        PhoneChatRole.assistant => 'Diane',
                        PhoneChatRole.status => 'Status',
                      },
                      text: message.text,
                      status: message.status,
                      alignRight: message.role == PhoneChatRole.user,
                    ),
                    const SizedBox(height: 10),
                  ],
                ],
              ),
            ),
          ),
          if (previewArtifacts.isNotEmpty) ...[
            _PreviewArtifactShelf(
              artifacts: previewArtifacts,
              expanded: previewArtifactsExpanded,
              onToggle: onTogglePreviewArtifacts,
              onSelect: onSelectArtifact,
            ),
            const SizedBox(height: 8),
          ],
          Text(status, style: Theme.of(context).textTheme.bodySmall),
          if (attachments.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final attachment in attachments)
                  InputChip(
                    avatar: const Icon(Icons.image_outlined, size: 18),
                    label: Text(
                      [
                        attachment.name,
                        _formatBytes(attachment.sizeBytes),
                      ].where((value) => value.isNotEmpty).join(' · '),
                    ),
                    onDeleted: () => onRemoveAttachment(attachment.id),
                  ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: controller,
            builder: (context, value, _) {
              final showStop =
                  activeRunLive &&
                  value.text.trim().isEmpty &&
                  attachments.isEmpty;
              return TextField(
                controller: controller,
                minLines: 1,
                maxLines: 4,
                enabled: !busy,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => showStop ? onStopRun() : onSend(),
                decoration: InputDecoration(
                  hintText: activeRunLive
                      ? 'Steer or queue the active run'
                      : 'Ask Diane to work in this workspace',
                  filled: true,
                  fillColor: Colors.white,
                  suffixIcon: IconButton(
                    tooltip: showStop ? 'Stop active run' : 'Send',
                    icon: busy
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Icon(
                            showStop
                                ? Icons.stop_circle_outlined
                                : Icons.send_outlined,
                          ),
                    onPressed: busy ? null : (showStop ? onStopRun : onSend),
                  ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8),
                  ),
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}

class _PreviewArtifactShelf extends StatelessWidget {
  const _PreviewArtifactShelf({
    required this.artifacts,
    required this.expanded,
    required this.onToggle,
    required this.onSelect,
  });

  final List<PhonePreviewArtifact> artifacts;
  final bool expanded;
  final VoidCallback onToggle;
  final ValueChanged<PhonePreviewArtifact> onSelect;

  @override
  Widget build(BuildContext context) {
    if (artifacts.isEmpty) return const SizedBox.shrink();
    return DecoratedBox(
      decoration: BoxDecoration(
        color: const Color(0xfffcfaf5),
        border: Border.all(color: const Color(0xffded7ca)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 8, 8, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                const Icon(Icons.inventory_2_outlined, size: 18),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    artifacts.length == 1
                        ? 'Output'
                        : '${artifacts.length} outputs',
                    style: Theme.of(context).textTheme.labelLarge,
                  ),
                ),
                IconButton(
                  tooltip: expanded ? 'Collapse outputs' : 'Show outputs',
                  onPressed: onToggle,
                  icon: Icon(
                    expanded
                        ? Icons.keyboard_arrow_down
                        : Icons.keyboard_arrow_right,
                  ),
                ),
              ],
            ),
            if (expanded)
              for (final artifact in artifacts.take(5))
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(_artifactIcon(artifact)),
                  title: Text(
                    artifact.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(
                    artifact.path.isNotEmpty ? artifact.path : artifact.url,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => onSelect(artifact),
                ),
          ],
        ),
      ),
    );
  }
}

IconData _artifactIcon(PhonePreviewArtifact artifact) {
  final target = '${artifact.kind} ${artifact.path} ${artifact.url}'
      .toLowerCase();
  if (target.contains('.html') || target.contains('text/html')) {
    return Icons.web_asset_outlined;
  }
  if (target.contains('.pdf') || target.contains('pdf')) {
    return Icons.picture_as_pdf_outlined;
  }
  if (RegExp(r'\.(png|jpe?g|webp|gif)\b').hasMatch(target) ||
      target.contains('image')) {
    return Icons.image_outlined;
  }
  if (RegExp(r'\.(md|markdown)\b').hasMatch(target)) {
    return Icons.article_outlined;
  }
  return Icons.description_outlined;
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({
    required this.title,
    required this.text,
    this.status = '',
    this.alignRight = false,
  });

  final String title;
  final String text;
  final String status;
  final bool alignRight;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: alignRight ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 320),
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: alignRight ? const Color(0xffd7eadf) : Colors.white,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: const Color(0xffded7ca)),
          ),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: Theme.of(context).textTheme.labelLarge),
                if (status.isNotEmpty)
                  Text(status, style: Theme.of(context).textTheme.labelSmall),
                const SizedBox(height: 4),
                alignRight ? Text(text) : _MarkdownPreviewBody(markdown: text),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _FilesPage extends StatefulWidget {
  const _FilesPage({
    required this.workspaceRoot,
    required this.controller,
    required this.files,
    required this.status,
    required this.loading,
    required this.onFetchRootSuggestions,
    required this.onApplyWorkspaceRoot,
    required this.onRefresh,
    required this.onSelectFile,
  });

  final String workspaceRoot;
  final TextEditingController controller;
  final List<WorkspaceFileEntry> files;
  final String status;
  final bool loading;
  final Future<List<WorkspaceRootSuggestion>> Function(String)
  onFetchRootSuggestions;
  final Future<void> Function([String? value]) onApplyWorkspaceRoot;
  final Future<void> Function({String? apiBase, String? root}) onRefresh;
  final ValueChanged<WorkspaceFileEntry> onSelectFile;

  @override
  State<_FilesPage> createState() => _FilesPageState();
}

class _FilesPageState extends State<_FilesPage> {
  String query = '';
  List<WorkspaceRootSuggestion> suggestions = const [];
  Timer? debounce;

  @override
  void initState() {
    super.initState();
    unawaited(_loadSuggestions(widget.controller.text));
  }

  @override
  void dispose() {
    debounce?.cancel();
    super.dispose();
  }

  Future<void> _loadSuggestions(String value) async {
    final next = await widget.onFetchRootSuggestions(value);
    if (!mounted) return;
    setState(() => suggestions = next);
  }

  void _scheduleSuggestions(String value) {
    debounce?.cancel();
    debounce = Timer(
      const Duration(milliseconds: 220),
      () => unawaited(_loadSuggestions(value)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final normalizedQuery = query.trim().toLowerCase();
    final visibleFiles = normalizedQuery.isEmpty
        ? widget.files
        : widget.files
              .where(
                (entry) =>
                    entry.name.toLowerCase().contains(normalizedQuery) ||
                    entry.relativePath.toLowerCase().contains(normalizedQuery),
              )
              .toList();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Files',
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
            ),
            IconButton(
              tooltip: 'Refresh files',
              onPressed: () => unawaited(widget.onRefresh()),
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        Text(
          widget.status,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 12),
        TextField(
          controller: widget.controller,
          onChanged: _scheduleSuggestions,
          onSubmitted: (value) => unawaited(widget.onApplyWorkspaceRoot(value)),
          decoration: InputDecoration(
            hintText: 'Workspace root',
            prefixIcon: const Icon(Icons.folder_open_outlined),
            suffixIcon: IconButton(
              tooltip: 'Set workspace',
              icon: const Icon(Icons.check),
              onPressed: () => unawaited(
                widget.onApplyWorkspaceRoot(widget.controller.text),
              ),
            ),
            filled: true,
            fillColor: Colors.white,
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
          ),
        ),
        if (suggestions.isNotEmpty) ...[
          const SizedBox(height: 8),
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
                    trailing: Text(suggestion.kind),
                    onTap: () {
                      widget.controller.text = suggestion.path;
                      setState(() => suggestions = const []);
                      unawaited(widget.onApplyWorkspaceRoot(suggestion.path));
                    },
                  ),
              ],
            ),
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          onChanged: (value) => setState(() => query = value),
          decoration: InputDecoration(
            hintText: 'Find files',
            prefixIcon: const Icon(Icons.search),
            filled: true,
            fillColor: Colors.white,
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
          ),
        ),
        const SizedBox(height: 12),
        if (widget.loading) const LinearProgressIndicator(),
        if (!widget.loading && visibleFiles.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(widget.status),
          ),
        for (final item in visibleFiles)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              tileColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
                side: const BorderSide(color: Color(0xffded7ca)),
              ),
              leading: Icon(
                item.isDirectory
                    ? Icons.folder_outlined
                    : Icons.description_outlined,
              ),
              title: Text(
                item.relativePath,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              subtitle: Text(
                item.isDirectory ? 'Folder' : '${item.size} bytes',
              ),
              trailing: const Icon(Icons.chevron_right),
              contentPadding: EdgeInsets.only(
                left: 16.0 + (item.depth.clamp(0, 4) * 10),
                right: 12,
              ),
              onTap: () => widget.onSelectFile(item),
            ),
          ),
      ],
    );
  }
}

class _FilePreviewPage extends StatelessWidget {
  const _FilePreviewPage({
    required this.activeFile,
    required this.title,
    required this.content,
    required this.status,
    required this.previewArtifacts,
    required this.previewArtifactsExpanded,
    required this.onTogglePreviewArtifacts,
    required this.onSelectArtifact,
  });

  final WorkspaceFileEntry? activeFile;
  final String title;
  final String content;
  final String status;
  final List<PhonePreviewArtifact> previewArtifacts;
  final bool previewArtifactsExpanded;
  final VoidCallback onTogglePreviewArtifacts;
  final ValueChanged<PhonePreviewArtifact> onSelectArtifact;

  @override
  Widget build(BuildContext context) {
    final displayTitle = title.isNotEmpty
        ? title
        : activeFile?.relativePath ?? 'Preview';
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: [
          if (previewArtifacts.isNotEmpty) ...[
            _PreviewArtifactShelf(
              artifacts: previewArtifacts,
              expanded: previewArtifactsExpanded,
              onToggle: onTogglePreviewArtifacts,
              onSelect: onSelectArtifact,
            ),
            const SizedBox(height: 10),
          ],
          Expanded(
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: Colors.white,
                border: Border.all(color: const Color(0xffded7ca)),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      displayTitle,
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    Text(
                      status,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    const SizedBox(height: 10),
                    Expanded(
                      child: SingleChildScrollView(
                        child: SelectableText(
                          content.isEmpty
                              ? 'Select a development file, output artifact, or prompt log to inspect it here.'
                              : content,
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 13,
                            height: 1.35,
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _NoteReadPage extends StatelessWidget {
  const _NoteReadPage({
    required this.activeNote,
    required this.preview,
    required this.fallbackContent,
    required this.status,
    required this.scrollController,
  });

  final WorkspaceNoteSummary? activeNote;
  final WorkspaceNotePreview? preview;
  final String fallbackContent;
  final String status;
  final ScrollController scrollController;

  @override
  Widget build(BuildContext context) {
    final body = preview?.previewMarkdown.trim().isNotEmpty == true
        ? _phonePreviewMarkdown(preview!.previewMarkdown)
        : _formatHugoPreviewBody(_stripHugoFrontmatter(fallbackContent));
    return Padding(
      padding: const EdgeInsets.all(16),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: Colors.white,
          border: Border.all(color: const Color(0xffded7ca)),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                activeNote?.title ?? 'Preview',
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  _MiniChip(Icons.cached_outlined, status),
                  if (preview?.routePath.isNotEmpty == true)
                    _MiniChip(Icons.link_outlined, preview!.routePath),
                  if (activeNote?.pageId.isNotEmpty == true)
                    _MiniChip(Icons.alternate_email, activeNote!.pageId),
                ],
              ),
              const SizedBox(height: 12),
              Expanded(
                child: SingleChildScrollView(
                  controller: scrollController,
                  child: _MarkdownPreviewBody(
                    markdown: body.isEmpty
                        ? 'No preview content loaded.'
                        : body,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

String _stripHugoFrontmatter(String content) {
  final match = RegExp(r'^---\s*\r?\n[\s\S]*?\r?\n---\s*').firstMatch(content);
  return (match == null ? content : content.substring(match.end)).trimLeft();
}

String _formatHugoPreviewBody(String body) {
  return body
      .replaceAllMapped(
        RegExp(
          r'\{\{<\s*callout\b([^>]*)>\}\}([\s\S]*?)\{\{<\s*/callout\s*>\}\}',
        ),
        (match) {
          final args = (match.group(1) ?? '').trim();
          final inner = (match.group(2) ?? '').trim();
          final titleMatch = RegExp(r'"([^"]+)"').firstMatch(args);
          final title = titleMatch?.group(1)?.trim() ?? '';
          final kindParts = args
              .replaceAll(RegExp(r'"[^"]*"'), '')
              .trim()
              .split(RegExp(r'\s+'))
              .where((part) => part.isNotEmpty)
              .toList();
          final kind = kindParts.isEmpty ? null : kindParts.first;
          final label = [
            if (kind != null && kind.toLowerCase() != 'note')
              '${kind[0].toUpperCase()}${kind.substring(1)}',
            if (title.isNotEmpty) title,
          ].join(': ');
          final header = label.isEmpty ? 'Note' : label;
          final lines = inner
              .split(RegExp(r'\r?\n'))
              .map((line) => line.trimRight())
              .toList();
          return [
            '> **$header**',
            for (final line in lines) line.isEmpty ? '>' : '> $line',
          ].join('\n');
        },
      )
      .replaceAllMapped(
        RegExp(r'\{\{<\s*summary\s+"([^"]+)"\s*>\}\}'),
        (match) => '> Summary transclusion: @${match.group(1)}',
      )
      .replaceAllMapped(RegExp(r'\{\{<\s*([^>\s]+)([\s\S]*?)>\}\}'), (match) {
        final shortcode = match.group(1) ?? '';
        final args = (match.group(2) ?? '').trim();
        return args.isEmpty ? '`$shortcode`' : '`$shortcode $args`';
      })
      .replaceAllMapped(
        RegExp(r'(^|[\s(])@([A-Za-z0-9][A-Za-z0-9_-]+)', multiLine: true),
        (match) => '${match.group(1)}[@${match.group(2)}](#${match.group(2)})',
      );
}

String _phonePreviewMarkdown(String value) {
  return value.replaceAllMapped(
    RegExp(
      r'<aside class="dan-markdown-callout[\s\S]*?<span class="text-sm[^"]*">([\s\S]*?)</span>[\s\S]*?<div class="dan-markdown-callout-body[^"]*">([\s\S]*?)</div>\s*</aside>',
      caseSensitive: false,
    ),
    (match) {
      final label = _stripHtml(_decodeHtmlEntities(match.group(1) ?? 'Note'));
      final bodyHtml = match.group(2) ?? '';
      final paragraphs = RegExp(r'<p[^>]*>([\s\S]*?)</p>', caseSensitive: false)
          .allMatches(bodyHtml)
          .map((item) => _stripHtml(_decodeHtmlEntities(item.group(1) ?? '')))
          .where((item) => item.trim().isNotEmpty)
          .toList();
      final bodyLines = paragraphs.isEmpty
          ? [_stripHtml(_decodeHtmlEntities(bodyHtml))]
          : paragraphs;
      return [
        '> **${label.isEmpty ? 'Note' : label}**',
        for (final line in bodyLines) '> $line',
      ].join('\n');
    },
  );
}

String _stripHtml(String value) {
  return value.replaceAll(RegExp(r'<[^>]+>'), '').trim();
}

String _decodeHtmlEntities(String value) {
  return value
      .replaceAll('&lt;', '<')
      .replaceAll('&gt;', '>')
      .replaceAll('&amp;', '&')
      .replaceAll('&quot;', '"')
      .replaceAll('&#39;', "'");
}

class _MiniChip extends StatelessWidget {
  const _MiniChip(this.icon, this.label);

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: const Color(0xffece6da),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14),
          const SizedBox(width: 4),
          Flexible(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.labelSmall,
            ),
          ),
        ],
      ),
    );
  }
}

class _MarkdownPreviewBody extends StatelessWidget {
  const _MarkdownPreviewBody({required this.markdown});

  final String markdown;

  @override
  Widget build(BuildContext context) {
    final lines = markdown.split(RegExp(r'\r?\n'));
    final widgets = <Widget>[];
    var inCode = false;
    final codeLines = <String>[];

    void flushCode() {
      if (codeLines.isEmpty) return;
      widgets.add(
        Container(
          width: double.infinity,
          margin: const EdgeInsets.symmetric(vertical: 8),
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: const Color(0xff1d2422),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Text(
            codeLines.join('\n'),
            style: const TextStyle(
              color: Color(0xfff3efe6),
              fontFamily: 'monospace',
              fontSize: 12.5,
              height: 1.35,
            ),
          ),
        ),
      );
      codeLines.clear();
    }

    for (final raw in lines) {
      final line = raw.trimRight();
      if (line.trimLeft().startsWith('```')) {
        if (inCode) flushCode();
        inCode = !inCode;
        continue;
      }
      if (inCode) {
        codeLines.add(raw);
        continue;
      }
      if (line.trim().isEmpty) {
        widgets.add(const SizedBox(height: 8));
        continue;
      }
      final heading = RegExp(r'^(#{1,6})\s+(.+)$').firstMatch(line);
      if (heading != null) {
        final level = heading.group(1)!.length;
        widgets.add(
          Padding(
            padding: EdgeInsets.only(top: level <= 2 ? 16 : 10, bottom: 5),
            child: Text(
              heading.group(2)!,
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w800,
                fontSize: level == 1
                    ? 22
                    : level == 2
                    ? 19
                    : 16,
              ),
            ),
          ),
        );
        continue;
      }
      final bullet = RegExp(r'^\s*[-*+]\s+(.+)$').firstMatch(line);
      if (bullet != null) {
        widgets.add(
          Padding(
            padding: const EdgeInsets.only(bottom: 5),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('•  '),
                Expanded(
                  child: _PreviewInlineText(
                    bullet.group(1)!,
                    style: const TextStyle(height: 1.38),
                  ),
                ),
              ],
            ),
          ),
        );
        continue;
      }
      final quote = RegExp(r'^\s*>\s?(.+)$').firstMatch(line);
      if (quote != null) {
        widgets.add(
          Container(
            width: double.infinity,
            margin: const EdgeInsets.symmetric(vertical: 6),
            padding: const EdgeInsets.fromLTRB(10, 8, 8, 8),
            decoration: BoxDecoration(
              border: Border.all(color: const Color(0xff9cb9a9)),
              borderRadius: BorderRadius.circular(8),
              color: const Color(0xffedf4ef),
            ),
            child: _PreviewInlineText(
              quote.group(1)!,
              style: const TextStyle(height: 1.38),
            ),
          ),
        );
        continue;
      }
      widgets.add(
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: _PreviewInlineText(line, style: const TextStyle(height: 1.42)),
        ),
      );
    }
    if (inCode) flushCode();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: widgets,
    );
  }
}

class _PreviewInlineText extends StatelessWidget {
  const _PreviewInlineText(this.text, {this.style});

  final String text;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    final baseStyle = DefaultTextStyle.of(context).style.merge(style);
    final spans = <TextSpan>[];
    final pattern = RegExp(
      r'(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))',
    );
    var cursor = 0;
    for (final match in pattern.allMatches(text)) {
      if (match.start > cursor) {
        spans.add(TextSpan(text: text.substring(cursor, match.start)));
      }
      if (match.group(2) != null) {
        spans.add(
          TextSpan(
            text: match.group(2),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
        );
      } else if (match.group(3) != null) {
        spans.add(
          TextSpan(
            text: match.group(3),
            style: const TextStyle(
              fontFamily: 'monospace',
              backgroundColor: Color(0xffece6da),
            ),
          ),
        );
      } else {
        spans.add(
          TextSpan(
            text: match.group(4),
            style: const TextStyle(
              color: Color(0xff176b5b),
              decoration: TextDecoration.underline,
              fontWeight: FontWeight.w600,
            ),
          ),
        );
      }
      cursor = match.end;
    }
    if (cursor < text.length) {
      spans.add(TextSpan(text: text.substring(cursor)));
    }
    return RichText(
      text: TextSpan(style: baseStyle, children: spans),
    );
  }
}

class _NoteEditPage extends StatefulWidget {
  const _NoteEditPage({
    required this.controller,
    required this.scrollController,
    required this.notes,
    required this.activeNote,
    required this.noteStatus,
    required this.saveStatus,
    required this.onSave,
  });

  final TextEditingController controller;
  final ScrollController scrollController;
  final List<WorkspaceNoteSummary> notes;
  final WorkspaceNoteSummary? activeNote;
  final String noteStatus;
  final String saveStatus;
  final Future<void> Function() onSave;

  @override
  State<_NoteEditPage> createState() => _NoteEditPageState();
}

class _NoteEditPageState extends State<_NoteEditPage> {
  List<WorkspaceNoteSummary> suggestions = const [];
  int mentionStart = -1;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_refreshSuggestions);
  }

  @override
  void didUpdateWidget(covariant _NoteEditPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_refreshSuggestions);
      widget.controller.addListener(_refreshSuggestions);
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_refreshSuggestions);
    super.dispose();
  }

  void _refreshSuggestions() {
    final selection = widget.controller.selection;
    if (!selection.isValid || selection.baseOffset < 0) return;
    final cursor = selection.baseOffset;
    final text = widget.controller.text;
    if (cursor > text.length) return;
    final before = text.substring(0, cursor);
    final at = before.lastIndexOf('@');
    if (at < 0) {
      if (suggestions.isNotEmpty) setState(() => suggestions = const []);
      return;
    }
    final query = before.substring(at + 1);
    final validQuery = RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(query);
    if (query.isEmpty || !validQuery) {
      if (suggestions.isNotEmpty) setState(() => suggestions = const []);
      return;
    }
    final lowered = query.toLowerCase();
    final next =
        widget.notes
            .where(
              (note) =>
                  note.pageId.toLowerCase().contains(lowered) ||
                  note.title.toLowerCase().contains(lowered) ||
                  note.relativePath.toLowerCase().contains(lowered),
            )
            .where((note) => note.pageId.isNotEmpty)
            .toList()
          ..sort(
            (a, b) => a.pageId.toLowerCase().compareTo(b.pageId.toLowerCase()),
          );
    final visible = next.take(8).toList();
    setState(() {
      mentionStart = at;
      suggestions = visible;
    });
  }

  void _insertMention(WorkspaceNoteSummary note) {
    final selection = widget.controller.selection;
    final cursor = selection.baseOffset;
    if (mentionStart < 0 || cursor < mentionStart) return;
    final text = widget.controller.text;
    final replacement = '@${note.pageId}';
    final next = text.replaceRange(mentionStart, cursor, replacement);
    final nextCursor = mentionStart + replacement.length;
    widget.controller.value = TextEditingValue(
      text: next,
      selection: TextSelection.collapsed(offset: nextCursor),
    );
    setState(() => suggestions = const []);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.activeNote?.title ?? 'Markdown source',
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            widget.activeNote?.relativePath ?? widget.noteStatus,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              _MiniChip(Icons.save_outlined, widget.saveStatus),
              const Spacer(),
              TextButton.icon(
                onPressed: () => unawaited(widget.onSave()),
                icon: const Icon(Icons.save_outlined),
                label: const Text('Save'),
              ),
            ],
          ),
          if (suggestions.isNotEmpty) ...[
            const SizedBox(height: 8),
            DecoratedBox(
              decoration: BoxDecoration(
                color: Colors.white,
                border: Border.all(color: const Color(0xffded7ca)),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Column(
                children: [
                  for (final note in suggestions)
                    ListTile(
                      dense: true,
                      leading: const Icon(Icons.alternate_email),
                      title: Text(note.pageId),
                      subtitle: Text(
                        note.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      onTap: () => _insertMention(note),
                    ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 12),
          Expanded(
            child: TextField(
              controller: widget.controller,
              scrollController: widget.scrollController,
              expands: true,
              minLines: null,
              maxLines: null,
              decoration: InputDecoration(
                hintText: 'Markdown source',
                alignLabelWithHint: true,
                filled: true,
                fillColor: Colors.white,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
