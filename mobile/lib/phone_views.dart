part of 'main.dart';

ThemeData buildPhoneTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme =
      ColorScheme.fromSeed(
        seedColor: const Color(0xff286e62),
        brightness: brightness,
      ).copyWith(
        primary: dark ? const Color(0xff91d2c2) : const Color(0xff286e62),
        onPrimary: dark ? const Color(0xff143b33) : Colors.white,
        surface: dark ? const Color(0xff1b211f) : const Color(0xfffaf9f5),
        surfaceContainerLow: dark
            ? const Color(0xff232b28)
            : const Color(0xfff1f0e9),
        surfaceContainerHighest: dark
            ? const Color(0xff303b36)
            : const Color(0xffe9ede6),
        onSurface: dark ? const Color(0xffe6eae3) : const Color(0xff24312b),
        onSurfaceVariant: dark
            ? const Color(0xffa8b6ad)
            : const Color(0xff637169),
        outlineVariant: dark
            ? const Color(0xff38443e)
            : const Color(0xffdfe3da),
      );
  final base = ThemeData(useMaterial3: true, colorScheme: scheme);
  return base.copyWith(
    scaffoldBackgroundColor: scheme.surface,
    textTheme: base.textTheme.copyWith(
      titleLarge: TextStyle(
        fontSize: 25,
        height: 1.2,
        fontWeight: FontWeight.w600,
        color: scheme.onSurface,
      ),
      titleMedium: TextStyle(
        fontSize: 17,
        height: 1.35,
        fontWeight: FontWeight.w600,
        color: scheme.onSurface,
      ),
      bodyLarge: TextStyle(fontSize: 16, height: 1.5, color: scheme.onSurface),
      bodyMedium: TextStyle(
        fontSize: 15,
        height: 1.45,
        color: scheme.onSurface,
      ),
      bodySmall: TextStyle(
        fontSize: 13,
        height: 1.4,
        color: scheme.onSurfaceVariant,
      ),
      labelLarge: TextStyle(
        fontSize: 14,
        height: 1.3,
        fontWeight: FontWeight.w600,
        color: scheme.onSurface,
      ),
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: scheme.surface,
      foregroundColor: scheme.onSurface,
      elevation: 0,
      scrolledUnderElevation: 0,
    ),
    dividerTheme: DividerThemeData(
      color: scheme.outlineVariant,
      thickness: 1,
      space: 1,
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        minimumSize: const Size(48, 48),
        foregroundColor: scheme.onSurfaceVariant,
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(48, 48),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(48, 48),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(minimumSize: const Size(48, 48)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surfaceContainerLow,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(color: scheme.primary, width: 1.5),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: scheme.surface,
      indicatorColor: scheme.primary.withValues(alpha: dark ? .22 : .1),
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      height: 76,
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => TextStyle(
          fontSize: 12,
          fontWeight: states.contains(WidgetState.selected)
              ? FontWeight.w700
              : FontWeight.w500,
          color: states.contains(WidgetState.selected)
              ? scheme.primary
              : scheme.onSurfaceVariant,
        ),
      ),
    ),
    listTileTheme: ListTileThemeData(
      iconColor: scheme.onSurfaceVariant,
      minVerticalPadding: 12,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16),
    ),
    drawerTheme: DrawerThemeData(
      backgroundColor: scheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.horizontal(right: Radius.circular(24)),
      ),
    ),
  );
}

/// A phone-first shell. Execution state is supplied by the shared workspace.
class PhoneShell extends StatelessWidget {
  const PhoneShell({
    super.key,
    required this.scaffoldKey,
    required this.drawer,
    required this.selectedIndex,
    required this.onDestinationSelected,
    required this.body,
    required this.connectionState,
    required this.computerLabel,
    required this.onConfigureBackend,
    this.attentionCount = 0,
    this.conversationTitle = '',
    this.onReturnToDiane,
    this.lastSyncedAt,
    this.onReconnect,
  });

  final GlobalKey<ScaffoldState> scaffoldKey;
  final Widget drawer;
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final Widget body;
  final String connectionState;
  final String computerLabel;
  final VoidCallback onConfigureBackend;
  final int attentionCount;
  final String conversationTitle;
  final VoidCallback? onReturnToDiane;
  final DateTime? lastSyncedAt;
  final VoidCallback? onReconnect;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final connected = connectionState == 'ready';
    final checking = connectionState == 'checking';
    final status = connected
        ? 'Connected'
        : checking
        ? 'Connecting…'
        : 'Offline';
    final title = ['Diane', 'Work', 'Library'][selectedIndex];
    final textScaler = MediaQuery.textScalerOf(context);
    final keyboardOpen = MediaQuery.viewInsetsOf(context).bottom > 0;
    final headerHeight =
        (textScaler.scale(25) * 1.2 +
                (keyboardOpen ? 0 : textScaler.scale(13) * 1.4) +
                14)
            .clamp(80.0, 160.0);
    return Scaffold(
      key: scaffoldKey,
      drawer: drawer,
      appBar: AppBar(
        toolbarHeight: headerHeight,
        leading: IconButton(
          tooltip: 'Conversations',
          icon: const Icon(Icons.menu_rounded),
          onPressed: () => scaffoldKey.currentState?.openDrawer(),
        ),
        titleSpacing: 2,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.titleLarge,
            ),
            if (!keyboardOpen) const SizedBox(height: 3),
            if (!keyboardOpen)
              Row(
                children: [
                  Icon(
                    connected
                        ? Icons.circle
                        : checking
                        ? Icons.sync_rounded
                        : Icons.cloud_off_outlined,
                    size: connected ? 7 : 13,
                    color: connected ? colors.primary : colors.onSurfaceVariant,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      '$status · $computerLabel',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Computer connection',
            icon: const Icon(Icons.computer_outlined),
            onPressed: onConfigureBackend,
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: SafeArea(
        top: false,
        bottom: false,
        child: Column(
          children: [
            if (!connected && !checking)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 10,
                ),
                color: colors.surfaceContainerLow,
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        keyboardOpen
                            ? 'Keep drafting'
                            : 'You can keep drafting. Reconnect to send and receive updates.${lastSyncedAt == null ? '' : '\nLast sync ${_compactDateTimeLabel(lastSyncedAt!.toIso8601String())}'}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                    if (onReconnect != null)
                      TextButton(
                        onPressed: onReconnect,
                        child: const Text('Retry'),
                      ),
                  ],
                ),
              ),
            if (selectedIndex == 0 && onReturnToDiane != null)
              Material(
                color: colors.surfaceContainerLow,
                child: InkWell(
                  onTap: onReturnToDiane,
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 10, 12, 10),
                    child: Row(
                      children: [
                        Icon(
                          Icons.arrow_back_rounded,
                          size: 18,
                          color: colors.primary,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Back to Diane',
                                style: Theme.of(context).textTheme.labelLarge
                                    ?.copyWith(color: colors.primary),
                              ),
                              Text(
                                conversationTitle.isEmpty
                                    ? 'Viewing a work conversation'
                                    : conversationTitle,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            Expanded(child: body),
          ],
        ),
      ),
      bottomNavigationBar: DecoratedBox(
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: colors.outlineVariant)),
        ),
        child: NavigationBar(
          height: 76 + (textScaler.scale(12) - 12),
          selectedIndex: selectedIndex,
          onDestinationSelected: onDestinationSelected,
          destinations: [
            const NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline_rounded),
              selectedIcon: Icon(Icons.chat_bubble_rounded),
              label: 'Diane',
            ),
            NavigationDestination(
              icon: Badge(
                isLabelVisible: attentionCount > 0,
                label: Text('$attentionCount'),
                child: const Icon(Icons.inbox_outlined),
              ),
              selectedIcon: Badge(
                isLabelVisible: attentionCount > 0,
                label: Text('$attentionCount'),
                child: const Icon(Icons.inbox_rounded),
              ),
              label: 'Work',
            ),
            const NavigationDestination(
              icon: Icon(Icons.folder_open_outlined),
              selectedIcon: Icon(Icons.folder_rounded),
              label: 'Library',
            ),
          ],
        ),
      ),
    );
  }
}

