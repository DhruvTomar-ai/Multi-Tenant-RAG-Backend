"""A stand-in for Groq's HTTP API, so tests use the real backend and SDK without spending quota.

The backend reads GROQ_BASE_URL through the Groq SDK, so no application code knows about this.
It answers from the first passage it receives. Words in the question choose other behaviour:

    trigger-500          the service fails, which the backend reports as 503
    trigger-bad-request  Groq refuses the request, which the backend reports as 502
    trigger-429          rate limited
    trigger-once         fails the first time this question arrives, then answers
    trigger-decline      the model says the passages don't contain the answer
    trigger-no-citation  the model cites a chunk that wasn't retrieved
    trigger-script       the answer contains HTML and script tags
    trigger-slow         answers after a delay
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEEN: set[str] = set()


def completion(content: str) -> dict:
    return {
        "id": "fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fake-groq",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


def reply(status: str = "answered", answer: str = "", ids: list | None = None) -> dict:
    return completion(json.dumps({"status": status, "answer": answer, "cited_chunk_ids": ids or []}))


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        prompt = body["messages"][-1]["content"]
        question = prompt.rsplit("Current question:", 1)[-1].strip()
        chunks = re.findall(r'<passage chunk_id="(\d+)"[^>]*>\s*(.*?)\s*</passage>', prompt, re.DOTALL)

        if "trigger-500" in question:
            return self._send(500, {"error": {"message": "fake outage"}})
        if "trigger-bad-request" in question:
            return self._send(400, {"error": {"message": "fake rejection"}})
        if "trigger-429" in question:
            return self._send(429, {"error": {"message": "fake rate limit"}}, {"retry-after": "1"})
        if "trigger-once" in question and question not in SEEN:
            SEEN.add(question)
            return self._send(500, {"error": {"message": "fake outage"}})
        if "trigger-slow" in question:
            time.sleep(2)
        if "trigger-decline" in question:
            return self._send(200, reply(status="insufficient_evidence"))
        if "trigger-no-citation" in question:
            return self._send(200, reply(answer="A confident claim with a made-up source [chunk:999999].", ids=["999999"]))

        chunk_id, text = chunks[0]
        if "trigger-script" in question:
            answer = f'<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>Plain answer [chunk:{chunk_id}].'
            return self._send(200, reply(answer=answer, ids=[chunk_id]))
        first_sentence = text.split(". ")[0].rstrip(".")
        self._send(200, reply(answer=f"{first_sentence} [chunk:{chunk_id}].", ids=[chunk_id]))

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
