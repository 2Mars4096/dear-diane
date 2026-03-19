const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true,

  dialog: {
    openFile: (options) => ipcRenderer.invoke("dialog:openFile", options),
    openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
    saveFile: (options) => ipcRenderer.invoke("dialog:saveFile", options),
  },

  fs: {
    readFile: (filePath) => ipcRenderer.invoke("fs:readFile", filePath),
    writeFile: (filePath, content) => ipcRenderer.invoke("fs:writeFile", filePath, content),
    writeTempAttachment: (payload) => ipcRenderer.invoke("fs:writeTempAttachment", payload),
    readDir: (dirPath) => ipcRenderer.invoke("fs:readDir", dirPath),
    stat: (filePath) => ipcRenderer.invoke("fs:stat", filePath),
    mkdir: (dirPath) => ipcRenderer.invoke("fs:mkdir", dirPath),
    rename: (oldPath, newPath) => ipcRenderer.invoke("fs:rename", oldPath, newPath),
    delete: (filePath) => ipcRenderer.invoke("fs:delete", filePath),
    exists: (filePath) => ipcRenderer.invoke("fs:exists", filePath),
    readGitignore: (rootPath) => ipcRenderer.invoke("fs:readGitignore", rootPath),
  },

  shell: {
    openPath: (filePath) => ipcRenderer.invoke("shell:openPath", filePath),
    run: (opts) => ipcRenderer.invoke("shell:run", opts),
  },

  search: {
    ripgrep: (opts) => ipcRenderer.invoke("search:ripgrep", opts),
    replaceInFile: (filePath, replacements) => ipcRenderer.invoke("search:replaceInFile", filePath, replacements),
  },

  git: {
    status: (cwd) => ipcRenderer.invoke("git:status", cwd),
    diff: (cwd, filePath) => ipcRenderer.invoke("git:diff", cwd, filePath),
    diffStaged: (cwd, filePath) => ipcRenderer.invoke("git:diffStaged", cwd, filePath),
    log: (cwd, maxCount) => ipcRenderer.invoke("git:log", cwd, maxCount),
    stage: (cwd, filePath) => ipcRenderer.invoke("git:stage", cwd, filePath),
    unstage: (cwd, filePath) => ipcRenderer.invoke("git:unstage", cwd, filePath),
    commit: (cwd, message) => ipcRenderer.invoke("git:commit", cwd, message),
    branch: (cwd) => ipcRenderer.invoke("git:branch", cwd),
    fileShow: (cwd, ref, filePath) => ipcRenderer.invoke("git:fileShow", cwd, ref, filePath),
    push: (cwd, remote, branch) => ipcRenderer.invoke("git:push", cwd, remote, branch),
    pull: (cwd, remote, branch) => ipcRenderer.invoke("git:pull", cwd, remote, branch),
    branchList: (cwd) => ipcRenderer.invoke("git:branchList", cwd),
    checkout: (cwd, branchName) => ipcRenderer.invoke("git:checkout", cwd, branchName),
    createBranch: (cwd, branchName) => ipcRenderer.invoke("git:createBranch", cwd, branchName),
    remoteInfo: (cwd) => ipcRenderer.invoke("git:remoteInfo", cwd),
    aheadBehind: (cwd) => ipcRenderer.invoke("git:aheadBehind", cwd),
    blame: (cwd, filePath) => ipcRenderer.invoke("git:blame", cwd, filePath),
    stash: (cwd, message) => ipcRenderer.invoke("git:stash", cwd, message),
    stashList: (cwd) => ipcRenderer.invoke("git:stashList", cwd),
    stashPop: (cwd, index) => ipcRenderer.invoke("git:stashPop", cwd, index),
    stashApply: (cwd, index) => ipcRenderer.invoke("git:stashApply", cwd, index),
    stashDrop: (cwd, index) => ipcRenderer.invoke("git:stashDrop", cwd, index),
    stashShow: (cwd, index) => ipcRenderer.invoke("git:stashShow", cwd, index),
    cherryPick: (cwd, hash) => ipcRenderer.invoke("git:cherryPick", cwd, hash),
    logGraph: (cwd, maxCount) => ipcRenderer.invoke("git:logGraph", cwd, maxCount),
    rebaseCommitList: (cwd, count) => ipcRenderer.invoke("git:rebaseCommitList", cwd, count),
    rebaseStart: (cwd, entries) => ipcRenderer.invoke("git:rebaseStart", cwd, entries),
    rebaseAbort: (cwd) => ipcRenderer.invoke("git:rebaseAbort", cwd),
    rebaseContinue: (cwd) => ipcRenderer.invoke("git:rebaseContinue", cwd),
    rebaseStatus: (cwd) => ipcRenderer.invoke("git:rebaseStatus", cwd),
    conflictFiles: (cwd) => ipcRenderer.invoke("git:conflictFiles", cwd),
    conflictContent: (cwd, filePath) => ipcRenderer.invoke("git:conflictContent", cwd, filePath),
    showBase: (cwd, filePath) => ipcRenderer.invoke("git:showBase", cwd, filePath),
    showOurs: (cwd, filePath) => ipcRenderer.invoke("git:showOurs", cwd, filePath),
    showTheirs: (cwd, filePath) => ipcRenderer.invoke("git:showTheirs", cwd, filePath),
    markResolved: (cwd, filePath, content) => ipcRenderer.invoke("git:markResolved", cwd, filePath, content),
  },

  watch: {
    start: (filePath) => ipcRenderer.invoke("watch:start", filePath),
    stop: (filePath) => ipcRenderer.invoke("watch:stop", filePath),
    onChange: (callback) => {
      const handler = (_event, filePath) => callback(filePath);
      ipcRenderer.on("watch:changed", handler);
      return () => ipcRenderer.removeListener("watch:changed", handler);
    },
  },

  lsp: {
    start: (rootPath) => ipcRenderer.invoke("lsp:start", rootPath),
    didOpen: (params) => ipcRenderer.invoke("lsp:didOpen", params),
    didChange: (params) => ipcRenderer.invoke("lsp:didChange", params),
    didSave: (params) => ipcRenderer.invoke("lsp:didSave", params),
    didClose: (params) => ipcRenderer.invoke("lsp:didClose", params),
    completion: (params) => ipcRenderer.invoke("lsp:completion", params),
    hover: (params) => ipcRenderer.invoke("lsp:hover", params),
    definition: (params) => ipcRenderer.invoke("lsp:definition", params),
    references: (params) => ipcRenderer.invoke("lsp:references", params),
    documentSymbol: (params) => ipcRenderer.invoke("lsp:documentSymbol", params),
    workspaceSymbol: (params) => ipcRenderer.invoke("lsp:workspaceSymbol", params),
    formatting: (params) => ipcRenderer.invoke("lsp:formatting", params),
    codeAction: (params) => ipcRenderer.invoke("lsp:codeAction", params),
    rename: (params) => ipcRenderer.invoke("lsp:rename", params),
    signatureHelp: (params) => ipcRenderer.invoke("lsp:signatureHelp", params),
    prepareCallHierarchy: (params) => ipcRenderer.invoke("lsp:prepareCallHierarchy", params),
    incomingCalls: (params) => ipcRenderer.invoke("lsp:incomingCalls", params),
    outgoingCalls: (params) => ipcRenderer.invoke("lsp:outgoingCalls", params),
    onDiagnostics: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("lsp:diagnostics", handler);
      return () => ipcRenderer.removeListener("lsp:diagnostics", handler);
    },
    onNotification: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("lsp:notification", handler);
      return () => ipcRenderer.removeListener("lsp:notification", handler);
    },
    onLog: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("lsp:log", handler);
      return () => ipcRenderer.removeListener("lsp:log", handler);
    },
  },

  terminal: {
    create: (options) => ipcRenderer.invoke("terminal:create", options),
    write: (id, data) => ipcRenderer.invoke("terminal:write", id, data),
    resize: (id, cols, rows) => ipcRenderer.invoke("terminal:resize", id, cols, rows),
    kill: (id) => ipcRenderer.invoke("terminal:kill", id),
    onData: (callback) => {
      const handler = (_event, id, data) => callback(id, data);
      ipcRenderer.on("terminal:data", handler);
      return () => ipcRenderer.removeListener("terminal:data", handler);
    },
    onExit: (callback) => {
      const handler = (_event, id, code) => callback(id, code);
      ipcRenderer.on("terminal:exit", handler);
      return () => ipcRenderer.removeListener("terminal:exit", handler);
    },
  },

  extension: {
    readFile: (filePath) => ipcRenderer.invoke("extension:readFile", filePath),
    install: (itemId, downloadUrl) => ipcRenderer.invoke("extension:install", itemId, downloadUrl),
    uninstall: (itemId) => ipcRenderer.invoke("extension:uninstall", itemId),
    listInstalled: () => ipcRenderer.invoke("extension:listInstalled"),
    getManifest: (itemId) => ipcRenderer.invoke("extension:getManifest", itemId),
    importVsix: (filePath) => ipcRenderer.invoke("extension:importVsix", filePath),
  },

  extensionHost: {
    start: () => ipcRenderer.invoke("extensionHost:start"),
    stop: () => ipcRenderer.invoke("extensionHost:stop"),
    status: () => ipcRenderer.invoke("extensionHost:status"),
    executeCommand: (commandId, args) => ipcRenderer.invoke("extensionHost:executeCommand", commandId, args),
    getCommands: () => ipcRenderer.invoke("extensionHost:getCommands"),
    getLanguageProviders: () => ipcRenderer.invoke("extensionHost:getLanguageProviders"),
    invokeLanguageProvider: (payload) => ipcRenderer.invoke("extensionHost:invokeLanguageProvider", payload),
    onEvent: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("extensionHost:event", handler);
      return () => ipcRenderer.removeListener("extensionHost:event", handler);
    },
  },

  github: {
    checkAvailable: (cwd) => ipcRenderer.invoke("github:checkAvailable", cwd),
    listPRs: (cwd) => ipcRenderer.invoke("github:listPRs", cwd),
    getPR: (cwd, number) => ipcRenderer.invoke("github:getPR", cwd, number),
    createPR: (cwd, title, body, base, head) => ipcRenderer.invoke("github:createPR", cwd, title, body, base, head),
    listIssues: (cwd) => ipcRenderer.invoke("github:listIssues", cwd),
    prDiff: (cwd, number) => ipcRenderer.invoke("github:prDiff", cwd, number),
    prReview: (cwd, number, action, body) => ipcRenderer.invoke("github:prReview", cwd, number, action, body),
    prMerge: (cwd, number, method) => ipcRenderer.invoke("github:prMerge", cwd, number, method),
    prCheckout: (cwd, number) => ipcRenderer.invoke("github:prCheckout", cwd, number),
  },

  debug: {
    start: (config) => ipcRenderer.invoke("debug:start", config),
    stop: () => ipcRenderer.invoke("debug:stop"),
    restart: () => ipcRenderer.invoke("debug:restart"),
    setBreakpoints: (filePath, breakpoints) => ipcRenderer.invoke("debug:setBreakpoints", filePath, breakpoints),
    continue: (threadId) => ipcRenderer.invoke("debug:continue", threadId),
    next: (threadId) => ipcRenderer.invoke("debug:next", threadId),
    stepIn: (threadId) => ipcRenderer.invoke("debug:stepIn", threadId),
    stepOut: (threadId) => ipcRenderer.invoke("debug:stepOut", threadId),
    pause: (threadId) => ipcRenderer.invoke("debug:pause", threadId),
    threads: () => ipcRenderer.invoke("debug:threads"),
    stackTrace: (threadId) => ipcRenderer.invoke("debug:stackTrace", threadId),
    scopes: (frameId) => ipcRenderer.invoke("debug:scopes", frameId),
    variables: (variablesReference) => ipcRenderer.invoke("debug:variables", variablesReference),
    evaluate: (expression, frameId) => ipcRenderer.invoke("debug:evaluate", expression, frameId),
    listProcesses: () => ipcRenderer.invoke("debug:listProcesses"),
    attach: (config) => ipcRenderer.invoke("debug:attach", config),
    onEvent: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("debug:event", handler);
      return () => ipcRenderer.removeListener("debug:event", handler);
    },
  },

  skills: {
    scan: () => ipcRenderer.invoke("skills:scan"),
    readSkill: (skillPath) => ipcRenderer.invoke("skills:readSkill", skillPath),
    importFromUrl: (url) => ipcRenderer.invoke("skills:importFromUrl", url),
    remove: (skillId) => ipcRenderer.invoke("skills:remove", skillId),
    toggleEnabled: (skillId) => ipcRenderer.invoke("skills:toggleEnabled", skillId),
    createTemplate: (name) => ipcRenderer.invoke("skills:createTemplate", name),
  },

  mcp: {
    listInstalled: () => ipcRenderer.invoke("mcp:listInstalled"),
    install: (serverId, config) => ipcRenderer.invoke("mcp:install", serverId, config),
    remove: (serverId) => ipcRenderer.invoke("mcp:remove", serverId),
    getConfig: (serverId) => ipcRenderer.invoke("mcp:getConfig", serverId),
    start: (serverId) => ipcRenderer.invoke("mcp:start", serverId),
    stop: (serverId) => ipcRenderer.invoke("mcp:stop", serverId),
    status: () => ipcRenderer.invoke("mcp:status"),
    toggleEnabled: (serverId) => ipcRenderer.invoke("mcp:toggleEnabled", serverId),
  },

  backend: {
    restart: () => ipcRenderer.invoke("backend:restart"),
    stop: () => ipcRenderer.invoke("backend:stop"),
    getStatus: () => ipcRenderer.invoke("backend:getStatus"),
    onLog: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("backend:log", handler);
      return () => ipcRenderer.removeListener("backend:log", handler);
    },
    onStatusChange: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on("backend:statusChange", handler);
      return () => ipcRenderer.removeListener("backend:statusChange", handler);
    },
  },

  updater: {
    check: () => ipcRenderer.invoke("updater:check"),
    download: () => ipcRenderer.invoke("updater:download"),
    install: () => ipcRenderer.invoke("updater:install"),
    onUpdateAvailable: (cb) => {
      const handler = (_event, data) => cb(data);
      ipcRenderer.on("updater:update-available", handler);
      return () => ipcRenderer.removeListener("updater:update-available", handler);
    },
    onUpToDate: (cb) => {
      const handler = () => cb();
      ipcRenderer.on("updater:up-to-date", handler);
      return () => ipcRenderer.removeListener("updater:up-to-date", handler);
    },
    onDownloadProgress: (cb) => {
      const handler = (_event, data) => cb(data);
      ipcRenderer.on("updater:download-progress", handler);
      return () => ipcRenderer.removeListener("updater:download-progress", handler);
    },
    onUpdateDownloaded: (cb) => {
      const handler = () => cb();
      ipcRenderer.on("updater:update-downloaded", handler);
      return () => ipcRenderer.removeListener("updater:update-downloaded", handler);
    },
    onError: (cb) => {
      const handler = (_event, msg) => cb(msg);
      ipcRenderer.on("updater:error", handler);
      return () => ipcRenderer.removeListener("updater:error", handler);
    },
  },
});