String phoneWorkGroup(PhoneTaskSummary? task) {
  final status = task?.status.toLowerCase() ?? '';
  if (const {
    'needs_input',
    'needs_attention',
    'blocked',
    'failed',
    'paused',
    'interrupted',
    'awaiting_approval',
    'waiting_for_input',
  }.contains(status)) {
    return 'Needs you';
  }
  if (const {
    'queued',
    'running',
    'active',
    'waiting',
    'waiting_dependency',
    'starting',
  }.contains(status)) {
    return 'Working';
  }
  return 'Recent';
}

String _workStatusLabel(String status) => switch (status.toLowerCase()) {
  'needs_input' || 'waiting_for_input' => 'Reply needed',
  'needs_attention' || 'awaiting_approval' => 'Decision needed',
  'blocked' => 'Blocked',
  'failed' => 'Could not finish',
  'interrupted' => 'Interrupted',
  'paused' => 'Paused',
  'queued' || 'waiting_dependency' => 'Queued',
  'running' || 'active' || 'starting' => 'Working',
  'waiting' => 'Waiting',
  'completed' || 'done' => 'Finished',
  'stopped' || 'cancelled' => 'Stopped',
  'saved' => 'Saved',
  'sending' => 'Sending…',
  'uncertain' => 'Delivery unconfirmed',
  'accepted' => 'Received by computer',
  '' || 'blank' || 'unknown' => '',
  _ => status,
};

class PhoneWorkOverview extends StatelessWidget {
  const PhoneWorkOverview({
    super.key,
    required this.groups,
    required this.tasksByThread,
    required this.loading,
    required this.onRefresh,
    required this.onSelectSession,
    this.deliveries = const [],
    this.onRetryDelivery,
    this.approvals = const [],
    this.onReplyApproval,
    this.onOpenDiane,
  });
  final List<PhoneWorkspaceSessionGroup> groups;
  final Map<String, PhoneTaskSummary> tasksByThread;
  final bool loading;
  final Future<void> Function() onRefresh;
  final ValueChanged<PhoneSessionSummary> onSelectSession;
  final List<PhoneDeliveryItem> deliveries;
  final ValueChanged<String>? onRetryDelivery;
  final List<PhoneApprovalRequest> approvals;
  final Future<void> Function(PhoneApprovalRequest, Map<String, Object?>)?
  onReplyApproval;
  final VoidCallback? onOpenDiane;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final sessions = <String, PhoneSessionSummary>{};
    final projects = <String, String>{};
    for (final group in groups.where((group) => !group.archived)) {
      for (final session in group.sessions.where(
        (session) => !session.archived,
      )) {
        sessions[session.id] = session;
        projects[session.id] = group.name;
      }
    }
    final ordered = sessions.values.toList()
      ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    final pending = deliveries
        .where((item) => item.status != 'accepted')
        .toList();
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        key: const PageStorageKey('phone-work-overview'),
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Across your conversations',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: colors.onSurfaceVariant,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'Refresh work',
                onPressed: loading ? null : () => unawaited(onRefresh()),
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          if (loading) const LinearProgressIndicator(minHeight: 2),
          if (pending.isNotEmpty) ...[
            _WorkSectionHeading(title: 'Delivery', count: pending.length),
            for (final item in pending)
              PhoneDeliveryNotice(
                item: item,
                onRetry: onRetryDelivery == null
                    ? null
                    : () => onRetryDelivery!(item.id),
              ),
          ],
          for (final section in const ['Needs you', 'Working', 'Recent']) ...[
            if ((section == 'Needs you' && approvals.isNotEmpty) ||
                ordered.any(
                  (session) =>
                      phoneWorkGroup(tasksByThread[session.id]) == section,
                )) ...[
              _WorkSectionHeading(
                title: section,
                count:
                    ordered
                        .where(
                          (session) =>
                              phoneWorkGroup(tasksByThread[session.id]) ==
                              section,
                        )
                        .length +
                    (section == 'Needs you' ? approvals.length : 0),
              ),
              if (section == 'Needs you')
                for (final request in approvals)
                  PhoneApprovalCard(
                    key: ValueKey(request.key),
                    request: request,
                    onReply: onReplyApproval,
                  ),
              for (final session in ordered.where(
                (session) =>
                    phoneWorkGroup(tasksByThread[session.id]) == section,
              ))
                _WorkSummaryRow(
                  session: session,
                  task: tasksByThread[session.id],
                  project: projects[session.id] ?? '',
                  onTap: () => onSelectSession(session),
                ),
            ],
          ],
          if (ordered.isEmpty &&
              approvals.isEmpty &&
              pending.isEmpty &&
              !loading)
            _PhoneEmptyState(
              icon: Icons.inbox_outlined,
              title: 'A place to pick things up',
              description:
                  'Updates from your conversations appear here, with anything that needs you first.',
              actionLabel: 'Talk to Diane',
              onAction: onOpenDiane,
            ),
        ],
      ),
    );
  }
}

