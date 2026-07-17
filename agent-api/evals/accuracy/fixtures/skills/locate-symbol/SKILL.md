---
name: locate-symbol
description: Locate where a named function, class, or symbol is defined in the codebase and cite its file path.
when_to_use: When the user asks where a specific function/class/symbol is defined, declared, or lives in the code.
---

## Protocol

1. Call `codegraph__find_symbol` with the exact symbol name from the request.
2. Read the top result's file path and definition line.
3. State the answer as `<symbol> is defined in <file path>` — always include the file path verbatim.

## Rules

- Always cite the file path returned by the tool; never guess a path.
- Do not stop until you have a file path from `codegraph__find_symbol`.
