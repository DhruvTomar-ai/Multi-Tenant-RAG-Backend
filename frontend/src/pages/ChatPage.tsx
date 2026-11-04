import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, EyeOff, FileSearch, Info, Loader2, Pencil, Plus, RefreshCw, Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { Badge, Button, EmptyState, Field, Input, Loading, LoadError, Modal, NotFoundPanel, Notice, Select, Textarea, useToast } from "../components/ui";
import { tenantKey, useAccess } from "../lib/access";
import { api, CHAT_TIMEOUT_MS } from "../lib/api";
import { FAILED_ANSWER, HIDDEN_ANSWER, insufficientReason, newClientMessageId, parseAnswer } from "../lib/chat";
import { friendlyChatMessage, friendlyMessage, NO_ACCESS } from "../lib/errors";
import type { ChatMessage, ChatReply, Citation, Conversation, DocumentChunk } from "../lib/types";

const conversationsKey = (tenantId: number) => tenantKey(tenantId, "conversations");
const MAX_QUESTION_LENGTH = 4000;

function NewConversationDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { tenantId, teams, can } = useAccess();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [teamId, setTeamId] = useState("");
  const scopes = teams.filter((team) => team.is_active && can(team.id, "document.read"));
  const create = useMutation({
    mutationFn: () => api<Conversation>(`/tenants/${tenantId}/chat/conversations`, { method: "POST", json: { title: title.trim() || null, team_id: teamId ? Number(teamId) : null } }),
    onSuccess: async (conversation) => {
      await queryClient.invalidateQueries({ queryKey: conversationsKey(tenantId) });
      setTitle("");
      setTeamId("");
      onClose();
      navigate(`/t/${tenantId}/chat/${conversation.id}`);
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="New conversation">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <Field label="Title (optional)" hint="Leave empty to use your first question.">
          <Input maxLength={255} value={title} onChange={(event) => setTitle(event.target.value)} />
        </Field>
        <Field label="Search in" hint="A team conversation only uses that team's documents. This can't be changed later.">
          <Select aria-label="Team scope" value={teamId} onChange={(event) => setTeamId(event.target.value)}>
            <option value="">All documents I can read</option>
            {scopes.map((team) => (
              <option key={team.id} value={team.id}>
                {team.name} only
              </option>
            ))}
          </Select>
        </Field>
        {create.isError && <Notice tone="error">{friendlyMessage(create.error)}</Notice>}
        <Button type="submit" busy={create.isPending}>
          Start conversation
        </Button>
      </form>
    </Modal>
  );
}

function ConversationList() {
  const { tenantId } = useAccess();
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const status = showArchived ? "archived" : "active";
  const conversations = useQuery({
    queryKey: [...conversationsKey(tenantId), status],
    queryFn: () => api<Conversation[]>(`/tenants/${tenantId}/chat/conversations`, { query: { status, limit: 200 } }),
  });
  return (
    <aside className="flex w-full shrink-0 flex-col gap-3 lg:w-64">
      <Button onClick={() => setCreating(true)}>
        <Plus className="size-4" aria-hidden />
        New conversation
      </Button>
      <div className="flex rounded-md bg-slate-100 p-1 text-xs font-medium" role="group" aria-label="Which conversations to show">
        {[false, true].map((archived) => (
          <button key={String(archived)} type="button" aria-pressed={showArchived === archived} onClick={() => setShowArchived(archived)} className={`flex-1 rounded px-2 py-1 ${showArchived === archived ? "bg-white shadow-sm" : "text-slate-600"}`}>
            {archived ? "Archived" : "Active"}
          </button>
        ))}
      </div>
      {conversations.isPending ? (
        <Loading />
      ) : conversations.isError ? (
        <LoadError error={conversations.error} retry={() => void conversations.refetch()} />
      ) : conversations.data.length === 0 ? (
        <p className="px-1 text-sm text-slate-600">{showArchived ? "No archived conversations." : "No conversations yet. Start one."}</p>
      ) : (
        <ul className="max-h-[60vh] space-y-1 overflow-y-auto" aria-label="Conversations">
          {conversations.data.map((conversation) => (
            <li key={conversation.id}>
              <NavLink to={`/t/${tenantId}/chat/${conversation.id}`} className={({ isActive }) => `block truncate rounded-md px-3 py-2 text-sm ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-700 hover:bg-slate-100"}`}>
                {conversation.title}
              </NavLink>
            </li>
          ))}
        </ul>
      )}
      <NewConversationDialog open={creating} onClose={() => setCreating(false)} />
    </aside>
  );
}

/** Fetches just the cited chunk when it's opened, so the backend rechecks access at that moment. */
function CitationDialog({ citation, onClose }: { citation: Citation | null; onClose: () => void }) {
  const { tenantId } = useAccess();
  const chunk = useQuery({
    queryKey: tenantKey(tenantId, "citation", citation?.document_id, citation?.chunk_id),
    queryFn: () => api<DocumentChunk[]>(`/tenants/${tenantId}/teams/${citation!.team_id}/documents/${citation!.document_id}/chunks`, { query: { offset: citation!.chunk_index, limit: 1 } }),
    enabled: citation !== null,
    staleTime: 0,
    gcTime: 0,
  });
  const text = chunk.data?.find((item) => item.id === citation?.chunk_id)?.chunk_text;
  return (
    <Modal open={citation !== null} onClose={onClose} title={citation?.document_title ?? "Source"}>
      {citation && (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-1.5">
            <Badge tone="info">Match {citation.similarity_score.toFixed(2)}</Badge>
            <Badge>Passage {citation.chunk_index + 1}</Badge>
            {citation.page_label && <Badge>Page {citation.page_label}</Badge>}
          </div>
          {chunk.isPending ? (
            <Loading label="Loading the source…" />
          ) : chunk.isError ? (
            <LoadError error={chunk.error} retry={() => void chunk.refetch()} />
          ) : text === undefined ? (
            <Notice tone="warning">{NO_ACCESS} The document may have changed since this answer was written.</Notice>
          ) : (
            <blockquote className="whitespace-pre-wrap break-words rounded-md bg-slate-50 p-3" data-testid="citation-text">
              {text}
            </blockquote>
          )}
          <Link className="inline-block font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/teams/${citation.team_id}/documents/${citation.document_id}`}>
            Open the document
          </Link>
        </div>
      )}
    </Modal>
  );
}

function AssistantMessage({ message, onCitation }: { message: ChatMessage; onCitation: (citation: Citation) => void }) {
  if (message.content_hidden) {
    return (
      <div className="flex items-start gap-2 text-sm text-slate-600" data-testid="hidden-answer">
        <EyeOff className="mt-0.5 size-4 shrink-0" aria-hidden />
        {HIDDEN_ANSWER}
      </div>
    );
  }
  if (message.status === "pending") {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        Still working on this answer…
      </div>
    );
  }
  if (message.status === "failed") {
    return <div className="text-sm text-slate-600">{FAILED_ANSWER}</div>;
  }
  if (message.status === "insufficient_evidence") {
    const reason = insufficientReason(message.status_reason);
    return (
      <div className="text-sm" data-testid="insufficient-evidence">
        <div className="flex items-start gap-2 font-medium text-amber-800">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
          {reason.title}
        </div>
        <p className="ml-6 mt-1 text-slate-600">{reason.detail}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {/* Untrusted model output. Each part is a text node or a button, never HTML. */}
      <p className="whitespace-pre-wrap break-words text-sm" data-testid="answer">
        {parseAnswer(message.content, message.citations).map((part, index) =>
          part.kind === "text" ? (
            <span key={index}>{part.text}</span>
          ) : (
            <button key={index} type="button" aria-label={`Source ${part.number}: ${part.citation.document_title}`} onClick={() => onCitation(part.citation)} className="mx-0.5 inline-flex size-5 items-center justify-center rounded-full bg-brand-100 align-text-top text-xs font-semibold text-brand-700 hover:bg-brand-600 hover:text-white">
              {part.number}
            </button>
          ),
        )}
      </p>
      {message.citations.length > 0 && (
        <div>
          <h3 className="mb-1 text-xs font-medium text-slate-500">Sources</h3>
          <ul className="flex flex-wrap gap-1.5">
            {message.citations.map((citation, index) => (
              <li key={citation.chunk_id}>
                <button type="button" onClick={() => onCitation(citation)} className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs hover:border-brand-600">
                  <FileSearch className="size-3.5 text-slate-500" aria-hidden />
                  {index + 1}. {citation.document_title}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ role, children }: { role: "user" | "assistant"; children: React.ReactNode }) {
  return role === "user" ? (
    <li className="flex justify-end" data-testid="user-message">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-brand-600 px-4 py-2 text-sm text-white">{children}</div>
    </li>
  ) : (
    <li data-testid="assistant-message">
      <div className="max-w-[85%] rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">{children}</div>
    </li>
  );
}

interface Outgoing {
  content: string;
  /** Kept when a send fails, so the retry can't create a second question. */
  clientMessageId: string;
}

function RenameDialog({ conversation, open, onClose }: { conversation: Conversation; open: boolean; onClose: () => void }) {
  const { tenantId } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState(conversation.title);
  const rename = useMutation({
    mutationFn: () => api<Conversation>(`/tenants/${tenantId}/chat/conversations/${conversation.id}`, { method: "PATCH", json: { title: title.trim() } }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: conversationsKey(tenantId) });
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "conversation", conversation.id) });
      toast("Conversation renamed.");
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="Rename conversation">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          rename.mutate();
        }}
      >
        <Field label="Title">
          <Input required maxLength={255} value={title} onChange={(event) => setTitle(event.target.value)} />
        </Field>
        {rename.isError && <Notice tone="error">{friendlyMessage(rename.error)}</Notice>}
        <Button type="submit" busy={rename.isPending}>
          Save
        </Button>
      </form>
    </Modal>
  );
}

function ConversationView({ conversationId }: { conversationId: number }) {
  const { tenantId, teamName } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [sending, setSending] = useState<Outgoing | null>(null);
  const [failed, setFailed] = useState<{ outgoing: Outgoing; error: unknown } | null>(null);
  // A state update waits for the next render, so a ref is what stops a double-click sending twice.
  const inFlight = useRef(false);
  const endRef = useRef<HTMLLIElement>(null);

  const path = `/tenants/${tenantId}/chat/conversations/${conversationId}`;
  const messagesKey = tenantKey(tenantId, "messages", conversationId);
  const conversation = useQuery({ queryKey: tenantKey(tenantId, "conversation", conversationId), queryFn: () => api<Conversation>(path) });
  const messages = useQuery({
    queryKey: messagesKey,
    queryFn: () => api<ChatMessage[]>(`${path}/messages`, { query: { limit: 500 } }),
    // An answer started elsewhere, or before a reload, may still finish.
    refetchInterval: (query) => (query.state.data?.some((message) => message.status === "pending") && !inFlight.current ? 3000 : false),
  });

  const count = messages.data?.length ?? 0;
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [count, sending]);

  const setArchived = useMutation({
    mutationFn: (archived: boolean) => api<Conversation>(path, { method: "PATCH", json: { status: archived ? "archived" : "active" } }),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: conversationsKey(tenantId) });
      queryClient.setQueryData(tenantKey(tenantId, "conversation", conversationId), updated);
      toast(updated.status === "archived" ? "Conversation archived." : "Conversation restored.");
    },
  });

  const send = async (outgoing: Outgoing) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(outgoing);
    setFailed(null);
    setDraft((current) => (current.trim() === outgoing.content ? "" : current));
    try {
      await api<ChatReply>(`${path}/messages`, { method: "POST", json: { content: outgoing.content, client_message_id: outgoing.clientMessageId }, timeoutMs: CHAT_TIMEOUT_MS });
    } catch (error) {
      setFailed({ outgoing, error });
    } finally {
      inFlight.current = false;
      // The saved history is the truth, including a failed answer's row.
      await Promise.allSettled([queryClient.invalidateQueries({ queryKey: messagesKey }), queryClient.invalidateQueries({ queryKey: conversationsKey(tenantId) }), queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "conversation", conversationId) })]);
      setSending(null);
    }
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const content = draft.trim();
    if (!content) return;
    void send({ content, clientMessageId: newClientMessageId() });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) submit(event);
  };

  if (conversation.isPending || messages.isPending) return <Loading label="Loading conversation…" />;
  if (conversation.isError) return <LoadError error={conversation.error} retry={() => void conversation.refetch()} backTo={`/t/${tenantId}/chat`} />;
  if (messages.isError) return <LoadError error={messages.error} retry={() => void messages.refetch()} backTo={`/t/${tenantId}/chat`} />;

  const item = conversation.data;
  const archived = item.status === "archived";
  const history = messages.data;
  const isBusy = sending !== null;
  const lastUser = [...history].reverse().find((message) => message.role === "user");
  // After a network failure the question may not have been saved, so it is shown from here instead.
  const unsavedQuestion = failed && lastUser?.content !== failed.outgoing.content ? failed.outgoing.content : null;
  const sendingNew = sending && lastUser?.content !== sending.content;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-3">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold">{item.title}</h1>
          <div className="mt-0.5 flex flex-wrap gap-1.5">
            <Badge tone={item.team_id ? "info" : "neutral"}>{item.team_id ? `${teamName(item.team_id)} documents only` : "All documents you can read"}</Badge>
            {archived && <Badge tone="warning">Archived</Badge>}
          </div>
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" onClick={() => setRenaming(true)}>
            <Pencil className="size-4" aria-hidden />
            Rename
          </Button>
          <Button variant="ghost" busy={setArchived.isPending} onClick={() => setArchived.mutate(!archived)}>
            {archived ? <ArchiveRestore className="size-4" aria-hidden /> : <Archive className="size-4" aria-hidden />}
            {archived ? "Restore" : "Archive"}
          </Button>
        </div>
      </div>
      {setArchived.isError && <Notice tone="error">{friendlyMessage(setArchived.error)}</Notice>}

      {history.length === 0 && !sending && !failed ? (
        <EmptyState>Ask a question about your documents. Answers cite the passages they came from.</EmptyState>
      ) : (
        <ul className="space-y-3" aria-label="Messages">
          {history.map((message) => (
            <MessageBubble key={message.id} role={message.role}>
              {message.role === "user" ? message.content : <AssistantMessage message={message} onCitation={setCitation} />}
            </MessageBubble>
          ))}
          {unsavedQuestion && <MessageBubble role="user">{unsavedQuestion}</MessageBubble>}
          {sending && (
            <>
              {sendingNew && <MessageBubble role="user">{sending.content}</MessageBubble>}
              <MessageBubble role="assistant">
                <div role="status" className="flex items-center gap-2 text-sm text-slate-600">
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Searching your documents and writing an answer…
                </div>
              </MessageBubble>
            </>
          )}
          <li ref={endRef} aria-hidden />
        </ul>
      )}

      {failed && !sending && (
        <Notice tone="error">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{friendlyChatMessage(failed.error)}</span>
            <Button variant="secondary" onClick={() => void send(failed.outgoing)}>
              <RefreshCw className="size-4" aria-hidden />
              Try again
            </Button>
          </div>
        </Notice>
      )}

      {archived ? (
        <Notice tone="info">This conversation is archived, so you can't send new messages. Restore it to continue.</Notice>
      ) : (
        <form onSubmit={submit} className="flex items-end gap-2">
          <Textarea aria-label="Your question" rows={2} maxLength={MAX_QUESTION_LENGTH} placeholder="Ask about your documents…" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} />
          <Button type="submit" busy={isBusy} disabled={!draft.trim()}>
            <Send className="size-4" aria-hidden />
            Send
          </Button>
        </form>
      )}
      <RenameDialog key={item.title} conversation={item} open={renaming} onClose={() => setRenaming(false)} />
      <CitationDialog citation={citation} onClose={() => setCitation(null)} />
    </div>
  );
}

export function ChatPage() {
  const { conversationId: raw } = useParams();
  const { tenantId } = useAccess();
  const conversationId = raw === undefined ? null : Number(raw);
  const valid = conversationId === null || (Number.isInteger(conversationId) && conversationId > 0 && conversationId <= 2_147_483_647);
  return (
    <div className="flex flex-col gap-5 lg:flex-row">
      <ConversationList />
      {!valid ? (
        <NotFoundPanel backTo={`/t/${tenantId}/chat`} backLabel="Go to chat" />
      ) : conversationId === null ? (
        <div className="flex-1">
          <EmptyState>Choose a conversation, or start a new one.</EmptyState>
        </div>
      ) : (
        <ConversationView key={conversationId} conversationId={conversationId} />
      )}
    </div>
  );
}
