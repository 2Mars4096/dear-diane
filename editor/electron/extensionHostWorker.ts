/**
 * Runs in a forked Node.js process. Loads VS Code extensions and provides
 * a partial vscode API shim so that many extensions work out-of-the-box.
 */

const extensionPaths: string[] = JSON.parse(
  process.env.EXTENSION_PATHS ?? "[]",
);

// ---------------------------------------------------------------------------
// Activation events
// ---------------------------------------------------------------------------

interface PendingActivation {
  extensionId: string;
  events: string[];
  mainPath: string;
  extPath: string;
  pkg: any;
  activated: boolean;
}

const pendingActivations: PendingActivation[] = [];

function shouldActivate(events: string[], trigger: string): boolean {
  return events.some((e) => {
    if (e === "*") return true;
    if (e === trigger) return true;
    if (e.startsWith("onLanguage:") && trigger.startsWith("onLanguage:"))
      return e === trigger;
    if (e.startsWith("onCommand:") && trigger.startsWith("onCommand:"))
      return e === trigger;
    if (e.startsWith("workspaceContains:")) return true;
    return false;
  });
}

async function activateByEvent(trigger: string) {
  for (const pending of pendingActivations) {
    if (pending.activated) continue;
    if (!shouldActivate(pending.events, trigger)) continue;
    pending.activated = true;
    try {
      await doActivate(pending);
    } catch (err: any) {
      sendEvent("extension:error", {
        path: pending.extPath,
        error: err.message,
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Partial vscode namespace
// ---------------------------------------------------------------------------

const vscodeApi = createVSCodeApi();

function createVSCodeApi() {
  const commands = new Map<string, Function>();
  const disposables: Array<{ dispose: () => void }> = [];
  const statusBarItems: any[] = [];
  const outputChannels = new Map<string, string[]>();

  return {
    commands: {
      registerCommand(id: string, callback: Function) {
        commands.set(id, callback);
        const disposable = { dispose: () => commands.delete(id) };
        disposables.push(disposable);
        return disposable;
      },
      async executeCommand(id: string, ...args: any[]) {
        const cmd = commands.get(id);
        if (cmd) return cmd(...args);
        await activateByEvent(`onCommand:${id}`);
        const cmdAfter = commands.get(id);
        if (cmdAfter) return cmdAfter(...args);
        sendEvent("command:execute", { id, args });
      },
      getCommands() {
        return Array.from(commands.keys());
      },
    },

    window: {
      showInformationMessage(message: string, ...items: string[]) {
        sendEvent("window:showMessage", { type: "info", message, items });
        return Promise.resolve(items[0]);
      },
      showWarningMessage(message: string, ...items: string[]) {
        sendEvent("window:showMessage", { type: "warning", message, items });
        return Promise.resolve(items[0]);
      },
      showErrorMessage(message: string, ...items: string[]) {
        sendEvent("window:showMessage", { type: "error", message, items });
        return Promise.resolve(items[0]);
      },
      createStatusBarItem(alignment?: number, priority?: number) {
        const item = {
          text: "",
          tooltip: "",
          command: "",
          alignment: alignment ?? 1,
          priority: priority ?? 0,
          show() {
            sendEvent("statusBar:show", this);
          },
          hide() {
            sendEvent("statusBar:hide", {
              id: statusBarItems.indexOf(this),
            });
          },
          dispose() {
            sendEvent("statusBar:dispose", {
              id: statusBarItems.indexOf(this),
            });
          },
        };
        statusBarItems.push(item);
        return item;
      },
      createOutputChannel(name: string) {
        outputChannels.set(name, []);
        return {
          name,
          append(value: string) {
            outputChannels.get(name)?.push(value);
            sendEvent("output:append", { name, value });
          },
          appendLine(value: string) {
            this.append(value + "\n");
          },
          clear() {
            outputChannels.set(name, []);
            sendEvent("output:clear", { name });
          },
          show() {
            sendEvent("output:show", { name });
          },
          hide() {
            sendEvent("output:hide", { name });
          },
          dispose() {
            outputChannels.delete(name);
          },
        };
      },
    },

    workspace: {
      workspaceFolders: [] as any[],
      getConfiguration(section?: string) {
        return {
          get<T>(key: string, defaultValue?: T): T {
            return defaultValue as T;
          },
          has(_key: string) {
            return false;
          },
          update(key: string, value: any) {
            sendEvent("config:update", { section, key, value });
          },
        };
      },
      onDidChangeConfiguration: createEventEmitter(),
      onDidSaveTextDocument: createEventEmitter(),
      onDidOpenTextDocument: createEventEmitter(),
      onDidCloseTextDocument: createEventEmitter(),
    },

    languages: {
      registerCompletionItemProvider(
        selector: any,
        provider: any,
        ...triggers: string[]
      ) {
        sendEvent("languages:registerProvider", {
          type: "completion",
          selector,
          triggers,
        });
        return { dispose() {} };
      },
      registerHoverProvider(selector: any, _provider: any) {
        sendEvent("languages:registerProvider", {
          type: "hover",
          selector,
        });
        return { dispose() {} };
      },
      registerDefinitionProvider(selector: any, _provider: any) {
        sendEvent("languages:registerProvider", {
          type: "definition",
          selector,
        });
        return { dispose() {} };
      },
      createDiagnosticCollection(name?: string) {
        return {
          name: name ?? "",
          set(uri: any, diagnostics: any[]) {
            sendEvent("diagnostics:set", { uri, diagnostics, name });
          },
          clear() {
            sendEvent("diagnostics:clear", { name });
          },
          dispose() {
            sendEvent("diagnostics:dispose", { name });
          },
        };
      },
    },

    extensions: {
      getExtension(_id: string) {
        return null;
      },
      all: [],
    },

    Uri: {
      file(p: string) {
        return {
          scheme: "file",
          path: p,
          fsPath: p,
          toString: () => `file://${p}`,
        };
      },
      parse(uri: string) {
        return {
          scheme: "file",
          path: uri,
          fsPath: uri,
          toString: () => uri,
        };
      },
    },

    Position: class {
      constructor(
        public line: number,
        public character: number,
      ) {}
    },
    Range: class {
      constructor(
        public start: any,
        public end: any,
      ) {}
    },
    Location: class {
      constructor(
        public uri: any,
        public range: any,
      ) {}
    },
    Diagnostic: class {
      constructor(
        public range: any,
        public message: string,
        public severity?: number,
      ) {}
    },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    CompletionItem: class {
      constructor(
        public label: string,
        public kind?: number,
      ) {}
    },
    CompletionItemKind: {
      Text: 0,
      Method: 1,
      Function: 2,
      Constructor: 3,
      Field: 4,
      Variable: 5,
      Class: 6,
      Interface: 7,
      Module: 8,
      Property: 9,
      Unit: 10,
      Value: 11,
      Enum: 12,
      Keyword: 13,
      Snippet: 14,
      Color: 15,
      File: 16,
      Reference: 17,
      Folder: 18,
      EnumMember: 19,
      Constant: 20,
      Struct: 21,
      Event: 22,
      Operator: 23,
    },
    StatusBarAlignment: { Left: 1, Right: 2 },
    TreeItem: class {
      constructor(
        public label: string,
        public collapsibleState?: number,
      ) {}
    },
    TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
    EventEmitter: class {
      private listeners: Function[] = [];
      event = (listener: Function) => {
        this.listeners.push(listener);
        return {
          dispose: () => {
            this.listeners = this.listeners.filter((l) => l !== listener);
          },
        };
      };
      fire(data: any) {
        this.listeners.forEach((l) => l(data));
      }
      dispose() {
        this.listeners = [];
      }
    },
  };
}

function createEventEmitter() {
  const listeners: Function[] = [];
  const event = (listener: Function) => {
    listeners.push(listener);
    return {
      dispose: () => {
        const idx = listeners.indexOf(listener);
        if (idx >= 0) listeners.splice(idx, 1);
      },
    };
  };
  (event as any)._fire = (data: any) => listeners.forEach((l) => l(data));
  return event;
}

function sendEvent(event: string, data: any) {
  process.send?.({ type: "event", event, data });
}

// ---------------------------------------------------------------------------
// Module interception — make `require("vscode")` return our shim
// ---------------------------------------------------------------------------

function installVscodeShim() {
  const Module = require("module");
  const originalResolve = Module._resolveFilename;
  Module._resolveFilename = function (request: string, parent: any, ...rest: any[]) {
    if (request === "vscode") return "vscode";
    return originalResolve.call(this, request, parent, ...rest);
  };
  Module._cache["vscode"] = { id: "vscode", exports: vscodeApi, loaded: true };
}

// ---------------------------------------------------------------------------
// Extension loading
// ---------------------------------------------------------------------------

async function doActivate(pending: PendingActivation) {
  const ext = require(pending.mainPath);
  if (typeof ext.activate === "function") {
    const context = {
      subscriptions: [] as any[],
      extensionPath: pending.extPath,
      storagePath: `${pending.extPath}/.storage`,
      globalStoragePath: `${process.env.HOME}/.dan/extension-storage`,
      extensionUri: vscodeApi.Uri.file(pending.extPath),
      extensionMode: 3, // Production
    };
    await ext.activate(context);
    sendEvent("extension:activated", {
      id: pending.extensionId,
      displayName: pending.pkg.displayName ?? pending.pkg.name,
    });
  }
}

async function loadExtensions() {
  installVscodeShim();

  for (const extPath of extensionPaths) {
    try {
      const pkgPath = `${extPath}/package.json`;
      const pkg = JSON.parse(
        require("fs").readFileSync(pkgPath, "utf-8"),
      );

      const mainEntry = pkg.main;
      if (!mainEntry) continue;

      const mainPath = require("path").resolve(extPath, mainEntry);
      const extensionId = `${pkg.publisher ?? "unknown"}.${pkg.name ?? "unknown"}`;
      const activationEvents: string[] = pkg.activationEvents ?? ["*"];

      const entry: PendingActivation = {
        extensionId,
        events: activationEvents,
        mainPath,
        extPath,
        pkg,
        activated: false,
      };
      pendingActivations.push(entry);

      if (shouldActivate(activationEvents, "*")) {
        entry.activated = true;
        await doActivate(entry);
      }
    } catch (err: any) {
      sendEvent("extension:error", { path: extPath, error: err.message });
    }
  }
}

// ---------------------------------------------------------------------------
// IPC request handler
// ---------------------------------------------------------------------------

process.on("message", async (msg: any) => {
  if (msg.type === "request") {
    try {
      let result;
      switch (msg.command) {
        case "getCommands":
          result = vscodeApi.commands.getCommands();
          break;
        case "executeCommand":
          result = await vscodeApi.commands.executeCommand(
            msg.args.id,
            ...(msg.args.args ?? []),
          );
          break;
        case "activateByEvent":
          await activateByEvent(msg.args.event);
          result = true;
          break;
        default:
          result = null;
      }
      process.send?.({ type: "response", id: msg.id, result });
    } catch (err: any) {
      process.send?.({ type: "response", id: msg.id, error: err.message });
    }
  }
});

loadExtensions();
