#ifndef CG_H
#define CG_H

#include <stdint.h>
#include <stdbool.h>
#include "arena.h"
#include "tree_sitter/api.h"

// Language enum mirrors lang.Language in Go.
// Order must match lang_specs.c tables.
typedef enum {
    CG_LANG_GO = 0,
    CG_LANG_PYTHON,
    CG_LANG_JAVASCRIPT,
    CG_LANG_TYPESCRIPT,
    CG_LANG_TSX,
    CG_LANG_RUST,
    CG_LANG_JAVA,
    CG_LANG_CPP,
    CG_LANG_CSHARP,
    CG_LANG_PHP,
    CG_LANG_LUA,
    CG_LANG_SCALA,
    CG_LANG_KOTLIN,
    CG_LANG_RUBY,
    CG_LANG_C,
    CG_LANG_BASH,
    CG_LANG_ZIG,
    CG_LANG_ELIXIR,
    CG_LANG_HASKELL,
    CG_LANG_OCAML,
    CG_LANG_OBJC,
    CG_LANG_SWIFT,
    CG_LANG_DART,
    CG_LANG_PERL,
    CG_LANG_GROOVY,
    CG_LANG_ERLANG,
    CG_LANG_R,
    CG_LANG_HTML,
    CG_LANG_CSS,
    CG_LANG_SCSS,
    CG_LANG_YAML,
    CG_LANG_TOML,
    CG_LANG_HCL,
    CG_LANG_SQL,
    CG_LANG_DOCKERFILE,
    // New languages (v0.5 expansion)
    CG_LANG_CLOJURE,
    CG_LANG_FSHARP,
    CG_LANG_JULIA,
    CG_LANG_VIMSCRIPT,
    CG_LANG_NIX,
    CG_LANG_COMMONLISP,
    CG_LANG_ELM,
    CG_LANG_FORTRAN,
    CG_LANG_CUDA,
    CG_LANG_COBOL,
    CG_LANG_VERILOG,
    CG_LANG_EMACSLISP,
    CG_LANG_JSON,
    CG_LANG_XML,
    CG_LANG_MARKDOWN,
    CG_LANG_MAKEFILE,
    CG_LANG_CMAKE,
    CG_LANG_PROTOBUF,
    CG_LANG_GRAPHQL,
    CG_LANG_VUE,
    CG_LANG_SVELTE,
    CG_LANG_MESON,
    CG_LANG_GLSL,
    CG_LANG_INI,
    // Scientific/math languages
    CG_LANG_MATLAB,
    CG_LANG_LEAN,
    CG_LANG_FORM,
    CG_LANG_MAGMA,
    CG_LANG_WOLFRAM,
    CG_LANG_SOLIDITY,
    CG_LANG_TYPST,
    CG_LANG_GDSCRIPT,
    CG_LANG_GLEAM,
    CG_LANG_POWERSHELL,
    CG_LANG_PASCAL,
    CG_LANG_DLANG,
    CG_LANG_NIM,
    CG_LANG_SCHEME,
    CG_LANG_FENNEL,
    CG_LANG_FISH,
    CG_LANG_AWK,
    CG_LANG_ZSH,
    CG_LANG_TCL,
    CG_LANG_ADA,
    CG_LANG_AGDA,
    CG_LANG_RACKET,
    CG_LANG_ODIN,
    CG_LANG_RESCRIPT,
    CG_LANG_PURESCRIPT,
    CG_LANG_NICKEL,
    CG_LANG_CRYSTAL,
    CG_LANG_TEAL,
    CG_LANG_HARE,
    CG_LANG_PONY,
    CG_LANG_LUAU,
    CG_LANG_JANET,
    CG_LANG_SWAY,
    CG_LANG_NASM,
    CG_LANG_ASSEMBLY,
    CG_LANG_ASTRO,
    CG_LANG_BLADE,
    CG_LANG_JUST,
    CG_LANG_GOTEMPLATE,
    CG_LANG_TEMPL,
    CG_LANG_LIQUID,
    CG_LANG_JINJA2,
    CG_LANG_PRISMA,
    CG_LANG_HYPRLANG,
    CG_LANG_DOTENV,
    CG_LANG_DIFF,
    CG_LANG_WGSL,
    CG_LANG_KDL,
    CG_LANG_JSON5,
    CG_LANG_JSONNET,
    CG_LANG_RON,
    CG_LANG_THRIFT,
    CG_LANG_CAPNP,
    CG_LANG_PROPERTIES,
    CG_LANG_SSHCONFIG,
    CG_LANG_BIBTEX,
    CG_LANG_STARLARK,
    CG_LANG_BICEP,
    CG_LANG_CSV,
    CG_LANG_REQUIREMENTS,
    CG_LANG_HLSL,
    CG_LANG_VHDL,
    CG_LANG_SYSTEMVERILOG,
    CG_LANG_DEVICETREE,
    CG_LANG_LINKERSCRIPT,
    CG_LANG_GN,
    CG_LANG_KCONFIG,
    CG_LANG_BITBAKE,
    CG_LANG_SMALI,
    CG_LANG_TABLEGEN,
    CG_LANG_ISPC,
    CG_LANG_CAIRO,
    CG_LANG_MOVE,
    CG_LANG_SQUIRREL,
    CG_LANG_FUNC,
    CG_LANG_REGEX,
    CG_LANG_JSDOC,
    CG_LANG_RST,
    CG_LANG_BEANCOUNT,
    CG_LANG_MERMAID,
    CG_LANG_PUPPET,
    CG_LANG_PO,
    CG_LANG_GITATTRIBUTES,
    CG_LANG_GITIGNORE,
    CG_LANG_SLANG,
    CG_LANG_LLVM_IR,
    CG_LANG_SMITHY,
    CG_LANG_WIT,
    CG_LANG_TLAPLUS,
    CG_LANG_PKL,
    CG_LANG_GOMOD,
    CG_LANG_APEX,
    CG_LANG_SOQL,
    CG_LANG_SOSL,
    CG_LANG_KUSTOMIZE, // kustomization.yaml — Kubernetes overlay tool
    CG_LANG_K8S,       // Generic Kubernetes manifest (apiVersion: detected)
    CG_LANG_PINE,      // Pine Script (TradingView indicator / strategy language)
    CG_LANG_QML,       // Qt QML (Qt Modeling Language — declarative UI + embedded JS)
    CG_LANG_CFSCRIPT,  // CFML script dialect (.cfc components — Lucee/ColdFusion)
    CG_LANG_CFML,      // CFML tag dialect (.cfm templates — Lucee/ColdFusion)
    CG_LANG_COUNT
} CGLanguage;

