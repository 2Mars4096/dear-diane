import { isElectron, nativeFs } from "./electronBridge";

export interface ComposerAttachmentDraft {
  id: string;
  kind: "file" | "figure";
  name: string;
  path?: string;
  size?: number;
  mimeType?: string;
  caption?: string;
  source?: string;
  dataUrl?: string;
  file?: File;
}

export interface ChatAttachmentRecord {
  filename: string;
  path?: string;
  size?: number;
  mimeType?: string;
  kind?: ComposerAttachmentDraft["kind"];
  caption?: string;
  source?: string;
}

function mimeTypeFromDataUrl(dataUrl?: string): string | undefined {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:")) return undefined;
  return /^data:([^;,]+)/.exec(dataUrl)?.[1] || undefined;
}

function defaultExtensionForMimeType(mimeType?: string): string {
  switch ((mimeType || "").toLowerCase()) {
    case "image/png":
      return ".png";
    case "image/jpeg":
      return ".jpg";
    case "image/webp":
      return ".webp";
    case "image/gif":
      return ".gif";
    case "image/svg+xml":
      return ".svg";
    case "application/pdf":
      return ".pdf";
    case "application/json":
      return ".json";
    case "text/plain":
      return ".txt";
    default:
      return "";
  }
}

export function resolveAttachmentName(name?: string, mimeType?: string): string {
  const trimmed = name?.trim();
  if (trimmed) return trimmed;
  const extension = defaultExtensionForMimeType(mimeType);
  if (mimeType?.startsWith("image/")) return `Pasted Image${extension}`;
  if (mimeType === "application/pdf") return `Pasted PDF${extension}`;
  return `Attachment${extension}`;
}

export function composerDraftToChatAttachment(
  attachment: ComposerAttachmentDraft,
): ChatAttachmentRecord {
  return {
    filename: resolveAttachmentName(attachment.name, attachment.mimeType),
    path: attachment.path,
    size: attachment.size,
    mimeType: attachment.mimeType,
    kind: attachment.kind,
    caption: attachment.caption,
    source: attachment.source,
  };
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read attachment"));
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }
      reject(new Error("Unexpected attachment read result"));
    };
    reader.readAsDataURL(file);
  });
}

async function persistAttachmentPath(
  attachment: ComposerAttachmentDraft,
): Promise<ComposerAttachmentDraft> {
  if (attachment.path || !isElectron()) return attachment;
  let dataUrl = attachment.dataUrl;
  if (!dataUrl && attachment.file) {
    try {
      dataUrl = await fileToDataUrl(attachment.file);
    } catch (error) {
      console.warn("Failed to read attachment for temp persistence:", error);
      return attachment;
    }
  }
  if (!dataUrl) return attachment;
  try {
    const persistedPath = await nativeFs.writeTempAttachment({
      name: attachment.name,
      mimeType: attachment.mimeType ?? mimeTypeFromDataUrl(dataUrl),
      dataUrl,
    });
    if (!persistedPath) return attachment;
    return {
      ...attachment,
      path: persistedPath,
      dataUrl,
      mimeType: attachment.mimeType ?? mimeTypeFromDataUrl(dataUrl),
    };
  } catch (error) {
    console.warn("Failed to persist attachment to local cache:", error);
    return attachment;
  }
}

export async function normalizeAttachmentDrafts(
  attachments: ComposerAttachmentDraft[],
): Promise<ComposerAttachmentDraft[]> {
  return Promise.all(attachments.map((attachment) => persistAttachmentPath(attachment)));
}
