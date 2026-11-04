import { useMutation, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, EmptyState, Field, Input, Loading, LoadError, Notice, PageTitle, Select } from "../components/ui";
import { tenantKey, useAccess } from "../lib/access";
import { api, SEARCH_TIMEOUT_MS } from "../lib/api";
import { ACCESS_LEVELS, describeProgress } from "../lib/documents";
import type { DocumentRecord, SearchFilters, SearchResponse, SearchResult } from "../lib/types";

const PAGE_SIZE = 10;
const SNIPPET_LENGTH = 400;

interface SearchRequest {
  query: string;
  team_ids: number[];
  document_ids: number[];
  filters: SearchFilters;
  top_k: number;
  offset: number;
}

function snippet(text: string) {
  return text.length > SNIPPET_LENGTH ? `${text.slice(0, SNIPPET_LENGTH).trimEnd()}…` : text;
}

function ResultCard({ result }: { result: SearchResult }) {
  const { tenantId, teamName } = useAccess();
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm" data-testid="search-result">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/teams/${result.team_id}/documents/${result.document_id}`}>
          {result.document_title}
        </Link>
        <span className="flex flex-wrap items-center gap-1.5">
          <Badge>{teamName(result.team_id)}</Badge>
          {result.metadata.department && <Badge>{result.metadata.department}</Badge>}
          <Badge tone="info">Match {result.similarity_score.toFixed(2)}</Badge>
        </span>
      </div>
      {/* Untrusted document text, always rendered as plain text. */}
      <p className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-700">{snippet(result.content)}</p>
    </li>
  );
}

export function SearchPage() {
  const { tenantId, teams } = useAccess();
  const [query, setQuery] = useState("");
  const [team, setTeam] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [filters, setFilters] = useState({ department: "", document_type: "", category: "", access_level: "", file_type: "", tags: "" });
  const [request, setRequest] = useState<SearchRequest | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [hasMore, setHasMore] = useState(false);

  const documents = useQuery({
    queryKey: tenantKey(tenantId, "documents", { all: true }),
    queryFn: () => api<DocumentRecord[]>(`/tenants/${tenantId}/documents`, { query: { limit: 200 } }),
  });
  const unsearchable = documents.data?.filter((document) => describeProgress(document).active).length ?? 0;

  const search = useMutation({
    mutationFn: (body: SearchRequest) => api<SearchResponse>(`/tenants/${tenantId}/search`, { method: "POST", json: body, timeoutMs: SEARCH_TIMEOUT_MS }),
    onSuccess: (response, body) => {
      setResults((current) => (body.offset === 0 ? response.results : [...current, ...response.results]));
      setHasMore(response.has_more);
      setRequest(body);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    const chosen: SearchFilters = {};
    if (filters.department.trim()) chosen.department = filters.department.trim();
    if (filters.document_type.trim()) chosen.document_type = filters.document_type.trim();
    if (filters.category.trim()) chosen.category = filters.category.trim();
    if (filters.access_level) chosen.access_level = filters.access_level;
    if (filters.file_type) chosen.file_type = filters.file_type;
    const tags = filters.tags.split(",").map((tag) => tag.trim()).filter(Boolean);
    if (tags.length > 0) chosen.tags = tags;
    search.mutate({ query: text, team_ids: team ? [Number(team)] : [], document_ids: documentId ? [Number(documentId)] : [], filters: chosen, top_k: PAGE_SIZE, offset: 0 });
  };

  const set = (name: keyof typeof filters) => (event: { target: { value: string } }) => setFilters((current) => ({ ...current, [name]: event.target.value }));

  return (
    <div className="space-y-5">
      <PageTitle description="Finds passages by meaning, only in documents you can read.">Search</PageTitle>
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex gap-2">
          <Input aria-label="Search query" maxLength={2000} required placeholder="What are you looking for?" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Button type="submit" busy={search.isPending && search.variables?.offset === 0}>
            <Search className="size-4" aria-hidden />
            Search
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Team">
            <Select aria-label="Team" value={team} onChange={(event) => setTeam(event.target.value)}>
              <option value="">All teams</option>
              {teams.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Document">
            <Select aria-label="Document" value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
              <option value="">All documents</option>
              {documents.data?.map((document) => (
                <option key={document.id} value={document.id}>
                  {document.original_filename}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Department">
            <Input value={filters.department} onChange={set("department")} />
          </Field>
          <Field label="Document type">
            <Input value={filters.document_type} onChange={set("document_type")} />
          </Field>
          <Field label="Category">
            <Input value={filters.category} onChange={set("category")} />
          </Field>
          <Field label="Access label">
            <Select aria-label="Access label" value={filters.access_level} onChange={set("access_level")}>
              <option value="">Any</option>
              {ACCESS_LEVELS.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="File type">
            <Select aria-label="File type" value={filters.file_type} onChange={set("file_type")}>
              <option value="">Any</option>
              <option value=".pdf">PDF</option>
              <option value=".docx">Word</option>
              <option value=".txt">Text</option>
            </Select>
          </Field>
          <Field label="Tags" hint="Separate with commas. A document must have all of them.">
            <Input value={filters.tags} onChange={set("tags")} />
          </Field>
        </div>
      </form>

      {unsearchable > 0 && (
        <Notice tone="info">
          {unsearchable} {unsearchable === 1 ? "document is" : "documents are"} still being processed and won't appear in results yet.
        </Notice>
      )}
      {search.isPending && search.variables?.offset === 0 && <Loading label="Searching… the first search after a restart can take a few seconds." />}
      {search.isError && <LoadError error={search.error} retry={() => search.variables && search.mutate(search.variables)} />}
      {request && !search.isPending && results.length === 0 && !search.isError && <EmptyState>No matching passages in the documents you can read. Try other words or fewer filters.</EmptyState>}
      {results.length > 0 && (
        <>
          <p className="text-xs text-slate-500">Match is a similarity score. Higher means closer in meaning to your search, up to 1.00.</p>
          <ol className="space-y-3">
            {results.map((result) => (
              <ResultCard key={result.chunk_id} result={result} />
            ))}
          </ol>
          {hasMore && request && (
            <div className="text-center">
              <Button variant="secondary" busy={search.isPending} onClick={() => search.mutate({ ...request, offset: request.offset + PAGE_SIZE })}>
                Show more
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