// --- Extraction result structs ---

typedef struct {
    const char *name;           // short name
    const char *qualified_name; // project.path.name
    const char *label;          // "Function", "Method", "Class", "Variable", "Module"
    const char *file_path;      // relative path
    uint32_t start_line;
    uint32_t end_line;
    const char *signature;     // parameter text (NULL if none)
    const char *return_type;   // return type text (NULL if none)
    const char *receiver;      // Go method receiver (NULL if none)
    const char *docstring;     // leading doc comment (NULL if none)
    const char *parent_class;  // enclosing class QN for methods (NULL if none)
    const char **decorators;   // NULL-terminated array (NULL if none)
    const char **base_classes; // NULL-terminated array (NULL if none)
    const char **param_names;  // NULL-terminated array (NULL if none)
    const char **param_types;  // NULL-terminated array (NULL if none)
    const char **return_types; // NULL-terminated array (NULL if none)
    const char *route_path;    // HTTP route path from decorator (e.g., "/api/users") or NULL
    const char *route_method;  // HTTP method from decorator (e.g., "POST") or NULL
    int complexity;            // cyclomatic complexity
    int cognitive;             // cognitive complexity (nesting-weighted)
    int loop_count;            // number of loop constructs in the body
    int loop_depth;            // max nested-loop depth (bottleneck proxy)
    bool is_recursive;         // body contains a direct self-call (seed for "recursive")
    int param_count;           // number of parameters (large = complexity smell)
    int max_access_depth;      // deepest chained member/subscript access (a.b.c.d)
    int linear_scan_in_loop;   // count of linear-scan calls (find/contains/indexOf) inside loops
    int alloc_in_loop;         // count of allocation/append calls inside loops
    bool recursion_in_loop;    // a self-call occurs inside a loop body
    bool unguarded_recursion;  // recursive with no self-call guarded by a conditional
    int lines;                 // body line count
    uint32_t *fingerprint;     // MinHash fingerprint (arena-allocated, K values) or NULL
    int fingerprint_k;         // number of hash values (CG_MINHASH_K or 0)
    bool is_exported;
    bool is_abstract;
    bool is_test;
    bool is_entry_point;
    const char *structural_profile; // AST structural profile (arena-allocated) or NULL
    const char *body_tokens; // space-separated raw identifier tokens from body (arena) or NULL
} CGDefinition;

