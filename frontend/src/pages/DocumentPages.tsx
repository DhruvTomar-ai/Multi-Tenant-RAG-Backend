import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, ConfirmDialog, DisabledAction, EmptyState, Field, formatBytes, formatDate, Input, Loading, LoadError, NotFoundPanel, Notice, PageTitle, Select, Textarea, useToast } from "../components/ui";
import { tenantKey, useAccess } from "../lib/access";
import { api, apiBlob, UPLOAD_TIMEOUT_MS } from "../lib/api";
import { ALLOWED_EXTENSIONS, ACCESS_LEVELS, describeProgress, fileProblem, MAX_UPLOAD_MB } from "../lib/documents";
import { ApiError, friendlyMessage } from "../lib/errors";
import type { DocumentChunk, DocumentGrant, DocumentRecord, DocumentStatus, UploadResult } from "../lib/types";

const PAGE_SIZE = 25;

const documentPath = (tenantId: number, teamId: number, documentId: number) => `/tenants/${tenantId}/teams/${teamId}/documents/${documentId}`;

export function DocumentsPage() {
  const { tenantId, teams, teamName, can } = useAccess();
  const [params, setParams] = useSearchParams();
  const [pendingDepartment, setPendingDepartment] = useState(params.get("department") ?? "");
  const team = params.get("team") ?? "";
  const status = params.get("status") ?? "";
  const department = params.get("department") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;

  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    next.delete("offset");
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  };

  const documents = useQuery({
    queryKey: tenantKey(tenantId, "documents", { team, status, department, offset }),
    // One extra row shows whether there is a next page.
    queryFn: () => api<DocumentRecord[]>(team ? `/tenants/${tenantId}/teams/${team}/documents` : `/tenants/${tenantId}/documents`, { query: { status, department, limit: PAGE_SIZE + 1, offset } }),
    refetchInterval: (query) => (query.state.data?.some((document) => describeProgress(document).active) ? 3000 : false),
  });

  const uploadableTeams = teams.filter((item) => item.is_active && can(item.id, "document.upload"));
  const rows = documents.data?.slice(0, PAGE_SIZE) ?? [];
  const filtered = Boolean(team || status || department);
  const uploadLink = (
    <Link to={`/t/${tenantId}/documents/upload`} className="inline-flex items-center gap-2 rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700">
      <Upload className="size-4" aria-hidden />
      Upload document
    </Link>
  );

  return (
    <div className="space-y-5">
      <PageTitle description="Documents you can read in this tenant." actions={uploadableTeams.length > 0 ? uploadLink : <DisabledAction label="Upload document" reason={teams.length > 0 ? "Viewers can read documents but can't upload them." : "Join a team to upload documents."} />}>
        Documents
      </PageTitle>
      <form
        className="grid gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter("department", pendingDepartment.trim());
        }}
      >
        <Field label="Team">
          <Select value={team} onChange={(event) => setFilter("team", event.target.value)}>
            <option value="">All teams</option>
            {teams.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Processing status">
          <Select value={status} onChange={(event) => setFilter("status", event.target.value)}>
            <option value="">Any status</option>
            <option value="pending">Waiting</option>
            <option value="processing">Processing</option>
            <option value="completed">Processed</option>
            <option value="failed">Failed</option>
          </Select>
        </Field>
        <Field label="Department" hint="Press Enter to apply.">
          <Input value={pendingDepartment} onChange={(event) => setPendingDepartment(event.target.value)} onBlur={() => setFilter("department", pendingDepartment.trim())} />
        </Field>
      </form>

      {documents.isPending ? (
        <Loading label="Loading documents…" />
      ) : documents.isError ? (
        <LoadError error={documents.error} retry={() => void documents.refetch()} backTo={`/t/${tenantId}/documents`} />
      ) : rows.length === 0 ? (
        filtered ? (
          <EmptyState>No documents match these filters.</EmptyState>
        ) : (
          <EmptyState action={uploadableTeams.length > 0 ? uploadLink : undefined}>{uploadableTeams.length > 0 ? "No documents yet. Upload one." : "No documents yet."}</EmptyState>
        )
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2.5 font-medium">Document</th>
                <th className="px-4 py-2.5 font-medium">Team</th>
                <th className="px-4 py-2.5 font-medium">Department</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Uploaded</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((document) => {
                const progress = describeProgress(document);
                return (
                  <tr key={document.id}>
                    <td className="px-4 py-3">
                      <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/teams/${document.team_id}/documents/${document.id}`}>
                        {document.original_filename}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{teamName(document.team_id)}</td>
                    <td className="px-4 py-3 text-slate-600">{document.department ?? "—"}</td>
                    <td className="px-4 py-3">
                      <Badge tone={progress.tone}>{progress.label}</Badge>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-600">{formatDate(document.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(offset > 0 || (documents.data?.length ?? 0) > PAGE_SIZE) && (
        <div className="flex justify-end gap-2">
          <Button variant="secondary" disabled={offset === 0} onClick={() => setFilter("offset", String(Math.max(0, offset - PAGE_SIZE)))}>
            Previous
          </Button>
          <Button variant="secondary" disabled={(documents.data?.length ?? 0) <= PAGE_SIZE} onClick={() => setFilter("offset", String(offset + PAGE_SIZE))}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

export function UploadPage() {
  const { tenantId, teams, can } = useAccess();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const allowed = teams.filter((team) => team.is_active && can(team.id, "document.upload"));
  const [teamId, setTeamId] = useState(String(allowed[0]?.id ?? ""));
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fields, setFields] = useState({ document_type: "", department: "", category: "", source: "", access_level: "", tags: "", custom_metadata: "" });
  const [customError, setCustomError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      for (const [name, value] of Object.entries(fields)) {
        if (value.trim()) form.append(name, value.trim());
      }
      return api<UploadResult>(`/tenants/${tenantId}/teams/${teamId}/documents/upload`, { method: "POST", form, timeoutMs: UPLOAD_TIMEOUT_MS });
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "documents") });
      toast("Upload received. Processing has started.");
      navigate(`/t/${tenantId}/teams/${teamId}/documents/${result.document_id}`);
    },
  });

  if (allowed.length === 0) {
    return (
      <div className="space-y-4">
        <PageTitle>Upload a document</PageTitle>
        <Notice tone="info">You can't upload here. Only team members with upload access can add documents. Ask a team manager if you need it.</Notice>
      </div>
    );
  }

  const set = (name: keyof typeof fields) => (event: { target: { value: string } }) => setFields((current) => ({ ...current, [name]: event.target.value }));
  const duplicate = upload.error instanceof ApiError && upload.error.status === 409 ? (upload.error.detail as { document_id?: number }) : null;

  return (
    <div className="max-w-2xl space-y-5">
      <PageTitle description={`Allowed: ${ALLOWED_EXTENSIONS.join(", ")}. Up to ${MAX_UPLOAD_MB} MB.`}>Upload a document</PageTitle>
      <Card>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!file || upload.isPending) return;
            if (fields.custom_metadata.trim()) {
              try {
                const parsed: unknown = JSON.parse(fields.custom_metadata);
                if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
              } catch {
                setCustomError('Enter this as JSON, for example {"region": "EU"}.');
                return;
              }
            }
            setCustomError(null);
            upload.mutate();
          }}
        >
          <Field label="Team">
            <Select aria-label="Team" value={teamId} onChange={(event) => setTeamId(event.target.value)}>
              {allowed.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="File">
            <Input
              type="file"
              required
              accept={ALLOWED_EXTENSIONS.join(",")}
              onChange={(event) => {
                const chosen = event.target.files?.[0] ?? null;
                const problem = chosen ? fileProblem(chosen) : null;
                setFileError(problem);
                setFile(problem ? null : chosen);
                upload.reset();
              }}
            />
          </Field>
          {fileError && <Notice tone="error">{fileError}</Notice>}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Document type (optional)">
              <Input maxLength={100} value={fields.document_type} onChange={set("document_type")} placeholder="Policy" />
            </Field>
            <Field label="Department (optional)">
              <Input maxLength={100} value={fields.department} onChange={set("department")} placeholder="HR" />
            </Field>
            <Field label="Category (optional)">
              <Input maxLength={100} value={fields.category} onChange={set("category")} />
            </Field>
            <Field label="Source (optional)">
              <Input maxLength={255} value={fields.source} onChange={set("source")} />
            </Field>
            <Field label="Access label (optional)" hint="A label for filtering. It doesn't change who can read the document.">
              <Select aria-label="Access label" value={fields.access_level} onChange={set("access_level")}>
                <option value="">Default (team)</option>
                {ACCESS_LEVELS.map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Tags (optional)" hint="Separate with commas.">
              <Input value={fields.tags} onChange={set("tags")} />
            </Field>
          </div>
          <Field label="Custom details (optional)" hint='Advanced. JSON, for example {"region": "EU"}.'>
            <Textarea rows={2} value={fields.custom_metadata} onChange={set("custom_metadata")} />
          </Field>
          {customError && <Notice tone="error">{customError}</Notice>}
          {upload.isError && (
            <Notice tone="error">
              {duplicate?.document_id ? (
                <>
                  This team already has this file.{" "}
                  <Link className="font-medium underline" to={`/t/${tenantId}/teams/${teamId}/documents/${duplicate.document_id}`}>
                    Open the existing document
                  </Link>
                </>
              ) : (
                friendlyMessage(upload.error)
              )}
            </Notice>
          )}
          <Button type="submit" busy={upload.isPending} disabled={!file}>
            {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
        </form>
      </Card>
    </div>
  );
}

function ProcessingCard({ document, status, teamId }: { document: DocumentRecord; status: DocumentStatus | undefined; teamId: number }) {
  const { tenantId, can } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const progress = describeProgress(status ? { ...document, ...status } : document);
  const canRetry = can(teamId, "document.upload");
  const retry = useMutation({
    mutationFn: () => api(`${documentPath(tenantId, teamId, document.id)}/embeddings/retry`, { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "document-status", document.id) });
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "documents") });
      toast("Embedding started again.");
    },
  });
  const embeddingFailed = (status?.embedding_status ?? document.embedding_status) === "failed";
  const processingFailed = (status?.status ?? document.status) === "failed";
  return (
    <Card title="Processing">
      <div className="flex flex-wrap items-center gap-3" data-testid="processing-status">
        <Badge tone={progress.tone}>{progress.label}</Badge>
        {progress.active && <span className="text-xs text-slate-500">Checking for updates…</span>}
        {progress.searchable && status && (
          <span className="text-xs text-slate-500">
            {status.chunk_count} {status.chunk_count === 1 ? "chunk" : "chunks"} indexed
          </span>
        )}
      </div>
      {progress.active && (
        <p className="mt-3 text-sm text-slate-600">This document isn't searchable yet. It won't appear in search or chat until embedding finishes.</p>
      )}
      {processingFailed && <p className="mt-3 text-sm text-slate-600">We couldn't read this file, so it isn't searchable. Check the file and upload it again.</p>}
      {embeddingFailed && (
        <div className="mt-3 space-y-2 text-sm text-slate-600">
          <p>The text was read, but it couldn't be prepared for search, so it isn't searchable yet.</p>
          {canRetry ? (
            <Button variant="secondary" busy={retry.isPending} onClick={() => retry.mutate()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry embedding
            </Button>
          ) : (
            <DisabledAction label="Retry embedding" reason="Only members who can upload to this team can retry." />
          )}
          {retry.isError && <Notice tone="error">{friendlyMessage(retry.error)}</Notice>}
        </div>
      )}
      {document.duplicate_of_document_id && (
        <p className="mt-3 text-sm text-slate-600">
          Its text matches{" "}
          <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/teams/${teamId}/documents/${document.duplicate_of_document_id}`}>
            another document in this team
          </Link>
          , which is the one used in search.
        </p>
      )}
    </Card>
  );
}

function GrantsCard({ document, teamId }: { document: DocumentRecord; teamId: number }) {
  const { tenantId, people } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState("");
  const [typedId, setTypedId] = useState("");
  const [revoking, setRevoking] = useState<DocumentGrant | null>(null);
  const path = `${documentPath(tenantId, teamId, document.id)}/grants`;
  const key = tenantKey(tenantId, "grants", document.id);
  const grants = useQuery({ queryKey: key, queryFn: () => api<DocumentGrant[]>(path) });
  const userId = Number(typedId || selected);
  const nameOf = (id: number) => {
    const person = people.find((item) => item.id === id);
    return person ? `${person.full_name || person.username} (${person.email})` : `User ID ${id}`;
  };
  const give = useMutation({
    mutationFn: () => api<DocumentGrant>(path, { method: "POST", json: { user_id: userId } }),
    onSuccess: async (grant) => {
      await queryClient.invalidateQueries({ queryKey: key });
      toast(`Read access given to ${nameOf(grant.user_id)}.`);
      setSelected("");
      setTypedId("");
    },
  });
  const revoke = useMutation({
    mutationFn: (grant: DocumentGrant) => api(`${path}/${grant.user_id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: key });
      toast("Access revoked.");
      setRevoking(null);
    },
  });
  const candidates = people.filter((person) => !grants.data?.some((grant) => grant.user_id === person.id));

  return (
    <Card title="Shared with">
      <p className="mb-3 text-sm text-slate-600">Give one person in this tenant read access to this document only. They don't get access to the rest of the team.</p>
      <form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          give.mutate();
        }}
      >
        <div className="min-w-56 flex-1">
          <Field label="Person">
            <Select aria-label="Person to share with" value={selected} disabled={Boolean(typedId)} onChange={(event) => setSelected(event.target.value)}>
              <option value="">Choose someone…</option>
              {candidates.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.full_name || person.username} ({person.email})
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="w-28">
          <Field label="Or user ID">
            <Input inputMode="numeric" pattern="[0-9]*" value={typedId} onChange={(event) => setTypedId(event.target.value)} />
          </Field>
        </div>
        <Button type="submit" busy={give.isPending} disabled={!userId}>
          Give access
        </Button>
      </form>
      {give.isError && (
        <div className="mb-3">
          <Notice tone="error">{friendlyMessage(give.error)}</Notice>
        </div>
      )}
      {grants.isPending ? (
        <Loading />
      ) : grants.isError ? (
        <LoadError error={grants.error} retry={() => void grants.refetch()} />
      ) : grants.data.length === 0 ? (
        <EmptyState>Not shared with anyone outside the team.</EmptyState>
      ) : (
        <ul className="divide-y divide-slate-100">
          {grants.data.map((grant) => (
            <li key={grant.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
              <span>{nameOf(grant.user_id)}</span>
              <Button variant="danger" onClick={() => setRevoking(grant)}>
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={revoking !== null}
        title="Revoke access?"
        body={revoking ? `${nameOf(revoking.user_id)} will no longer be able to read this document, and answers based on it will be hidden from them.` : ""}
        confirmLabel="Revoke access"
        busy={revoke.isPending}
        error={revoke.error}
        onConfirm={() => revoking && revoke.mutate(revoking)}
        onClose={() => {
          setRevoking(null);
          revoke.reset();
        }}
      />
    </Card>
  );
}

function ChunksCard({ document, teamId }: { document: DocumentRecord; teamId: number }) {
  const { tenantId } = useAccess();
  const [show, setShow] = useState(false);
  const chunks = useQuery({
    queryKey: tenantKey(tenantId, "chunks", document.id, document.version),
    queryFn: () => api<DocumentChunk[]>(`${documentPath(tenantId, teamId, document.id)}/chunks`, { query: { limit: 100 } }),
    enabled: show,
  });
  return (
    <Card title="Text" actions={<Button variant="secondary" onClick={() => setShow((value) => !value)}>{show ? "Hide text" : "Show text"}</Button>}>
      {!show ? (
        <p className="text-sm text-slate-600">The text extracted from this document, split into the chunks used for search.</p>
      ) : chunks.isPending ? (
        <Loading />
      ) : chunks.isError ? (
        <LoadError error={chunks.error} retry={() => void chunks.refetch()} />
      ) : chunks.data.length === 0 ? (
        <EmptyState>There's no extracted text yet.</EmptyState>
      ) : (
        <ol className="space-y-3">
          {chunks.data.map((chunk) => (
            <li key={chunk.id} className="rounded-md bg-slate-50 p-3 text-sm">
              <div className="mb-1 text-xs font-medium text-slate-500">Chunk {chunk.chunk_index + 1}</div>
              {/* Untrusted file text, always rendered as plain text. */}
              <p className="whitespace-pre-wrap break-words">{chunk.chunk_text}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{children}</dd>
    </div>
  );
}

export function DocumentDetailPage() {
  const { teamId: rawTeam, documentId: rawDocument } = useParams();
  const { tenantId, can, teamName } = useAccess();
  const teamId = Number(rawTeam);
  const documentId = Number(rawDocument);
  const valid = [teamId, documentId].every((value) => Number.isInteger(value) && value > 0 && value <= 2_147_483_647);
  const backTo = `/t/${tenantId}/documents`;
  const [downloadError, setDownloadError] = useState<unknown>(null);

  const documentQuery = useQuery({ queryKey: tenantKey(tenantId, "document", documentId, teamId), queryFn: () => api<DocumentRecord>(documentPath(tenantId, teamId, documentId)), enabled: valid });
  const statusQuery = useQuery({
    queryKey: tenantKey(tenantId, "document-status", documentId),
    queryFn: () => api<DocumentStatus>(`${documentPath(tenantId, teamId, documentId)}/status`),
    enabled: valid && documentQuery.isSuccess,
    refetchInterval: (query) => {
      const current = query.state.data;
      if (query.state.error) return false;
      return !current || describeProgress(current).active ? 2000 : false;
    },
  });

  if (!valid) return <NotFoundPanel backTo={backTo} backLabel="Go to documents" />;
  if (documentQuery.isPending) return <Loading label="Loading document…" />;
  if (documentQuery.isError) return <LoadError error={documentQuery.error} retry={() => void documentQuery.refetch()} backTo={backTo} />;

  const document = documentQuery.data;

  const download = async () => {
    setDownloadError(null);
    try {
      const blob = await apiBlob(`${documentPath(tenantId, teamId, documentId)}/download`);
      const url = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = document.original_filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadError(error);
    }
  };

  return (
    <div className="space-y-5">
      <PageTitle
        actions={
          <Button variant="secondary" onClick={() => void download()}>
            <Download className="size-4" aria-hidden />
            Download
          </Button>
        }
      >
        {document.original_filename}
      </PageTitle>
      {downloadError != null && <Notice tone="error">{friendlyMessage(downloadError)}</Notice>}
      <ProcessingCard document={document} status={statusQuery.data} teamId={teamId} />
      <Card title="Details">
        <dl className="grid gap-4 sm:grid-cols-3">
          <Detail label="Team">{teamName(document.team_id)}</Detail>
          <Detail label="Size">{formatBytes(document.file_size)}</Detail>
          <Detail label="Version">{document.version}</Detail>
          <Detail label="Uploaded">{formatDate(document.created_at)}</Detail>
          <Detail label="Document type">{document.document_type ?? "—"}</Detail>
          <Detail label="Department">{document.department ?? "—"}</Detail>
          <Detail label="Category">{document.category ?? "—"}</Detail>
          <Detail label="Source">{document.source ?? "—"}</Detail>
          <Detail label="Access label">{document.access_level}</Detail>
          <Detail label="Tags">{document.tags.length > 0 ? document.tags.join(", ") : "—"}</Detail>
        </dl>
        {Object.keys(document.custom_metadata).length > 0 && (
          <div className="mt-4">
            <h3 className="text-xs font-medium text-slate-500">Custom details</h3>
            <ul className="mt-1 text-sm">
              {Object.entries(document.custom_metadata).map(([key, value]) => (
                <li key={key}>
                  {key}: {typeof value === "string" ? value : JSON.stringify(value)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
      <ChunksCard document={document} teamId={teamId} />
      {can(teamId, "team.manage") && <GrantsCard document={document} teamId={teamId} />}
    </div>
  );
}
