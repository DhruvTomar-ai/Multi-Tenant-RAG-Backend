// Mirrors the backend's response schemas in app/schemas.

export interface Tokens {
  access_token: string;
  refresh_token: string;
}

export interface Me {
  id: number;
  email: string;
  username: string;
  full_name: string | null;
}

export interface Tenant {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Team {
  id: number;
  tenant_id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type RoleName = "admin" | "manager" | "member" | "viewer";

export interface Person {
  id: number;
  email: string;
  username: string;
  full_name: string | null;
  role: RoleName;
}

export interface DocumentRecord {
  id: number;
  tenant_id: number;
  team_id: number;
  uploaded_by_user_id: number | null;
  original_filename: string;
  file_extension: string;
  content_type: string;
  file_size: number;
  version: number;
  status: string;
  chunk_count: number;
  duplicate_of_document_id: number | null;
  document_type: string | null;
  department: string | null;
  category: string | null;
  source: string | null;
  access_level: string;
  tags: string[];
  custom_metadata: Record<string, unknown>;
  embedding_status: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentStatus {
  document_id: number;
  status: string;
  version: number;
  chunk_count: number;
  latest_job_status: string | null;
  embedding_status: string;
  embedding_count: number;
  embedding_attempts: number;
  duplicate_of_document_id: number | null;
}

export interface DocumentChunk {
  id: number;
  document_id: number;
  chunk_index: number;
  chunk_text: string;
}

export interface DocumentGrant {
  id: number;
  document_id: number;
  user_id: number;
  granted_by_user_id: number | null;
  created_at: string;
}

export interface UploadResult {
  document_id: number;
  job_id: number;
  status: string;
  job_status: string;
}

export interface SearchFilters {
  document_type?: string;
  department?: string;
  category?: string;
  access_level?: string;
  file_type?: string;
  tags?: string[];
}

export interface SearchResult {
  chunk_id: number;
  chunk_index: number;
  document_id: number;
  document_title: string;
  team_id: number;
  content: string;
  similarity_score: number;
  metadata: { department: string | null; document_type: string | null; category: string | null; access_level: string; file_type: string; tags: string[] };
}

export interface SearchResponse {
  results: SearchResult[];
  offset: number;
  has_more: boolean;
}

export interface Conversation {
  id: number;
  tenant_id: number;
  team_id: number | null;
  created_by_user_id: number;
  title: string;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface Citation {
  chunk_id: number;
  chunk_index: number;
  document_id: number;
  document_title: string;
  team_id: number;
  similarity_score: number;
  page_label: string | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: "pending" | "completed" | "insufficient_evidence" | "failed";
  status_reason: string | null;
  reply_to_message_id: number | null;
  citations: Citation[];
  sources: { document_id: number; document_title: string; chunk_ids: number[] }[];
  content_hidden: boolean;
  llm_model: string | null;
  created_at: string;
}

export interface ChatReply {
  conversation_id: number;
  status: string;
  replayed: boolean;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}