/* Argument captured from a call expression */
typedef struct {
    const char *expr;    // raw expression text ("payload.info", "MY_URL", "'hello'")
    const char *value;   // resolved string value or NULL (constant propagation)
    const char *keyword; // keyword name if keyword arg ("url", "topic_id"), NULL if positional
    int index;           // positional index (0-based)
} CGCallArg;

#define CG_MAX_CALL_ARGS 8

typedef struct {
    const char *callee_name;            // raw callee text ("pkg.Func", "foo")
    const char *enclosing_func_qn;      // QN of enclosing function (or module QN)
    const char *first_string_arg;       // first string literal argument (URL, topic, key) or NULL
    const char *second_arg_name;        // second argument identifier (handler ref) or NULL
    CGCallArg args[CG_MAX_CALL_ARGS]; // first N arguments with expressions
    int arg_count;                      // number of captured arguments
    int loop_depth;                     // enclosing loop nesting at the call site
    int branch_depth;                   // enclosing branch nesting at the call site
    int start_line;                     // 1-based source line of the call (for def range-match)
    bool is_method;                     // Perl-only: arrow/method call ($obj->m). Default false.
} CGCall;

typedef struct {
    const char *local_name;  // local alias or name
    const char *module_path; // resolved module path / QN
} CGImport;

typedef struct {
    const char *ref_name;          // referenced identifier
    const char *enclosing_func_qn; // QN of enclosing function (or module QN)
} CGUsage;

typedef struct {
    const char *exception_name;    // exception class/type name
    const char *enclosing_func_qn; // QN of enclosing function
} CGThrow;

typedef struct {
    const char *var_name;          // variable name
    const char *enclosing_func_qn; // QN of enclosing function
    bool is_write;                 // true = write, false = read
} CGReadWrite;

typedef struct {
    const char *type_name;         // referenced type/class name
    const char *enclosing_func_qn; // QN of enclosing function
} CGTypeRef;

typedef struct {
    const char *env_key;           // environment variable key
    const char *enclosing_func_qn; // QN of enclosing function
} CGEnvAccess;

typedef struct {
    const char *var_name;          // variable being assigned
    const char *type_name;         // class/type name of RHS constructor
    const char *enclosing_func_qn; // QN of enclosing function
} CGTypeAssign;

// String reference: URL, config key, or async target found in source.
// Extracted from string literals during AST walk.
typedef enum {
    CG_STRREF_URL = 0,    // REST path or full URL
    CG_STRREF_CONFIG = 1, // config file path or env var key
} CGStringRefKind;

typedef struct {
    const char *value;             // the string literal content
    const char *enclosing_func_qn; // QN of enclosing function
    const char *key_path;          // dotted key path from YAML/JSON nesting (NULL if flat)
    CGStringRefKind kind;         // URL, CONFIG
} CGStringRef;

