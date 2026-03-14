import { iconThemeManager } from "../../lib/extensions/iconThemeLoader";
import { File, Folder, FolderOpen } from "lucide-react";

interface FileIconProps {
  name: string;
  isDirectory: boolean;
  isExpanded?: boolean;
  size?: number;
  className?: string;
}

const EXT_COLORS: Record<string, string> = {
  ts: "text-blue-400", tsx: "text-blue-400",
  js: "text-yellow-400", jsx: "text-yellow-400",
  py: "text-green-400", rs: "text-orange-400",
  go: "text-cyan-400", json: "text-yellow-300",
  md: "text-gray-400", css: "text-purple-400",
  html: "text-orange-300", svg: "text-green-300",
  yml: "text-red-300", yaml: "text-red-300",
  toml: "text-gray-300", sh: "text-green-300",
  sql: "text-blue-300", graphql: "text-pink-400",
};

export function FileIcon({ name, isDirectory, isExpanded, size = 16, className }: FileIconProps) {
  if (iconThemeManager.hasTheme) {
    const icon = isDirectory
      ? iconThemeManager.getFolderIcon(name, isExpanded ?? false)
      : iconThemeManager.getFileIcon(name);

    if (icon) {
      return (
        <img
          src={icon.src}
          alt={icon.alt}
          width={size}
          height={size}
          className={className}
          style={{ minWidth: size }}
        />
      );
    }
  }

  if (isDirectory) {
    const Icon = isExpanded ? FolderOpen : Folder;
    return <Icon size={size} className={`${className ?? ""} text-yellow-500`} />;
  }

  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return <File size={size} className={`${className ?? ""} ${EXT_COLORS[ext] ?? "text-gray-500"}`} />;
}
