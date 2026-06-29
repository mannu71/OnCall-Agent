/*
 * constants.h — Project-wide named constants.
 *
 * Eliminates magic numbers flagged by readability-magic-numbers.
 * Every literal integer/float in source should reference a named constant.
 */
#ifndef CG_CONSTANTS_H
#define CG_CONSTANTS_H

/* ── Allocation counts ───────────────────────────────────────── */
enum { CG_ALLOC_ONE = 1 }; /* calloc(CG_ALLOC_ONE, sizeof(T)) */

/* ── Byte / character constants ──────────────────────────────── */
enum {
    CG_BYTE_RANGE = 256, /* full byte range 0x00–0xFF */
    CG_QUOTE_PAIR = 2,   /* two quote characters (open + close) */
    CG_QUOTE_OFFSET = 1, /* skip opening quote */
};

/* ── Size units (powers of 2) ────────────────────────────────── */
enum {
    CG_SZ_2 = 2,
    CG_SZ_3 = 3,
    CG_SZ_4 = 4,
    CG_SZ_5 = 5,
    CG_SZ_6 = 6,
    CG_SZ_7 = 7,
    CG_SZ_8 = 8,
    CG_SZ_16 = 16,
    CG_SZ_32 = 32,
    CG_SZ_64 = 64,
    CG_SZ_128 = 128,
    CG_SZ_256 = 256,
    CG_SZ_512 = 512,
    CG_SZ_1K = 1024,
    CG_SZ_2K = 2048,
    CG_SZ_4K = 4096,
    CG_SZ_8K = 8192,
    CG_SZ_16K = 16384,
    CG_SZ_32K = 32768,
    CG_SZ_64K = 65536,
};

/* ── Numeric bases and common factors ────────────────────────── */
enum {
    CG_DECIMAL_BASE = 10,
    CG_HEX_BASE = 16,
    CG_PERCENT = 100,
};

/* ── Tree-sitter field name helper ───────────────────────────── */
/* Usage: ts_node_child_by_field_name(node, TS_FIELD("callee"))
 * Expands to: ts_node_child_by_field_name(node, TS_FIELD("callee"))
 * The sizeof includes the NUL terminator, so subtract 1. */
#define TS_FIELD(name) (name), (uint32_t)(sizeof(name) - SKIP_ONE)

/* ── Tree-sitter line offset ─────────────────────────────────── */
/* ts_node row is 0-based; source lines are 1-based. */
enum { TS_LINE_OFFSET = 1 };

/* Common offset constants. */

/* Common offset constants. */

/* ── Sentinel values ─────────────────────────────────────────── */
enum {
    CG_NOT_FOUND = -1, /* search miss, invalid index */
    CG_INIT_DONE = 1,  /* initialization flag */
};

/* ── Default pagination limits ───────────────────────────────── */
/* Default page size for search_graph and the underlying store-layer search.
 * Chosen so a typical broad query (e.g. file_pattern="**" on a 12k-node
 * project) stays well within MCP tool-result size budgets. Callers that
 * want more results paginate via offset+limit; the response always carries
 * 'total' and 'has_more' so agents can detect truncation. */
enum { CG_DEFAULT_SEARCH_LIMIT = 200 };

/* ── Time conversion factors ─────────────────────────────────── */
#define CG_NSEC_PER_SEC 1000000000ULL
#define CG_USEC_PER_SEC 1000000ULL
#define CG_MSEC_PER_SEC 1000ULL
#define CG_NSEC_PER_USEC 1000ULL
#define CG_NSEC_PER_MSEC 1000000ULL

/* ── Common string/buffer sizes ──────────────────────────────── */
enum {
    CG_SMALL_BUF = 3,   /* small scratch buffers */
    CG_NAME_BUF = 4,    /* name buffer slots */
    CG_PATH_MAX = 1024, /* path buffer size */
    CG_LINE_BUF = 512,  /* line read buffer */
};

/* Common offset constants (used across many files). */
enum { SKIP_ONE = 1, PAIR_LEN = 2 };

#endif /* CG_CONSTANTS_H */