/* Infrastructure binding: topic/queue → endpoint URL.
 * Extracted from YAML/HCL/JSON subscription/scheduler configs.
 * Used by pass_route_nodes to connect async Route nodes to handler services. */
typedef struct {
    const char *source_name; // topic, queue, or schedule name
    const char *target_url;  // push_endpoint, uri, or http_target URL
    const char *broker;      // "pubsub", "cloud_tasks", "cloud_scheduler", "sqs", "kafka"
} CGInfraBinding;

/* Pub/sub channel participation.  One record per emit() or on()/addListener()
 * call detected in source — the receiver (e.g. Socket.IO client, EventEmitter
 * instance) is intentionally NOT identified; matching is by channel_name
 * across files, which captures the common pattern of one logical bus per
 * service.  Transport disambiguates Socket.IO vs EventEmitter vs future
 * detectors (Kafka, Cloud Pub/Sub, etc.). */
typedef enum {
    CG_CHANNEL_EMIT = 0,
    CG_CHANNEL_LISTEN = 1,
} CGChannelDirection;

typedef struct {
    const char *channel_name;      // literal channel name (e.g. "user.created")
    const char *transport;         // "socketio", "event_emitter", ...
    const char *enclosing_func_qn; // QN of the function containing the emit/on call
    CGChannelDirection direction;
} CGChannel;

// Rust: impl Trait for Struct
typedef struct {
    const char *trait_name;  // trait name (raw text)
    const char *struct_name; // struct/type name (raw text)
} CGImplTrait;

// LSP-resolved call: high-confidence type-aware call resolution
typedef struct {
    const char *caller_qn; // enclosing function QN
    const char *callee_qn; // resolved target QN (fully qualified)
    const char *strategy;  // "lsp_type_dispatch", "lsp_direct", etc.
    float confidence;      // 0.90-0.95
    const char *reason;    // diagnostic label for unresolved calls (NULL if resolved)
} CGResolvedCall;

typedef struct {
    CGResolvedCall *items;
    int count;
    int cap;
} CGResolvedCallArray;

// Growable arrays used during extraction.
typedef struct {
    CGDefinition *items;
    int count;
    int cap;
} CGDefArray;

typedef struct {
    CGCall *items;
    int count;
    int cap;
} CGCallArray;

typedef struct {
    CGImport *items;
    int count;
    int cap;
} CGImportArray;

typedef struct {
    CGUsage *items;
    int count;
    int cap;
} CGUsageArray;

typedef struct {
    CGThrow *items;
    int count;
    int cap;
} CGThrowArray;

typedef struct {
    CGReadWrite *items;
    int count;
    int cap;
} CGRWArray;

typedef struct {
    CGTypeRef *items;
    int count;
    int cap;
} CGTypeRefArray;

typedef struct {
    CGEnvAccess *items;
    int count;
    int cap;
} CGEnvAccessArray;

typedef struct {
    CGTypeAssign *items;
    int count;
    int cap;
} CGTypeAssignArray;

typedef struct {
    CGStringRef *items;
    int count;
    int cap;
} CGStringRefArray;

typedef struct {
    CGInfraBinding *items;
    int count;
    int cap;
} CGInfraBindingArray;

typedef struct {
    CGImplTrait *items;
    int count;
    int cap;
} CGImplTraitArray;

typedef struct {
    CGChannel *items;
    int count;
    int cap;
} CGChannelArray;