class _WorkSectionHeading extends StatelessWidget {
  const _WorkSectionHeading({required this.title, required this.count});
  final String title;
  final int count;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 25, bottom: 9),
    child: Row(
      children: [
        Text(title, style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(width: 8),
        Text('$count', style: Theme.of(context).textTheme.bodySmall),
      ],
    ),
  );
}

class _WorkSummaryRow extends StatelessWidget {
  const _WorkSummaryRow({
    required this.session,
    required this.task,
    required this.project,
    required this.onTap,
  });
  final PhoneSessionSummary session;
  final PhoneTaskSummary? task;
  final String project;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final group = phoneWorkGroup(task);
    final attention = group == 'Needs you';
    final detail = task?.latestProgress.trim() ?? '';
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          constraints: const BoxConstraints(minHeight: 82),
          padding: const EdgeInsets.symmetric(vertical: 15, horizontal: 1),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: colors.outlineVariant)),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.only(top: 4, right: 12),
                child: Icon(
                  attention
                      ? Icons.radio_button_checked_rounded
                      : group == 'Working'
                      ? Icons.timelapse_rounded
                      : task?.status == 'completed'
                      ? Icons.check_circle_outline_rounded
                      : Icons.chat_bubble_outline_rounded,
                  size: 18,
                  color: attention
                      ? colors.tertiary
                      : group == 'Working'
                      ? colors.primary
                      : colors.onSurfaceVariant,
                ),
              ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      session.displayTitle,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(
                        context,
                      ).textTheme.titleMedium?.copyWith(fontSize: 16),
                    ),
                    if (detail.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 5),
                        child: Text(
                          detail,
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodyMedium
                              ?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ),
                    const SizedBox(height: 7),
                    Text(
                      [
                        if (task != null) _workStatusLabel(task!.status),
                        if (project.isNotEmpty && project != 'Other chats')
                          project,
                        _compactDateTimeLabel(session.updatedAt),
                      ].where((part) => part.isNotEmpty).join(' · '),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.only(top: 4, left: 8),
                child: Icon(
                  Icons.chevron_right_rounded,
                  size: 18,
                  color: colors.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class PhoneLibraryView extends StatelessWidget {
  const PhoneLibraryView({
    super.key,
    required this.selectedIndex,
    required this.onSelected,
    required this.child,
  });
  final int selectedIndex;
  final ValueChanged<int> onSelected;
  final Widget child;
  @override
  Widget build(BuildContext context) => Column(
    children: [
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
        child: Row(
          children: [
            for (var index = 0; index < 3; index++)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ChoiceChip(
                  label: Text(const ['Files', 'Notes', 'Outputs'][index]),
                  showCheckmark: false,
                  selected: selectedIndex == index,
                  onSelected: (_) => onSelected(index),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 11,
                  ),
                  materialTapTargetSize: MaterialTapTargetSize.padded,
                ),
              ),
          ],
        ),
      ),
      Expanded(child: child),
    ],
  );
}

