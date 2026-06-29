#!/usr/bin/env python3
"""Mock OpenAI-compatible /v1/embeddings server for codegraph M2 neural tests.

Returns a deterministic 64-dim bag-of-words vector (hashing trick) for any
input, so shared words -> high cosine. Lets us verify the embed -> store ->
cosine -> RRF pipeline without downloading a real model.
"""
import http.server
import json
import hashlib

DIM = 64


def embed(text):
    v = [0.0] * DIM
    for w in text.lower().replace("(", " ").replace(")", " ").split():
        h = int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM
        v[h] += 1.0
    return v


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        out = {"data": [{"embedding": embed(str(body.get("input", "")))}]}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