// Full extraction result for one file.
typedef struct {
    CGArena arena; // owns all string memory

    CGDefArray defs;
    CGCallArray calls;
    CGImportArray imports;
    CGUsageArray usages;
    CGThrowArray throws;
    CGRWArray rw;
    CGTypeRefArray type_refs;
    CGEnvAccessArray env_accesses;
    CGTypeAssignArray type_assigns;
    CGImplTraitArray impl_traits;       // Rust: impl Trait for Struct pairs
    CGResolvedCallArray resolved_calls; // LSP-resolved calls (high confidence)
    CGStringRefArray string_refs;       // URL/config string literals from AST
    CGInfraBindingArray infra_bindings; // topic→URL pairs from IaC configs
    CGChannelArray channels;            // Socket.IO / EventEmitter pub/sub participation

    const char *module_qn;      // module qualified name
    const char *namespace_name; // declared namespace/package (Java/Kotlin/C#/PHP), NULL if none
    const char **exports;       // NULL-terminated (NULL if none)
    const char **constants;     // NULL-terminated (NULL if none)
    const char **global_vars;   // NULL-terminated (NULL if none)
    const char **macros;        // NULL-terminated, C/C++ only (NULL if none)

    bool has_error;
    const char *error_msg;
    bool is_test_file;
    int imports_count;
    TSTree *cached_tree;     // retained parse tree (caller frees via cg_free_tree)
    CGLanguage cached_lang; // language of cached tree (for parser selection)

    // Retained source bytes — copied into `arena` by the parallel
    // extract pass so the fused cross-file LSP step in resolve_worker
    // can run without re-reading the file from disk. NULL when the
    // file exceeded the per-file (100 MB) or total (2 GB) retention
    // cap; in that case the cross-file LSP step is skipped for this
    // file (defs/calls already extracted are unaffected).
    const char *source;
    int source_len;
} CGFileResult;

// --- Enclosing function cache ---
// Avoids repeated parent-chain walks for nodes within the same function body.
// Each entry records a function's byte range and its precomputed QN.
#define EFC_SIZE 64 // power of 2 for fast modulo

typedef struct {
    uint32_t start_byte;
    uint32_t end_byte;
    const char *qn;
} EFCEntry;

typedef struct {
    EFCEntry entries[EFC_SIZE];
    int count;
} EFCache;

// --- Extraction context passed to sub-extractors ---

// Module-level string constant map (for constant propagation)
#define CG_MAX_STRING_CONSTANTS 256
typedef struct {
    const char *names[CG_MAX_STRING_CONSTANTS];
    const char *values[CG_MAX_STRING_CONSTANTS];
    int count;
} CGStringConstantMap;

typedef struct {
    CGArena *arena;
    CGFileResult *result;
    const char *source;
    int source_len;
    CGLanguage language;
    const char *project;
    const char *rel_path;
    const char *module_qn;
    TSNode root;
    EFCache ef_cache;                      // enclosing function cache
    const char *enclosing_class_qn;        // for nested class QN computation
    CGStringConstantMap string_constants; // module-level NAME = "value" pairs
} CGExtractCtx;

// --- Public API ---

// Bind third-party allocators (tree-sitter, sqlite3, libgit2) to mimalloc as
// defense-in-depth, so they never depend on the fragile MI_OVERRIDE symbol
// override (#424). MUST be called as the very first statement of main(), before
// any sqlite3_open*/sqlite3_initialize (SQLITE_CONFIG_MALLOC returns
// SQLITE_MISUSE once sqlite has initialized) and before any git_libgit2_init.
// Idempotent (static guard); intended for single-threaded startup. cg_init()
// also calls it so non-main entry points (pipeline passes) still get the binds.
// In the test build (no CG_BIND_TS_ALLOCATOR) this is a no-op.
void cg_alloc_init(void);

// Initialize the library. Call once at startup. Returns 0 on success.
int cg_init(void);

// Extract all data from one file. Caller must call cg_free_result().
// source must remain valid for the duration of the call.
// timeout_micros: per-file parse timeout in microseconds (0 = no timeout).
CGFileResult *cg_extract_file(const char *source, int source_len, CGLanguage language,
                                const char *project, const char *rel_path, int64_t timeout_micros,
                                const char **extra_defines, // NULL-terminated, or NULL
                                const char **include_paths  // NULL-terminated, or NULL
);

