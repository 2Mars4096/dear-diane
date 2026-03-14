/**
 * Default layout configs per mode. User overrides are layered on top via layoutPersistence.
 */
export const DEFAULT_LAYOUTS: Record<string, { description: string }> = {
  chat: {
    description:
      "Full-width conversation with optional sidebar and context panel",
  },
  development: {
    description:
      "VS Code-style: sidebar, editor, terminal, chat sidebar",
  },
  operations: {
    description: "Workflow canvas with toolbar and log panel",
  },
  research: {
    description: "Writing pane, PDF reader, references sidebar",
  },
};