class _PhoneEmptyState extends StatelessWidget {
  const _PhoneEmptyState({
    required this.icon,
    required this.title,
    required this.description,
    this.actionLabel,
    this.onAction,
  });
  final IconData icon;
  final String title;
  final String description;
  final String? actionLabel;
  final VoidCallback? onAction;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(8, 36, 8, 28),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 28, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: 20),
        Text(title, style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 10),
        Text(
          description,
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
            color: Theme.of(context).colorScheme.onSurfaceVariant,
          ),
        ),
        if (onAction != null && actionLabel != null)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: TextButton.icon(
              onPressed: onAction,
              icon: const Icon(Icons.arrow_forward_rounded, size: 18),
              label: Text(actionLabel!),
            ),
          ),
      ],
    ),
  );
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
                    'Conversations',
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
                hintText: 'Search conversations',
                prefixIcon: const Icon(Icons.search),
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceContainerLow,
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
    final colors = Theme.of(context).colorScheme;
    final active = !group.archived && group.root == activeWorkspaceRoot;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        children: [
          ListTile(
            contentPadding: const EdgeInsets.only(left: 2),
            title: Text(
              group.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                color: active ? colors.primary : colors.onSurface,
              ),
            ),
            subtitle: Text(
              '${group.sessions.length} ${group.archived ? 'archived' : 'conversations'}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            onTap: group.archived
                ? () => onExpandedChanged(!expanded)
                : onSelectWorkspace,
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (!group.archived)
                  IconButton(
                    tooltip: 'New conversation in ${group.name}',
                    onPressed: onCreateSession,
                    icon: const Icon(Icons.add_comment_outlined, size: 20),
                  ),
                IconButton(
                  tooltip: expanded ? 'Collapse project' : 'Expand project',
                  onPressed: () => onExpandedChanged(!expanded),
                  icon: Icon(
                    expanded
                        ? Icons.keyboard_arrow_down_rounded
                        : Icons.keyboard_arrow_right_rounded,
                  ),
                ),
              ],
            ),
          ),
          if (expanded) ...[
            if (group.sessions.isEmpty)
              const Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 12),
                  child: Text('No conversations yet.'),
                ),
              ),
            for (final session in group.sessions)
              _SessionTile(
                session: session,
                task: tasksByThread[session.id],
                active: session.id == activeThreadId,
                archived: group.archived,
                onTap: () => onSelectSession(session),
              ),
          ],
          const Divider(),
        ],
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
    final colors = Theme.of(context).colorScheme;
    final updated = _compactDateTimeLabel(session.updatedAt);
    final status = _workStatusLabel(task?.status ?? '');
    return ListTile(
      enabled: !archived,
      selected: active,
      selectedTileColor: colors.primary.withValues(alpha: .09),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      title: Text(
        session.displayTitle,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
          fontWeight: active ? FontWeight.w600 : FontWeight.w400,
        ),
      ),
      subtitle: Text(
        [
          if (status.isNotEmpty) status,
          if (updated.isNotEmpty) updated,
        ].join(' · '),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: Theme.of(context).textTheme.bodySmall,
      ),
      trailing: task?.isLive == true
          ? Icon(Icons.timelapse_rounded, size: 18, color: colors.primary)
          : null,
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
        color: Theme.of(context).colorScheme.surfaceContainerLow,
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
    this.deliveries = const [],
    this.pendingApprovals = const [],
    this.onRetryDelivery,
    this.onReplyApproval,
    this.conversationTitle = '',
    this.conversationId = '',
    this.connectionState = 'ready',
    this.onQueueChat,
    this.reportReference,
    this.chatFocusNode,
    this.onRemoveReport,
    this.onOpenReport,
    this.onAskReport,
    this.showAskReport = false,
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
  final List<PhoneDeliveryItem> deliveries;
  final List<PhoneApprovalRequest> pendingApprovals;
  final ValueChanged<String>? onRetryDelivery;
  final Future<void> Function(PhoneApprovalRequest, Map<String, Object?>)?
  onReplyApproval;
  final String conversationTitle;
  final String conversationId;
  final String connectionState;
  final VoidCallback? onQueueChat;
  final PhoneReportReference? reportReference;
  final FocusNode? chatFocusNode;
  final VoidCallback? onRemoveReport, onAskReport;
  final ValueChanged<PhoneReportReference>? onOpenReport;
  final bool showAskReport;

  @override
  Widget build(BuildContext context) {
    return switch (page) {
      WorkPage.chat => PhoneConversationView(
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
        deliveries: deliveries,
        pendingApprovals: pendingApprovals,
        onRetryDelivery: onRetryDelivery,
        onReplyApproval: onReplyApproval,
        conversationTitle: conversationTitle,
        conversationId: conversationId,
        connectionState: connectionState,
        onQueue: onQueueChat,
        reportReference: reportReference,
        focusNode: chatFocusNode,
        onRemoveReport: onRemoveReport,
        onOpenReport: onOpenReport,
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
        onAsk: onAskReport,
        showAsk: showAskReport,
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
            fillColor: Theme.of(context).colorScheme.surfaceContainerLow,
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
              tileColor: Theme.of(context).colorScheme.surface,
              selectedTileColor: Theme.of(
                context,
              ).colorScheme.primary.withValues(alpha: .1),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
                side: BorderSide(
                  color: Theme.of(context).colorScheme.outlineVariant,
                ),
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

class PhoneConversationView extends StatefulWidget {
  const PhoneConversationView({
    super.key,
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
    this.deliveries = const [],
    this.pendingApprovals = const [],
    this.onRetryDelivery,
    this.onReplyApproval,
    this.conversationTitle = '',
    this.conversationId = '',
    this.connectionState = 'ready',
    this.onQueue,
    this.reportReference,
    this.focusNode,
    this.onRemoveReport,
    this.onOpenReport,
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
  final List<PhoneDeliveryItem> deliveries;
  final List<PhoneApprovalRequest> pendingApprovals;
  final ValueChanged<String>? onRetryDelivery;
  final Future<void> Function(PhoneApprovalRequest, Map<String, Object?>)?
  onReplyApproval;
  final String conversationTitle;
  final String conversationId;
  final String connectionState;
  final VoidCallback? onQueue;
  final PhoneReportReference? reportReference;
  final FocusNode? focusNode;
  final VoidCallback? onRemoveReport;
  final ValueChanged<PhoneReportReference>? onOpenReport;

  @override
  State<PhoneConversationView> createState() => _PhoneConversationViewState();
}

class _PhoneConversationViewState extends State<PhoneConversationView> {
  final ScrollController _scrollController = ScrollController();
  bool _followLatest = true;
  bool _showJump = false;
  bool _jumpScheduled = false;

  String _conversationIdentity(PhoneConversationView view) =>
      view.conversationId.isEmpty
      ? '${view.workspaceRoot}:${view.conversationTitle}'
      : view.conversationId;

  String _latestVersion(PhoneConversationView view) => [
    view.messages.length,
    if (view.messages.isNotEmpty)
      '${view.messages.last.id}:${view.messages.last.text}:${view.messages.last.status}',
    view.previewArtifacts.length,
    view.pendingApprovals.map((item) => '${item.key}:${item.status}').join('|'),
    view.deliveries.map((item) => '${item.id}:${item.status}').join('|'),
  ].join('~');

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _scheduleLatest();
  }

  @override
  void didUpdateWidget(covariant PhoneConversationView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_conversationIdentity(oldWidget) != _conversationIdentity(widget)) {
      _followLatest = true;
      _showJump = false;
      _scheduleLatest();
    } else if (_latestVersion(oldWidget) != _latestVersion(widget)) {
      if (_followLatest) {
        _scheduleLatest();
      } else {
        _showJump = true;
      }
    }
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    _followLatest = _scrollController.position.extentAfter < 88;
    if (_followLatest && _showJump) setState(() => _showJump = false);
  }

  void _scheduleLatest([int remainingPasses = 3]) {
    if (_jumpScheduled || !mounted) return;
    _jumpScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _jumpScheduled = false;
      if (!mounted || !_scrollController.hasClients || !_followLatest) return;
      _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
      // A lazy list may discover a taller final item after the first jump.
      if (remainingPasses > 0) {
        _scheduleLatest(remainingPasses - 1);
        WidgetsBinding.instance.scheduleFrame();
      }
    });
  }

  void _jumpToLatest() {
    setState(() {
      _followLatest = true;
      _showJump = false;
    });
    _scheduleLatest();
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final normalizedStatus = widget.status.trim().toLowerCase();
    final notice =
        const {
          '',
          'ready',
          'diane running',
          'running',
        }.contains(normalizedStatus)
        ? ''
        : widget.status.trim();
    final pending = widget.deliveries
        .where((item) => item.status != 'accepted')
        .toList();
    final contents = <Widget>[
      if (widget.messages.isEmpty)
        const _PhoneEmptyState(
          icon: Icons.chat_bubble_outline_rounded,
          title: 'What shall we work on?',
          description:
              'Leave a thought, ask a question, or pick up something already in motion.',
        ),
      for (final message in widget.messages)
        Padding(
          padding: const EdgeInsets.only(bottom: 22),
          child: _MessageBubble(
            title: switch (message.role) {
              PhoneChatRole.user => 'You',
              PhoneChatRole.assistant => 'Diane',
              PhoneChatRole.status => 'Update',
            },
            text: phoneQuestionFromMessage(message.record) ?? message.text,
            reportReference: phoneReportFromMessage(message.record),
            onOpenReport: widget.onOpenReport,
            status: _workStatusLabel(message.status),
            alignRight: message.role == PhoneChatRole.user,
            isUpdate: message.role == PhoneChatRole.status,
          ),
        ),
      for (final item in pending)
        PhoneDeliveryNotice(
          item: item,
          onRetry: widget.onRetryDelivery == null
              ? null
              : () => widget.onRetryDelivery!(item.id),
        ),
      for (final request in widget.pendingApprovals)
        PhoneApprovalCard(
          key: ValueKey(request.key),
          request: request,
          onReply: widget.onReplyApproval,
        ),
      if (widget.previewArtifacts.isNotEmpty)
        _PreviewArtifactShelf(
          artifacts: widget.previewArtifacts,
          expanded: widget.previewArtifactsExpanded,
          onToggle: widget.onTogglePreviewArtifacts,
          onSelect: widget.onSelectArtifact,
        ),
      if (widget.attachments.isNotEmpty)
        SizedBox(
          height: 52,
          child: ListView(
            scrollDirection: Axis.horizontal,
            children: [
              for (final attachment in widget.attachments)
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: InputChip(
                    avatar: const Icon(Icons.image_outlined, size: 17),
                    label: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 170),
                      child: Text(
                        attachment.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    onDeleted: () => widget.onRemoveAttachment(attachment.id),
                  ),
                ),
            ],
          ),
        ),
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxHeight < 300;
        // At large system text sizes, keep citation and input reachable while
        // the keyboard is open. Detailed notices remain in scrollable history.
        final noticeInHistory = compact && widget.reportReference != null;
        final visibleContents = [
          ...contents,
          if (noticeInHistory && notice.isNotEmpty)
            Semantics(
              liveRegion: true,
              child: Text(notice, style: Theme.of(context).textTheme.bodySmall),
            ),
        ];
        return Column(
          children: [
            Expanded(
              child: Stack(
                children: [
                  Positioned.fill(
                    child: ListView.builder(
                      key: ValueKey(_conversationIdentity(widget)),
                      controller: _scrollController,
                      keyboardDismissBehavior:
                          ScrollViewKeyboardDismissBehavior.onDrag,
                      padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
                      itemCount: visibleContents.length,
                      itemBuilder: (context, index) => visibleContents[index],
                    ),
                  ),
                  if (_showJump)
                    Positioned(
                      right: 16,
                      bottom: 8,
                      child: FilledButton.tonalIcon(
                        onPressed: _jumpToLatest,
                        icon: const Icon(
                          Icons.arrow_downward_rounded,
                          size: 17,
                        ),
                        label: const Text('New updates'),
                      ),
                    ),
                ],
              ),
            ),
            Container(
              padding: EdgeInsets.fromLTRB(12, compact ? 4 : 10, 12, 12),
              decoration: BoxDecoration(
                color: colors.surface,
                border: Border(top: BorderSide(color: colors.outlineVariant)),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (widget.reportReference != null)
                    PhoneReportReferenceChip(
                      key: const ValueKey('phone-report-reference'),
                      reference: widget.reportReference!,
                      onOpen: widget.onOpenReport == null
                          ? null
                          : () => widget.onOpenReport!(widget.reportReference!),
                      onRemove: widget.onRemoveReport,
                    ),
                  if (notice.isNotEmpty && !noticeInHistory)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                      child: ConstrainedBox(
                        constraints: BoxConstraints(
                          maxHeight: compact ? 44 : 88,
                        ),
                        child: SingleChildScrollView(
                          child: Semantics(
                            liveRegion: true,
                            child: Text(
                              notice,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                        ),
                      ),
                    ),
                  if ((widget.activeRunLive || widget.busy) &&
                      !compact &&
                      notice.isEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(8, 0, 4, 7),
                      child: Row(
                        children: [
                          Icon(
                            widget.activeRunLive
                                ? Icons.timelapse_rounded
                                : Icons.arrow_upward_rounded,
                            size: 14,
                            color: colors.primary,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              widget.busy
                                  ? 'Preparing your message…'
                                  : 'Working on your computer',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ValueListenableBuilder<TextEditingValue>(
                    valueListenable: widget.controller,
                    builder: (context, value, _) {
                      final hasDraft =
                          value.text.trim().isNotEmpty ||
                          widget.attachments.isNotEmpty ||
                          widget.reportReference != null;
                      final showStop = widget.activeRunLive && !hasDraft;
                      final canSend = hasDraft && !widget.busy;
                      return TextField(
                        key: const ValueKey('phone-composer'),
                        controller: widget.controller,
                        focusNode: widget.focusNode,
                        minLines: 1,
                        maxLines: compact ? 1 : 4,
                        textCapitalization: TextCapitalization.sentences,
                        textInputAction: TextInputAction.newline,
                        style: Theme.of(context).textTheme.bodyLarge,
                        decoration: InputDecoration(
                          hintText: widget.activeRunLive
                              ? 'Steer this task…'
                              : widget.reportReference != null
                              ? 'Ask about this report…'
                              : 'Message Diane…',
                          hintMaxLines: 1,
                          prefixIcon: PopupMenuButton<String>(
                            tooltip: 'Message options',
                            icon: const Icon(Icons.add_rounded),
                            onSelected: (choice) {
                              if (choice == 'attach') {
                                widget.onPasteScreenshot();
                              }
                              if (choice == 'details') widget.onOpenPromptLog();
                              if (choice == 'queue') widget.onQueue?.call();
                            },
                            itemBuilder: (_) => [
                              const PopupMenuItem(
                                value: 'attach',
                                height: 52,
                                child: Text('Attach a screenshot'),
                              ),
                              PopupMenuItem(
                                value: 'details',
                                height: 52,
                                enabled: !widget.loadingPromptLog,
                                child: const Text('Work details'),
                              ),
                              if (widget.activeRunLive &&
                                  widget.onQueue != null)
                                PopupMenuItem(
                                  value: 'queue',
                                  height: 52,
                                  enabled: canSend,
                                  child: const Text('Send after this task'),
                                ),
                            ],
                          ),
                          suffixIcon: Padding(
                            padding: const EdgeInsets.all(4),
                            child: IconButton.filled(
                              tooltip: showStop
                                  ? 'Stop current task'
                                  : widget.activeRunLive
                                  ? 'Steer current task'
                                  : widget.connectionState == 'ready'
                                  ? 'Send message'
                                  : 'Save message to send',
                              style: IconButton.styleFrom(
                                minimumSize: const Size(48, 48),
                                maximumSize: const Size(48, 48),
                                foregroundColor: colors.onPrimary,
                              ),
                              onPressed: showStop && !widget.busy
                                  ? widget.onStopRun
                                  : canSend
                                  ? widget.onSend
                                  : null,
                              icon: widget.busy
                                  ? const SizedBox(
                                      width: 18,
                                      height: 18,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                      ),
                                    )
                                  : Icon(
                                      showStop
                                          ? Icons.stop_rounded
                                          : Icons.arrow_upward_rounded,
                                      size: 23,
                                    ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}

class PhoneDeliveryNotice extends StatelessWidget {
  const PhoneDeliveryNotice({super.key, required this.item, this.onRetry});
  final PhoneDeliveryItem item;
  final VoidCallback? onRetry;
  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final explanation = switch (item.status) {
      'queued' => 'Saved on this phone. Waiting to send.',
      'sending' => 'Sending to your computer…',
      'uncertain' =>
        'Your computer may have received this. Check delivery before sending again.',
      'failed' =>
        item.error.isEmpty
            ? 'This message has not been delivered.'
            : item.error,
      'expired' =>
        'This action has expired. Review the current work before trying again.',
      'needs_review' =>
        item.error.isNotEmpty
            ? item.error
            : 'Check this action on your computer. It will not be sent again automatically.',
      'accepted' => 'Received by your computer.',
      _ => item.statusLabel,
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.surfaceContainerLow,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: colors.outlineVariant),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 13, 14, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    item.status == 'sending'
                        ? Icons.north_east_rounded
                        : Icons.cloud_off_outlined,
                    size: 17,
                    color: colors.onSurfaceVariant,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      item.statusLabel,
                      style: Theme.of(context).textTheme.labelLarge,
                    ),
                  ),
                ],
              ),
              if (item.text.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 7),
                  child: Text(
                    item.text,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ),
              Padding(
                padding: const EdgeInsets.only(top: 5),
                child: Text(
                  explanation,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
              if (item.canRetry && onRetry != null)
                TextButton.icon(
                  onPressed: onRetry,
                  icon: Icon(
                    item.status == 'uncertain'
                        ? Icons.sync_rounded
                        : Icons.refresh_rounded,
                    size: 18,
                  ),
                  label: Text(
                    item.status == 'uncertain'
                        ? 'Check and retry'
                        : 'Retry delivery',
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class PhoneApprovalCard extends StatefulWidget {
  const PhoneApprovalCard({super.key, required this.request, this.onReply});
  final PhoneApprovalRequest request;
  final Future<void> Function(PhoneApprovalRequest, Map<String, Object?>)?
  onReply;
  @override
  State<PhoneApprovalCard> createState() => _PhoneApprovalCardState();
}

class _PhoneApprovalCardState extends State<PhoneApprovalCard> {
  final Map<String, String> answers = {};
  final Map<String, TextEditingController> answerControllers = {};
  bool submitting = false;
  String localError = '';

  @override
  void didUpdateWidget(covariant PhoneApprovalCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.request.key != widget.request.key) {
      answers.clear();
      for (final controller in answerControllers.values) {
        controller.dispose();
      }
      answerControllers.clear();
      localError = '';
    }
  }

  @override
  void dispose() {
    for (final controller in answerControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> submit(String decision) async {
    if (submitting || !widget.request.canAnswer || widget.onReply == null) {
      return;
    }
    setState(() {
      submitting = true;
      localError = '';
    });
    try {
      await widget.onReply!(widget.request, {
        'decision': decision,
        if (widget.request.isQuestion)
          'answers': Map<String, String>.from(answers),
      });
    } catch (_) {
      if (mounted) {
        setState(
          () => localError =
              'Could not confirm your reply. Check the connection and try again.',
        );
      }
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final request = widget.request;
    final colors = Theme.of(context).colorScheme;
    final enabled = request.canAnswer && !submitting && widget.onReply != null;
    final questions = request.questions;
    final ready =
        !request.isQuestion ||
        (questions.isNotEmpty &&
            questions.asMap().entries.every(
              (entry) =>
                  (answers[_asString(
                            entry.value['id'],
                            'question-${entry.key}',
                          )] ??
                          '')
                      .trim()
                      .isNotEmpty,
            ));
    final detail = _asString(
      request.params['reason'],
      _asString(request.params['description'], ''),
    );
    final command = request.params['command'];
    final commandText = command is List
        ? command.join(' ')
        : command is String
        ? command
        : '';
    final error = localError.isNotEmpty ? localError : request.error;
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
        decoration: BoxDecoration(
          color: colors.surfaceContainerLow,
          border: Border(left: BorderSide(color: colors.primary, width: 3)),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.chat_bubble_outline_rounded,
                  size: 19,
                  color: colors.primary,
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    request.title,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            if (detail.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  detail,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
            if (commandText.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: colors.surface,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: SelectableText(
                    commandText,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 13,
                    ),
                  ),
                ),
              ),
            for (final entry in questions.asMap().entries) ...[
              Padding(
                padding: const EdgeInsets.only(top: 16, bottom: 8),
                child: Text(
                  _asString(
                    entry.value['question'],
                    _asString(entry.value['header'], 'Your answer'),
                  ),
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
              ),
              ..._questionOptions(context, entry.key, entry.value, enabled),
            ],
            if (error.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(
                  error,
                  style: Theme.of(
                    context,
                  ).textTheme.bodySmall?.copyWith(color: colors.error),
                ),
              ),
            if (!request.canAnswer)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(switch (request.status) {
                  'expired' =>
                    'This request has expired. Refresh to see what is needed now.',
                  'sending' =>
                    'Waiting for your computer to confirm your reply.',
                  'accepted' => 'Your computer received your reply.',
                  'needs_review' =>
                    'Check this request on your computer before responding again.',
                  'rejected' || 'failed' =>
                    'Your reply was not accepted. Refresh to see what is needed now.',
                  _ => 'This request is no longer available to answer.',
                }, style: Theme.of(context).textTheme.bodySmall),
              ),
            if (request.canAnswer && widget.onReply == null)
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Text(
                  'Open this conversation on your computer to respond.',
                ),
              ),
            if (request.canAnswer && widget.onReply != null)
              Padding(
                padding: const EdgeInsets.only(top: 14),
                child: Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    FilledButton(
                      onPressed: enabled && ready
                          ? () => unawaited(submit('accept'))
                          : null,
                      child: Text(
                        submitting
                            ? 'Sending…'
                            : request.isQuestion
                            ? 'Send answer'
                            : 'Allow once',
                      ),
                    ),
                    if (!request.isQuestion)
                      OutlinedButton(
                        onPressed: enabled
                            ? () => unawaited(submit('decline'))
                            : null,
                        child: const Text('Deny'),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  List<Widget> _questionOptions(
    BuildContext context,
    int index,
    Map<String, dynamic> question,
    bool enabled,
  ) {
    final id = _asString(question['id'], 'question-$index');
    final controller = answerControllers.putIfAbsent(
      id,
      TextEditingController.new,
    );
    final options =
        (question['options'] is List ? question['options'] as List : const [])
            .whereType<Map>()
            .map((option) => Map<String, dynamic>.from(option))
            .toList();
    final colors = Theme.of(context).colorScheme;
    return [
      for (final option in options)
        Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Material(
            color: answers[id] == option['label']
                ? colors.primary.withValues(alpha: .1)
                : colors.surface,
            borderRadius: BorderRadius.circular(10),
            child: InkWell(
              onTap: enabled
                  ? () => setState(() {
                      controller.clear();
                      answers[id] = _asString(option['label'], '');
                    })
                  : null,
              borderRadius: BorderRadius.circular(10),
              child: Semantics(
                checked: answers[id] == option['label'],
                inMutuallyExclusiveGroup: true,
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        answers[id] == option['label']
                            ? Icons.radio_button_checked
                            : Icons.radio_button_unchecked,
                        size: 20,
                        color: enabled
                            ? colors.primary
                            : colors.onSurfaceVariant,
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              _asString(option['label'], 'Option'),
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                            if (_asString(option['description'], '').isNotEmpty)
                              Text(
                                _asString(option['description'], ''),
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      Padding(
        padding: const EdgeInsets.only(top: 3),
        child: TextField(
          controller: controller,
          enabled: enabled,
          minLines: 1,
          maxLines: 3,
          textCapitalization: TextCapitalization.sentences,
          decoration: InputDecoration(
            hintText: options.isEmpty
                ? 'Your answer'
                : 'Or write your own answer',
          ),
          onChanged: (value) => setState(() => answers[id] = value),
        ),
      ),
    ];
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Divider(),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.attach_file_rounded, size: 20),
          title: Text(
            '${artifacts.length} ${artifacts.length == 1 ? 'output' : 'outputs'}',
            style: Theme.of(context).textTheme.labelLarge,
          ),
          trailing: Icon(
            expanded ? Icons.expand_less_rounded : Icons.expand_more_rounded,
          ),
          onTap: onToggle,
        ),
        if (expanded)
          for (final artifact in artifacts)
            ListTile(
              contentPadding: const EdgeInsets.only(left: 4, right: 0),
              leading: Icon(_artifactIcon(artifact), size: 20),
              title: Text(
                artifact.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              trailing: const Icon(Icons.chevron_right_rounded, size: 18),
              onTap: () => onSelect(artifact),
            ),
      ],
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

/// The same source stays visible in the draft and in the sent message.
class PhoneReportReferenceChip extends StatelessWidget {
  const PhoneReportReferenceChip({
    super.key,
    required this.reference,
    this.onOpen,
    this.onRemove,
  });

  final PhoneReportReference reference;
  final VoidCallback? onOpen, onRemove;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: TextButton.icon(
            key: onRemove == null
                ? null
                : const ValueKey('phone-open-report-reference'),
            onPressed: onOpen,
            style: TextButton.styleFrom(
              alignment: Alignment.centerLeft,
              minimumSize: const Size(48, 48),
              padding: const EdgeInsets.symmetric(horizontal: 8),
            ),
            icon: const Icon(Icons.description_outlined, size: 18),
            label: Text(
              'Report: ${reference.title}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ),
        if (onRemove != null)
          IconButton(
            key: const ValueKey('phone-remove-report'),
            tooltip: 'Remove report reference',
            onPressed: onRemove,
            icon: const Icon(Icons.close_rounded, size: 18),
          ),
      ],
    );
  }
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({
    required this.title,
    required this.text,
    this.status = '',
    this.alignRight = false,
    this.isUpdate = false,
    this.reportReference,
    this.onOpenReport,
  });
  final String title;
  final String text;
  final String status;
  final bool alignRight;
  final bool isUpdate;
  final PhoneReportReference? reportReference;
  final ValueChanged<PhoneReportReference>? onOpenReport;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    if (isUpdate) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 4, right: 8),
              child: Icon(
                Icons.subdirectory_arrow_right_rounded,
                size: 15,
                color: colors.onSurfaceVariant,
              ),
            ),
            Expanded(
              child: Text(text, style: Theme.of(context).textTheme.bodySmall),
            ),
          ],
        ),
      );
    }
    if (alignRight) {
      return Align(
        alignment: Alignment.centerRight,
        child: FractionallySizedBox(
          widthFactor: .91,
          child: Container(
            padding: const EdgeInsets.fromLTRB(15, 12, 15, 12),
            decoration: BoxDecoration(
              color: colors.surfaceContainerHighest.withValues(alpha: .7),
              borderRadius: const BorderRadius.only(
                topLeft: Radius.circular(18),
                topRight: Radius.circular(18),
                bottomLeft: Radius.circular(18),
                bottomRight: Radius.circular(5),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (reportReference != null)
                  PhoneReportReferenceChip(
                    reference: reportReference!,
                    onOpen: onOpenReport == null
                        ? null
                        : () => onOpenReport!(reportReference!),
                  ),
                SelectableText(
                  text,
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
                if (status.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      status,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
              ],
            ),
          ),
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 24,
              height: 24,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: colors.primary.withValues(alpha: .1),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                'd',
                style: TextStyle(
                  fontFamily: 'serif',
                  fontSize: 19,
                  height: 1,
                  fontWeight: FontWeight.w700,
                  color: colors.primary,
                ),
              ),
            ),
            const SizedBox(width: 8),
            Text(title, style: Theme.of(context).textTheme.labelLarge),
            if (status.isNotEmpty)
              Flexible(
                child: Padding(
                  padding: const EdgeInsets.only(left: 8),
                  child: Text(
                    status,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 11),
        SelectionArea(child: _MarkdownPreviewBody(markdown: text)),
      ],
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
    final colors = Theme.of(context).colorScheme;
    final queryText = query.trim().toLowerCase();
    final visible = widget.files
        .where(
          (entry) =>
              queryText.isEmpty ||
              '${entry.name} ${entry.relativePath}'.toLowerCase().contains(
                queryText,
              ),
        )
        .toList();
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
      children: [
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          initiallyExpanded: widget.workspaceRoot.isEmpty,
          title: Text(
            widget.workspaceRoot.isEmpty
                ? 'Choose a project folder'
                : _baseName(widget.workspaceRoot),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          subtitle: Text(
            'Files on your computer',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          childrenPadding: const EdgeInsets.only(bottom: 16),
          children: [
            TextField(
              controller: widget.controller,
              onChanged: _scheduleSuggestions,
              onSubmitted: (value) =>
                  unawaited(widget.onApplyWorkspaceRoot(value)),
              decoration: InputDecoration(
                labelText: 'Project folder',
                prefixIcon: const Icon(Icons.folder_open_outlined),
                suffixIcon: IconButton(
                  tooltip: 'Use this folder',
                  icon: const Icon(Icons.check_rounded),
                  onPressed: () => unawaited(
                    widget.onApplyWorkspaceRoot(widget.controller.text),
                  ),
                ),
              ),
            ),
            for (final suggestion in suggestions.take(5))
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.folder_outlined),
                title: Text(
                  suggestion.label.isEmpty
                      ? _baseName(suggestion.path)
                      : suggestion.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  suggestion.path,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () {
                  widget.controller.text = suggestion.path;
                  setState(() => suggestions = const []);
                  unawaited(widget.onApplyWorkspaceRoot(suggestion.path));
                },
              ),
          ],
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            Expanded(
              child: TextField(
                onChanged: (value) => setState(() => query = value),
                decoration: const InputDecoration(
                  hintText: 'Find a file',
                  prefixIcon: Icon(Icons.search_rounded),
                ),
              ),
            ),
            IconButton(
              tooltip: 'Refresh files',
              onPressed: widget.loading
                  ? null
                  : () => unawaited(widget.onRefresh()),
              icon: const Icon(Icons.refresh_rounded),
            ),
          ],
        ),
        const SizedBox(height: 16),
        if (widget.loading) const LinearProgressIndicator(minHeight: 2),
        if (!widget.loading && visible.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 24),
            child: Text(
              queryText.isEmpty ? widget.status : 'No files match your search.',
              style: Theme.of(
                context,
              ).textTheme.bodyMedium?.copyWith(color: colors.onSurfaceVariant),
            ),
          ),
        for (final file in visible)
          Column(
            children: [
              ListTile(
                contentPadding: EdgeInsets.only(
                  left: file.depth.clamp(0, 3) * 8.0,
                ),
                leading: Icon(
                  file.isDirectory
                      ? Icons.folder_outlined
                      : Icons.description_outlined,
                  size: 21,
                ),
                title: Text(
                  file.name,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  file.isDirectory ? 'Folder' : _formatBytes(file.size),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                trailing: const Icon(Icons.chevron_right_rounded, size: 18),
                onTap: () => widget.onSelectFile(file),
              ),
              const Divider(),
            ],
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
    this.onAsk,
    this.showAsk = false,
  });

  final WorkspaceFileEntry? activeFile;
  final String title;
  final String content;
  final String status;
  final List<PhonePreviewArtifact> previewArtifacts;
  final bool previewArtifactsExpanded;
  final VoidCallback onTogglePreviewArtifacts;
  final ValueChanged<PhonePreviewArtifact> onSelectArtifact;
  final VoidCallback? onAsk;
  final bool showAsk;

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
                color: Theme.of(context).colorScheme.surface,
                border: Border.all(
                  color: Theme.of(context).colorScheme.outlineVariant,
                ),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Text(
                            displayTitle,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.titleMedium
                                ?.copyWith(fontWeight: FontWeight.w700),
                          ),
                        ),
                        if (showAsk) ...[
                          const SizedBox(width: 8),
                          FilledButton.icon(
                            key: const ValueKey('phone-ask-report'),
                            onPressed: onAsk,
                            icon: const Icon(
                              Icons.chat_bubble_outline_rounded,
                              size: 18,
                            ),
                            label: const Text('Ask'),
                          ),
                        ],
                      ],
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
          color: Theme.of(context).colorScheme.surface,
          border: Border.all(
            color: Theme.of(context).colorScheme.outlineVariant,
          ),
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
        color: Theme.of(context).colorScheme.surfaceContainerLow,
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
              border: Border.all(
                color: Theme.of(
                  context,
                ).colorScheme.primary.withValues(alpha: .35),
              ),
              borderRadius: BorderRadius.circular(8),
              color: Theme.of(context).colorScheme.surfaceContainerLow,
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
            style: TextStyle(
              fontFamily: 'monospace',
              backgroundColor: Theme.of(
                context,
              ).colorScheme.surfaceContainerHighest,
            ),
          ),
        );
      } else {
        spans.add(
          TextSpan(
            text: match.group(4),
            style: TextStyle(
              color: Theme.of(context).colorScheme.primary,
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
    return Text.rich(TextSpan(style: baseStyle, children: spans));
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
              Expanded(
                child: _MiniChip(Icons.save_outlined, widget.saveStatus),
              ),
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
                color: Theme.of(context).colorScheme.surface,
                border: Border.all(
                  color: Theme.of(context).colorScheme.outlineVariant,
                ),
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
                fillColor: Theme.of(context).colorScheme.surfaceContainerLow,
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