// Free all memory associated with a result.
void cg_free_result(CGFileResult *result);

// Free only the cached tree from a result (caller retained it for reuse).
void cg_free_tree(CGFileResult *result);

// Free a standalone TSTree pointer (for Go layer cleanup).
void cg_free_tree_ptr(TSTree *tree);

// Reset the thread-local parser's internal state, releasing slab-allocated
// subtrees. Must be called BEFORE cg_slab_reset_thread() so the slab rebuild
// doesn't corrupt live parser state.
void cg_reset_thread_parser(void);

// Destroy the thread-local parser. Call on worker thread exit.
void cg_destroy_thread_parser(void);

// Shutdown the library. Call once at exit.
void cg_shutdown(void);

// Profiling: get accumulated parse/extraction times and file count.
typedef struct {
    uint64_t *parse_ns;
    uint64_t *extract_ns;
    uint64_t *files;
} cg_profile_out_t;
void cg_get_profile(cg_profile_out_t out);
uint64_t cg_get_lsp_ns(void);
uint64_t cg_get_preprocess_ns(void);
uint64_t cg_get_files_preprocessed(void);
void cg_reset_profile(void);

// Toggle C/C++ preprocessor Macro-node extraction (#375). The pipeline enables
// it only for full/advanced index modes (it dominates extraction on macro-dense
// codebases). Default ON. Set before extraction; read-only during.
void cg_set_macro_extraction(int enabled);
int cg_macro_extraction_enabled(void);

// --- Internal helpers used by extractors ---

// Growable array push functions (arena-allocated, no individual free needed).
void cg_defs_push(CGDefArray *arr, CGArena *a, CGDefinition def);
void cg_calls_push(CGCallArray *arr, CGArena *a, CGCall call);
void cg_imports_push(CGImportArray *arr, CGArena *a, CGImport imp);
void cg_usages_push(CGUsageArray *arr, CGArena *a, CGUsage usage);
void cg_throws_push(CGThrowArray *arr, CGArena *a, CGThrow thr);
void cg_rw_push(CGRWArray *arr, CGArena *a, CGReadWrite rw);
void cg_typerefs_push(CGTypeRefArray *arr, CGArena *a, CGTypeRef tr);
void cg_envaccess_push(CGEnvAccessArray *arr, CGArena *a, CGEnvAccess ea);
void cg_typeassign_push(CGTypeAssignArray *arr, CGArena *a, CGTypeAssign ta);
void cg_stringref_push(CGStringRefArray *arr, CGArena *a, CGStringRef sr);
void cg_infrabinding_push(CGInfraBindingArray *arr, CGArena *a, CGInfraBinding ib);
void cg_impltrait_push(CGImplTraitArray *arr, CGArena *a, CGImplTrait it);
void cg_resolvedcall_push(CGResolvedCallArray *arr, CGArena *a, CGResolvedCall rc);
void cg_channels_push(CGChannelArray *arr, CGArena *a, CGChannel ch);

// --- Sub-extractor entry points ---

void cg_extract_definitions(CGExtractCtx *ctx);
void cg_extract_imports(CGExtractCtx *ctx);
void cg_extract_usages(CGExtractCtx *ctx);
void cg_extract_semantic(CGExtractCtx *ctx);
void cg_extract_type_refs(CGExtractCtx *ctx);
void cg_extract_env_accesses(CGExtractCtx *ctx);
void cg_extract_type_assigns(CGExtractCtx *ctx);
void cg_extract_channels(CGExtractCtx *ctx);

// Single-pass unified extraction (replaces the 7 calls above except defs+imports).
void cg_extract_unified(CGExtractCtx *ctx);

// K8s / Kustomize semantic extractor (called when language is CG_LANG_K8S or CG_LANG_KUSTOMIZE).
void cg_extract_k8s(CGExtractCtx *ctx);

#endif // CG_H
