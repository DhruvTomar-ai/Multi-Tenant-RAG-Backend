import type { BadgeTone } from "../components/ui";
import type { DocumentRecord, DocumentStatus } from "./types";

export const MAX_UPLOAD_MB = Number(import.meta.env.VITE_MAX_UPLOAD_MB ?? 25);
export const ALLOWED_EXTENSIONS: string[] = String(import.meta.env.VITE_ALLOWED_EXTENSIONS ?? ".pdf,.docx,.txt")
  .split(",")
  .map((extension) => extension.trim().toLowerCase());
export const ACCESS_LEVELS = ["team", "restricted", "confidential"];

export interface Progress {
  label: string;
  tone: BadgeTone;
  /** Still changing, so keep checking. */
  active: boolean;
  searchable: boolean;
}

type ProgressInput = Pick<DocumentRecord, "status" | "embedding_status" | "chunk_count" | "duplicate_of_document_id"> & Partial<Pick<DocumentStatus, "embedding_count">>;

/** The document's state in plain words. */
export function describeProgress(document: ProgressInput): Progress {
  if (document.status === "failed") return { label: "Processing failed", tone: "danger", active: false, searchable: false };
  if (document.status === "pending") return { label: "Waiting to be processed", tone: "neutral", active: true, searchable: false };
  if (document.status === "processing") return { label: "Reading the file and splitting it into chunks", tone: "info", active: true, searchable: false };
  switch (document.embedding_status) {
    case "completed":
      return { label: "Ready to search", tone: "success", active: false, searchable: true };
    case "failed":
      return { label: "Embedding failed", tone: "danger", active: false, searchable: false };
    case "not_required":
      return { label: "Duplicate content: search uses the original", tone: "neutral", active: false, searchable: false };
    case "processing":
      return {
        label: document.embedding_count !== undefined ? `Embedding ${document.embedding_count} of ${document.chunk_count} chunks` : "Embedding chunks",
        tone: "info",
        active: true,
        searchable: false,
      };
    default:
      return { label: "Waiting to create search embeddings", tone: "neutral", active: true, searchable: false };
  }
}

export function fileProblem(file: File): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!ALLOWED_EXTENSIONS.includes(extension)) return `That file type isn't supported. Upload one of: ${ALLOWED_EXTENSIONS.join(", ")}.`;
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `That file is larger than the ${MAX_UPLOAD_MB} MB limit.`;
  return null;
}
