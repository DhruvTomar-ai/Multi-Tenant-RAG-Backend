import type { Citation } from "./types";

export const HIDDEN_ANSWER = "This answer's sources are no longer available to you.";
export const FAILED_ANSWER = "An answer couldn't be generated for this question.";

const INSUFFICIENT_REASONS: Record<string, { title: string; detail: string }> = {
  no_results: { title: "I couldn't find this in your documents.", detail: "None of the documents you can read matched this question." },
  low_relevance: { title: "Your documents don't seem to cover this.", detail: "The closest passages I found weren't related enough to answer from." },
  model_insufficient_evidence: { title: "The passages I found don't answer this.", detail: "I read the closest passages, and they didn't contain the answer." },
  no_valid_citations: { title: "I couldn't back up an answer with a source.", detail: "So I'm not showing one. Try asking in a different way." },
};

export function insufficientReason(reason: string | null) {
  return INSUFFICIENT_REASONS[reason ?? ""] ?? INSUFFICIENT_REASONS.no_results;
}

export type AnswerPart = { kind: "text"; text: string } | { kind: "citation"; citation: Citation; number: number };

/**
 * Splits an answer on its [chunk:ID] markers. Parts are rendered by React as text nodes, so nothing
 * in a model's answer can become HTML. A marker with no validated citation is dropped.
 */
export function parseAnswer(content: string, citations: Citation[]): AnswerPart[] {
  const parts: AnswerPart[] = [];
  const pattern = /\s*\[chunk:(\d+)\]/g;
  let position = 0;
  for (const match of content.matchAll(pattern)) {
    if (match.index > position) parts.push({ kind: "text", text: content.slice(position, match.index) });
    position = match.index + match[0].length;
    const index = citations.findIndex((citation) => citation.chunk_id === Number(match[1]));
    if (index >= 0) parts.push({ kind: "citation", citation: citations[index], number: index + 1 });
  }
  if (position < content.length) parts.push({ kind: "text", text: content.slice(position) });
  return parts;
}

export function newClientMessageId() {
  return crypto.randomUUID();
}
