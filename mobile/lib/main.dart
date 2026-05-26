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

enum NotesPage { pages, edit, read }

enum PhoneChatRole { user, assistant, status }

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

String _asString(Object? value, String fallback) {
  final text = value?.toString().trim();
  return text == null || text.isEmpty ? fallback : text;
}

int _asInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '') ?? 0;
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
      title: 'DAN Phone',
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
  NotesPage notesPage = NotesPage.pages;
  WireGuardStatus? wireGuard;
  String health = 'checking';
  String activeApiBase = compiledDefaultApiBase;
  String statusDetail = 'Checking backend';
  String accessToken = '';
  String phoneWireGuardConfig = decodedDefaultPhoneWireGuardConfig();
  String phoneWireGuardDetail = decodedDefaultPhoneWireGuardConfig().isEmpty
      ? 'No DAN phone VPN config loaded'
      : 'DAN phone VPN config loaded';
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
  List<WorkspaceFileEntry> workspaceFiles = const [];
  WorkspaceFileEntry? activeFile;
  String activeFileContent = '';
  String fileStatus = 'Set a workspace root to browse files';
  bool loadingFiles = false;
  List<PhoneChatMessage> chatMessages = const [
    PhoneChatMessage(
      id: 'welcome',
      role: PhoneChatRole.status,
      text:
          'Set a workspace, then send a Work request. Super DAN will run through the same Agent-run backend as the desktop GUI.',
    ),
  ];
  String activeWorkflowId = '_scratch';
  String activeThreadId = '';
  String activeRunId = '';
  String activeTaskId = '';
  bool activeRunLive = false;
  bool chatBusy = false;
  String chatStatus = 'Ready';
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
          if (phoneWireGuardDetail == 'No DAN phone VPN config loaded') {
            phoneWireGuardDetail = 'DAN phone VPN config loaded';
          }
        }
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
    if (!Platform.isAndroid) return;
    try {
      await wireGuardChannel.invokeMethod<Object?>('saveSettings', {
        'apiBase': activeApiBase,
        'accessToken': accessToken,
        'wireGuardConfig': phoneWireGuardConfig,
        'workspaceRoot': workspaceRoot,
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
            : 'No DAN backend at ${candidates.join(', ')}';
        if (phoneWireGuardConfig.trim().isEmpty && backendReady) {
          phoneWireGuardDetail = parsedWireGuard?.status == 'active'
              ? 'Phone VPN not required; backend is reachable and host WG is active'
              : 'Phone VPN config is only needed for app-managed VPN';
        }
      });
      if (health == 'ready') {
        unawaited(_refreshNotes(selectedBase));
        unawaited(_refreshWorkspaceFiles(apiBase: selectedBase));
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
      fileStatus = 'Loading workspace';
    });
    await _savePhoneSettings();
    await _refreshWorkspaceFiles(root: nextRoot);
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
      activeFileContent = '';
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
      fileStatus = entry.relativePath;
    });
  }

  String _newId(String prefix) {
    return '$prefix-${DateTime.now().microsecondsSinceEpoch}';
  }

  void _replaceChatMessage(String id, String text, {String status = ''}) {
    setState(() {
      chatMessages = chatMessages
          .map(
            (message) => message.id == id
                ? message.copyWith(text: text, status: status)
                : message,
          )
          .toList();
    });
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
    return activeThreadId.isNotEmpty;
  }

  Map<String, Object?> _phoneSurfaceContext() {
    return {
      'identity': {'name': 'DAN Phone', 'role': 'chunk_workspace_phone'},
      'workspace_root': workspaceRoot,
      'notes_root': notesRoot,
      'workspace_source': 'chunk_workspace_phone',
      'ui_surface': 'chunk_workspace',
      'surface_profile': 'super_tui',
      'agent_profile': 'super_tui',
      'agent_backend': 'super_dan',
      'gui_for': 'dan super-tui',
      'capabilities': [
        'notes',
        'markdown_preview',
        'phone_page_navigation',
        'background_agent_runs',
        'checkpoint_commands',
        'read_only_wireguard_status',
      ],
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
    final prompt = chatController.text.trim();
    if (prompt.isEmpty || chatBusy) return;
    chatController.clear();
    final user = PhoneChatMessage(
      id: _newId('user'),
      role: PhoneChatRole.user,
      text: prompt,
    );
    final assistantId = _newId('assistant');
    final assistant = PhoneChatMessage(
      id: assistantId,
      role: PhoneChatRole.assistant,
      text: activeRunLive
          ? 'Steering the active Super DAN run...'
          : 'Starting Super DAN...',
      status: 'working',
    );
    final historyForBackend = _chatHistoryForBackend([...chatMessages, user]);
    setState(() {
      chatBusy = true;
      chatStatus = activeRunLive ? 'Steering Super DAN' : 'Starting Super DAN';
      chatMessages = [...chatMessages, user, assistant];
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
              'surface_context': _phoneSurfaceContext(),
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
        'surface_context': _phoneSurfaceContext(),
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
        _replaceChatMessage(assistantId, 'Super DAN queued this request.');
        setState(() => chatStatus = 'Queued');
        return;
      }
      activeRunId = runId;
      activeRunLive = true;
      _replaceChatMessage(
        assistantId,
        'Super DAN accepted the request.',
        status: 'running',
      );
      await _postJson(activeApiBase, '/api/v2/agent-runs/$runId/execute', {
        'backend': 'super_dan',
        'surface_profile': 'super_tui',
        'background': true,
        'profile_policy': {
          'backend': 'super_dan',
          'surface_profile': 'super_tui',
        },
        'metadata': {
          'backend': 'super_dan',
          'surface_profile': 'super_tui',
          'compatibility_profile': 'super_tui',
          'surface': 'gui:chunk-workspace',
          'requested_from': 'chunk_workspace_phone',
          'gui_for': 'dan super-tui',
          'selected_backend': 'super_dan',
        },
      });
      setState(() => chatStatus = 'Super DAN running');
      _startAgentPolling(runId, assistantId);
    } finally {
      if (mounted) setState(() => chatBusy = false);
    }
  }

  bool _ensureWorkspaceUsableForChat(String assistantId) {
    if (health != 'ready') {
      _replaceChatMessage(assistantId, 'DAN API is offline. Check Settings.');
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
    final latest = events.last;
    final text = _agentEventText(latest);
    if (text.isNotEmpty) {
      _replaceChatMessage(
        assistantId,
        text,
        status: _asString(latest['type'], 'running'),
      );
    }
    final type = _asString(latest['type'], '');
    if (type == 'completed' ||
        type == 'failed' ||
        type == 'blocked' ||
        type == 'stopped') {
      agentPollTimer?.cancel();
      setState(() {
        activeRunLive = false;
        chatStatus = type == 'completed' ? 'Ready' : type;
      });
    }
  }

  String _agentEventText(Map<String, dynamic>? event) {
    if (event == null) return '';
    final summary = _asString(event['summary'], '');
    if (summary.isNotEmpty) return summary;
    final payload = event['payload'] is Map<String, dynamic>
        ? event['payload'] as Map<String, dynamic>
        : const <String, dynamic>{};
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
        phoneWireGuardDetail = 'Add a DAN phone VPN config in Settings';
      });
      return;
    }
    if (!compiledPhoneWireGuardAutoStart) {
      if (!mounted) return;
      setState(() {
        phoneWireGuardDetail = 'Connect DAN Phone VPN from Settings';
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
              : 'Add a DAN phone VPN config to start app-managed VPN';
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
              'Paste a DAN phone VPN config before forcing the tunnel on';
        });
      }
      return null;
    }
    if (showBusy && mounted) {
      setState(() {
        phoneWireGuardBusy = true;
        phoneWireGuardDetail = 'Force starting DAN Phone VPN tunnel';
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
        phoneWireGuardDetail = 'DAN phone VPN config saved';
      });
    }
  }

  Future<PhoneWireGuardStatus?> _forceStopPhoneWireGuard({
    bool showBusy = true,
  }) async {
    if (showBusy && mounted) {
      setState(() {
        phoneWireGuardBusy = true;
        phoneWireGuardDetail = 'Force off requested for DAN Phone VPN';
      });
    }
    try {
      final status = await _phoneWireGuardCall('stop', showBusy: false);
      if (mounted) {
        setState(() {
          phoneWireGuardDetail =
              'DAN Phone VPN forced off (${status?.name ?? 'dan-phone'})';
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
                          labelText: 'DAN API base',
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
                          'No phone VPN config saved. Force start needs a DAN phone WireGuard peer config first.',
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
                        labelText: 'DAN phone WireGuard config',
                        hintText: '[Interface]\nPrivateKey = ...',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    if (phoneWireGuardConfig.trim().isEmpty) ...[
                      Text(
                        'No phone VPN config saved. Paste a DAN phone peer config before using Force start.',
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
        onSelectSession: () {
          Navigator.of(context).pop();
          setState(() {
            mode = WorkspaceMode.work;
            workPage = WorkPage.chat;
          });
        },
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
                      workspaceRoot: workspaceRoot,
                      workspaceRootController: workspaceRootController,
                      files: workspaceFiles,
                      activeFile: activeFile,
                      activeFileContent: activeFileContent,
                      fileStatus: fileStatus,
                      loadingFiles: loadingFiles,
                      chatMessages: chatMessages,
                      chatController: chatController,
                      chatStatus: chatStatus,
                      chatBusy: chatBusy,
                      onFetchRootSuggestions: _fetchWorkspaceRootSuggestions,
                      onApplyWorkspaceRoot: _applyWorkspaceRoot,
                      onRefreshFiles: _refreshWorkspaceFiles,
                      onSelectFile: _selectWorkspaceFile,
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

class _SessionDrawer extends StatelessWidget {
  const _SessionDrawer({required this.onSelectSession});

  final VoidCallback onSelectSession;

  @override
  Widget build(BuildContext context) {
    const sessions = [
      'Unified workspace surface',
      'Phone friendly navigation',
      'WireGuard status check',
    ];
    return Drawer(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(
              'Sessions',
              style: Theme.of(
                context,
              ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 12),
            TextField(
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
            for (final session in sessions)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: ListTile(
                  tileColor: Colors.white,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(8),
                    side: const BorderSide(color: Color(0xffded7ca)),
                  ),
                  leading: const Icon(Icons.chat_bubble_outline),
                  title: Text(session),
                  onTap: onSelectSession,
                ),
              ),
          ],
        ),
      ),
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
    required this.workspaceRoot,
    required this.workspaceRootController,
    required this.files,
    required this.activeFile,
    required this.activeFileContent,
    required this.fileStatus,
    required this.loadingFiles,
    required this.chatMessages,
    required this.chatController,
    required this.chatStatus,
    required this.chatBusy,
    required this.onFetchRootSuggestions,
    required this.onApplyWorkspaceRoot,
    required this.onRefreshFiles,
    required this.onSelectFile,
    required this.onSendChat,
  });

  final WorkPage page;
  final String workspaceRoot;
  final TextEditingController workspaceRootController;
  final List<WorkspaceFileEntry> files;
  final WorkspaceFileEntry? activeFile;
  final String activeFileContent;
  final String fileStatus;
  final bool loadingFiles;
  final List<PhoneChatMessage> chatMessages;
  final TextEditingController chatController;
  final String chatStatus;
  final bool chatBusy;
  final Future<List<WorkspaceRootSuggestion>> Function(String)
  onFetchRootSuggestions;
  final Future<void> Function([String? value]) onApplyWorkspaceRoot;
  final Future<void> Function({String? apiBase, String? root}) onRefreshFiles;
  final ValueChanged<WorkspaceFileEntry> onSelectFile;
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
        content: activeFileContent,
        status: fileStatus,
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
    required this.onSend,
  });

  final String workspaceRoot;
  final List<PhoneChatMessage> messages;
  final TextEditingController controller;
  final String status;
  final bool busy;
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
                        PhoneChatRole.assistant => 'Super DAN',
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
          Text(status, style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 12),
          TextField(
            controller: controller,
            minLines: 1,
            maxLines: 4,
            enabled: !busy,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => onSend(),
            decoration: InputDecoration(
              hintText: 'Ask Super DAN to work in this workspace',
              filled: true,
              fillColor: Colors.white,
              suffixIcon: IconButton(
                tooltip: 'Send',
                icon: busy
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send_outlined),
                onPressed: busy ? null : onSend,
              ),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
        ],
      ),
    );
  }
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
                Text(text),
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
    required this.content,
    required this.status,
  });

  final WorkspaceFileEntry? activeFile;
  final String content;
  final String status;

  @override
  Widget build(BuildContext context) {
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
                activeFile?.relativePath ?? 'Preview',
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
              Text(
                status,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 10),
              Expanded(
                child: SingleChildScrollView(
                  child: SelectableText(
                    content.isEmpty
                        ? 'Select a development file to inspect it here.'
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
        ? preview!.previewMarkdown
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
            decoration: const BoxDecoration(
              border: Border(
                left: BorderSide(color: Color(0xff176b5b), width: 3),
              ),
              color: Color(0xffedf4ef),
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
