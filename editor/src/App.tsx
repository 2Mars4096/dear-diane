import AppShell from "./components/shell/AppShell";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";

export default function App() {
  useKeyboardShortcuts();
  return <AppShell />;
}
