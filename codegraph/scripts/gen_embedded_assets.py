#!/usr/bin/env python3
"""Generate src/webui/embedded_assets.c from a built frontend dist/ directory.

Emits each asset as an inline C byte array plus a lookup table implementing the
ui/embedded_assets.h contract. Single self-contained .c (no separate object
files to link). "/" resolves to "/index.html".

Usage: gen_embedded_assets.py <dist_dir> <out_c>
"""
import os
import sys

CT = {
    ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript",
    ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
    ".ttf": "font/ttf", ".map": "application/json", ".txt": "text/plain",
    ".wasm": "application/wasm", ".webp": "image/webp",
}


def main():
    dist, out = sys.argv[1], sys.argv[2]
    files = []
    for root, _, names in os.walk(dist):
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, dist).replace(os.sep, "/")
            files.append(("/" + rel, full))
    files.sort()

    with open(out, "w", encoding="utf-8") as o:
        o.write('/* embedded_assets.c — GENERATED from graph-ui/dist. Do not edit. */\n')
        o.write('#include "webui/embedded_assets.h"\n#include <stddef.h>\n#include <string.h>\n\n')
        for i, (path, full) in enumerate(files):
            data = open(full, "rb").read()
            o.write("static const unsigned char a%d[] = {" % i)
            o.write(",".join(str(b) for b in data))
            if not data:
                o.write("0")
            o.write("};\n")
        o.write("\ncg_embedded_file_t CG_EMBEDDED_FILES[] = {\n")
        for i, (path, full) in enumerate(files):
            size = os.path.getsize(full)
            ext = os.path.splitext(path)[1].lower()
            ct = CT.get(ext, "application/octet-stream")
            o.write('    {"%s", a%d, %du, "%s"},\n' % (path, i, size, ct))
        o.write("};\n")
        o.write("const int CG_EMBEDDED_FILE_COUNT = %d;\n\n" % len(files))
        o.write(
            "const cg_embedded_file_t *cg_embedded_lookup(const char *path) {\n"
            '    if (!path || strcmp(path, "/") == 0) path = "/index.html";\n'
            "    for (int i = 0; i < CG_EMBEDDED_FILE_COUNT; i++) {\n"
            "        if (strcmp(CG_EMBEDDED_FILES[i].path, path) == 0) {\n"
            "            return &CG_EMBEDDED_FILES[i];\n"
            "        }\n"
            "    }\n"
            "    return NULL;\n"
            "}\n")
    print("wrote %s (%d assets)" % (out, len(files)))


if __name__ == "__main__":
    main()
