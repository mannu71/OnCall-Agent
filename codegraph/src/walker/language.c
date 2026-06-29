/*
 * language.c — Language detection from filename and extension.
 *
 * Maps file extensions and special filenames to CGLanguage enum values.
 * Handles .m disambiguation (Objective-C vs Magma vs MATLAB).
 * Consults the process-global user config (set via cg_set_user_lang_config)
 * before the built-in lookup table.
 */
#include "walker/discover.h"
#include "walker/userconfig.h"
#include "cg.h" // CGLanguage, CG_LANG_*

#include "base/constants.h"

enum { LANG_SCAN_PASSES = 2 };
#define SLEN(s) (sizeof(s) - 1)
#include <ctype.h>
#include <stdio.h>
#include <string.h>

/* ── Extension → Language lookup table ───────────────────────────── */

typedef struct {
    const char *ext; /* including dot, e.g. ".go" */
    CGLanguage language;
} ext_entry_t;

/* Sorted by extension for binary search (but linear scan is fine for ~120 entries) */
static const ext_entry_t EXT_TABLE[] = {
    /* Bash */
    {".bash", CG_LANG_BASH},
    {".sh", CG_LANG_BASH},

    /* C */
    {".c", CG_LANG_C},

    /* C++ */
    {".cc", CG_LANG_CPP},
    {".ccm", CG_LANG_CPP},
    {".cpp", CG_LANG_CPP},
    {".cppm", CG_LANG_CPP},
    {".cxx", CG_LANG_CPP},
    {".h", CG_LANG_CPP},
    {".hh", CG_LANG_CPP},
    {".hpp", CG_LANG_CPP},
    {".hxx", CG_LANG_CPP},
    {".ixx", CG_LANG_CPP},

    /* C# */
    {".cs", CG_LANG_CSHARP},

    /* Clojure */
    {".clj", CG_LANG_CLOJURE},
    {".cljc", CG_LANG_CLOJURE},
    {".cljs", CG_LANG_CLOJURE},

    /* CMake */
    {".cmake", CG_LANG_CMAKE},

    /* COBOL */
    {".cbl", CG_LANG_COBOL},
    {".cob", CG_LANG_COBOL},

    /* Common Lisp */
    {".cl", CG_LANG_COMMONLISP},
    {".lisp", CG_LANG_COMMONLISP},
    {".lsp", CG_LANG_COMMONLISP},

    /* CSS */
    {".css", CG_LANG_CSS},

    /* CUDA */
    {".cu", CG_LANG_CUDA},
    {".cuh", CG_LANG_CUDA},

    /* Dart */
    {".dart", CG_LANG_DART},

    /* Dockerfile */
    {".dockerfile", CG_LANG_DOCKERFILE},

    /* Elixir */
    {".ex", CG_LANG_ELIXIR},
    {".exs", CG_LANG_ELIXIR},

    /* DotEnv */
    {".env", CG_LANG_DOTENV},

    /* Elm */
    {".elm", CG_LANG_ELM},

    /* Emacs Lisp */
    {".el", CG_LANG_EMACSLISP},

    /* Erlang */
    {".erl", CG_LANG_ERLANG},

    /* F# */
    {".fs", CG_LANG_FSHARP},
    {".fsi", CG_LANG_FSHARP},
    {".fsx", CG_LANG_FSHARP},

    /* FORM */
    {".frm", CG_LANG_FORM},
    {".prc", CG_LANG_FORM},

    /* Fortran */
    {".f03", CG_LANG_FORTRAN},
    {".f08", CG_LANG_FORTRAN},
    {".f90", CG_LANG_FORTRAN},
    {".f95", CG_LANG_FORTRAN},

    /* GLSL */
    {".frag", CG_LANG_GLSL},
    {".glsl", CG_LANG_GLSL},
    {".vert", CG_LANG_GLSL},

    /* Go */
    {".go", CG_LANG_GO},

    /* GraphQL */
    {".gql", CG_LANG_GRAPHQL},
    {".graphql", CG_LANG_GRAPHQL},

    /* Groovy */
    {".gradle", CG_LANG_GROOVY},
    {".groovy", CG_LANG_GROOVY},

    /* Haskell */
    {".hs", CG_LANG_HASKELL},

    /* HCL / Terraform */
    {".hcl", CG_LANG_HCL},
    {".tf", CG_LANG_HCL},

    /* HTML */
    {".htm", CG_LANG_HTML},
    {".html", CG_LANG_HTML},

    /* INI */
    {".cfg", CG_LANG_INI},
    {".conf", CG_LANG_INI},
    {".ini", CG_LANG_INI},

    /* Java */
    {".java", CG_LANG_JAVA},

    /* JavaScript */
    {".js", CG_LANG_JAVASCRIPT},
    {".jsx", CG_LANG_JAVASCRIPT},
    {".mjs", CG_LANG_JAVASCRIPT}, /* ES modules (#197) */
    {".cjs", CG_LANG_JAVASCRIPT}, /* CommonJS modules */

    /* JSON */
    {".json", CG_LANG_JSON},

    /* Julia */
    {".jl", CG_LANG_JULIA},

    /* Kotlin */
    {".kt", CG_LANG_KOTLIN},
    {".kts", CG_LANG_KOTLIN},

    /* Lean */
    {".lean", CG_LANG_LEAN},

    /* Lua */
    {".lua", CG_LANG_LUA},

    /* Magma */
    {".mag", CG_LANG_MAGMA},
    {".magma", CG_LANG_MAGMA},

    /* Makefile */
    {".mk", CG_LANG_MAKEFILE},

    /* Markdown */
    {".md", CG_LANG_MARKDOWN},
    {".mdx", CG_LANG_MARKDOWN},

    /* MATLAB */
    {".m", CG_LANG_MATLAB},
    {".matlab", CG_LANG_MATLAB},
    {".mlx", CG_LANG_MATLAB},

    /* Meson */
    {".meson", CG_LANG_MESON},

    /* Nix */
    {".nix", CG_LANG_NIX},

    /* OCaml */
    {".ml", CG_LANG_OCAML},
    {".mli", CG_LANG_OCAML},

    /* Perl */
    {".pl", CG_LANG_PERL},
    {".pm", CG_LANG_PERL},

    /* PHP */
    {".php", CG_LANG_PHP},

    /* Protobuf */
    {".proto", CG_LANG_PROTOBUF},

    /* Python */
    {".py", CG_LANG_PYTHON},

    /* R — case insensitive handled separately */
    {".R", CG_LANG_R},
    {".r", CG_LANG_R},

    /* Ruby */
    {".gemspec", CG_LANG_RUBY},
    {".rake", CG_LANG_RUBY},
    {".rb", CG_LANG_RUBY},

    /* Rust */
    {".rs", CG_LANG_RUST},

    /* Scala */
    {".sc", CG_LANG_SCALA},
    {".scala", CG_LANG_SCALA},

    /* SCSS */
    {".scss", CG_LANG_SCSS},

    /* SQL */
    {".sql", CG_LANG_SQL},

    /* Svelte */
    {".svelte", CG_LANG_SVELTE},

    /* Swift */
    {".swift", CG_LANG_SWIFT},

    /* SystemVerilog + Verilog */
    {".sv", CG_LANG_VERILOG},
    {".v", CG_LANG_VERILOG},

    /* TOML */
    {".toml", CG_LANG_TOML},

    /* TSX */
    {".tsx", CG_LANG_TSX},

    /* TypeScript */
    {".ts", CG_LANG_TYPESCRIPT},
    {".mts", CG_LANG_TYPESCRIPT}, /* TS ES modules */
    {".cts", CG_LANG_TYPESCRIPT}, /* TS CommonJS modules */

    /* VimScript */
    {".vim", CG_LANG_VIMSCRIPT},
    {".vimrc", CG_LANG_VIMSCRIPT},
    {"justfile", CG_LANG_JUST},
    {"Justfile", CG_LANG_JUST},
    {".justfile", CG_LANG_JUST},
    {".just", CG_LANG_JUST}, /* `import 'common.just'` target files */
    {"hyprland.conf", CG_LANG_HYPRLANG},
    {"ssh_config", CG_LANG_SSHCONFIG},
    {"sshd_config", CG_LANG_SSHCONFIG},
    {"BUILD", CG_LANG_STARLARK},
    {"BUILD.bazel", CG_LANG_STARLARK},
    {"WORKSPACE", CG_LANG_STARLARK},
    {"WORKSPACE.bazel", CG_LANG_STARLARK},

    /* BitBake include fragments — `require/include foo.inc` target files. */
    {".inc", CG_LANG_BITBAKE},

    /* Vue */
    {".vue", CG_LANG_VUE},

    /* Wolfram */
    {".wl", CG_LANG_WOLFRAM},
    {".wls", CG_LANG_WOLFRAM},

    /* XML */
    {".xml", CG_LANG_XML},
    {".xsd", CG_LANG_XML},
    {".xsl", CG_LANG_XML},
    {".svg", CG_LANG_XML},

    /* YAML */
    {".yaml", CG_LANG_YAML},
    {".yml", CG_LANG_YAML},

    /* Ada */
    {".adb", CG_LANG_ADA},

    /* Ada */
    {".ads", CG_LANG_ADA},

    /* Agda */
    {".agda", CG_LANG_AGDA},

    /* Astro */
    {".astro", CG_LANG_ASTRO},

    /* AWK */
    {".awk", CG_LANG_AWK},

    /* BitBake */
    {".bb", CG_LANG_BITBAKE},

    /* BitBake */
    {".bbappend", CG_LANG_BITBAKE},

    /* BitBake */
    {".bbclass", CG_LANG_BITBAKE},

    /* Beancount */
    {".beancount", CG_LANG_BEANCOUNT},

    /* BibTeX */
    {".bib", CG_LANG_BIBTEX},

    /* Bicep */
    {".bicep", CG_LANG_BICEP},

    /* Blade */
    /* .blade.php handled by userconfig compound extensions, not EXT_TABLE */

    /* Starlark */
    {".bzl", CG_LANG_STARLARK},

    /* Cairo */
    {".cairo", CG_LANG_CAIRO},

    /* Cap'n Proto */
    {".capnp", CG_LANG_CAPNP},

    /* Apex */
    {".cls", CG_LANG_APEX},

    /* Crystal */
    {".cr", CG_LANG_CRYSTAL},

    /* CSV */
    {".csv", CG_LANG_CSV},

    /* D */
    {".d", CG_LANG_DLANG},

    /* Diff */
    {".diff", CG_LANG_DIFF},

    /* Pascal */
    {".dpr", CG_LANG_PASCAL},

    /* DeviceTree */
    {".dts", CG_LANG_DEVICETREE},

    /* DeviceTree */
    {".dtsi", CG_LANG_DEVICETREE},

    /* FunC */
    {".fc", CG_LANG_FUNC},

    /* Fish */
    {".fish", CG_LANG_FISH},

    /* Fennel */
    {".fnl", CG_LANG_FENNEL},

    /* HLSL */
    {".fx", CG_LANG_HLSL},

    /* GDScript */
    {".gd", CG_LANG_GDSCRIPT},

    /* Gleam */
    {".gleam", CG_LANG_GLEAM},

    /* GN */
    {".gn", CG_LANG_GN},

    /* GN */
    {".gni", CG_LANG_GN},

    /* Go Template */
    {".gotmpl", CG_LANG_GOTEMPLATE},
    {".tpl", CG_LANG_GOTEMPLATE}, /* Helm _helpers.tpl named-template definitions */

    /* Hare */
    {".ha", CG_LANG_HARE},

    /* Hyprlang */
    {".hl", CG_LANG_HYPRLANG},

    /* HLSL */
    {".hlsl", CG_LANG_HLSL},

    /* HLSL */
    {".hlsli", CG_LANG_HLSL},

    /* ISPC */
    {".ispc", CG_LANG_ISPC},

    /* Jinja2 */
    {".j2", CG_LANG_JINJA2},

    /* Janet */
    {".janet", CG_LANG_JANET},

    /* Jinja2 */
    {".jinja", CG_LANG_JINJA2},

    /* Jinja2 */
    {".jinja2", CG_LANG_JINJA2},

    /* JSON5 */
    {".json5", CG_LANG_JSON5},

    /* Jsonnet */
    {".jsonnet", CG_LANG_JSONNET},

    /* KDL */
    {".kdl", CG_LANG_KDL},

    /* Linker Script */
    {".ld", CG_LANG_LINKERSCRIPT},

    /* Linker Script */
    {".lds", CG_LANG_LINKERSCRIPT},

    /* Jsonnet */
    {".libsonnet", CG_LANG_JSONNET},

    /* Liquid */
    {".liquid", CG_LANG_LIQUID},

    /* LLVM IR */
    {".ll", CG_LANG_LLVM_IR},

    /* Pascal */
    {".lpr", CG_LANG_PASCAL},

    /* Luau */
    {".luau", CG_LANG_LUAU},

    /* Qt QML */
    {".qml", CG_LANG_QML},

    /* CFML / ColdFusion — .cfc components are script-dialect; .cfm are tag templates */
    {".cfc", CG_LANG_CFSCRIPT},
    {".cfm", CG_LANG_CFML},

    /* Mermaid */
    {".mermaid", CG_LANG_MERMAID},

    /* Mermaid */
    {".mmd", CG_LANG_MERMAID},

    /* Move */
    {".move", CG_LANG_MOVE},

    /* NASM */
    {".nasm", CG_LANG_NASM},

    /* Nickel */
    {".ncl", CG_LANG_NICKEL},

    /* Nim */

    /* Nim */

    /* Squirrel */
    {".nut", CG_LANG_SQUIRREL},

    /* Odin */
    {".odin", CG_LANG_ODIN},

    /* DeviceTree */
    {".overlay", CG_LANG_DEVICETREE},

    /* Pascal */
    {".pas", CG_LANG_PASCAL},

    /* Diff */
    {".patch", CG_LANG_DIFF},

    /* Pine Script */
    {".pine", CG_LANG_PINE},

    /* Pkl */
    {".pkl", CG_LANG_PKL},

    /* PO */
    {".po", CG_LANG_PO},

    /* Pony */
    {".pony", CG_LANG_PONY},

    /* PO */
    {".pot", CG_LANG_PO},

    /* Puppet */
    {".pp", CG_LANG_PUPPET},

    /* Prisma */
    {".prisma", CG_LANG_PRISMA},

    /* Properties */
    {".properties", CG_LANG_PROPERTIES},

    /* PowerShell */
    {".ps1", CG_LANG_POWERSHELL},

    /* PowerShell */
    {".psd1", CG_LANG_POWERSHELL},

    /* PowerShell */
    {".psm1", CG_LANG_POWERSHELL},

    /* PureScript */
    {".purs", CG_LANG_PURESCRIPT},

    /* ReScript */
    {".res", CG_LANG_RESCRIPT},

    /* ReScript */
    {".resi", CG_LANG_RESCRIPT},

    /* Regex */
    {".re", CG_LANG_REGEX},

    /* Racket */
    {".rkt", CG_LANG_RACKET},

    /* RON */
    {".ron", CG_LANG_RON},

    /* reStructuredText */
    {".rst", CG_LANG_RST},

    /* Assembly */
    {".s", CG_LANG_ASSEMBLY},

    /* Assembly */
    {".S", CG_LANG_ASSEMBLY},

    /* Scheme */
    {".scm", CG_LANG_SCHEME},

    /* Slang */
    {".slang", CG_LANG_SLANG},

    /* Smali */
    {".smali", CG_LANG_SMALI},

    /* Smithy */
    {".smithy", CG_LANG_SMITHY},

    /* Solidity */
    {".sol", CG_LANG_SOLIDITY},

    /* SOQL */
    {".soql", CG_LANG_SOQL},

    /* SOSL */
    {".sosl", CG_LANG_SOSL},

    /* Scheme */
    {".ss", CG_LANG_SCHEME},

    /* Starlark */
    {".star", CG_LANG_STARLARK},

    /* SystemVerilog */

    /* SystemVerilog */

    /* Sway */
    {".sw", CG_LANG_SWAY},

    /* Tcl */
    {".tcl", CG_LANG_TCL},

    /* TableGen */
    {".td", CG_LANG_TABLEGEN},

    /* Templ */
    {".templ", CG_LANG_TEMPL},

    /* Thrift */
    {".thrift", CG_LANG_THRIFT},

    /* Teal */
    {".tl", CG_LANG_TEAL},

    /* TLA+ */
    {".tla", CG_LANG_TLAPLUS},

    /* Go Template */
    {".tmpl", CG_LANG_GOTEMPLATE},

    /* Apex */
    {".trigger", CG_LANG_APEX},

    /* Typst */
    {".typ", CG_LANG_TYPST},

    /* VHDL */
    {".vhd", CG_LANG_VHDL},

    /* VHDL */
    {".vhdl", CG_LANG_VHDL},

    /* WGSL */
    {".wgsl", CG_LANG_WGSL},

    /* WIT */
    {".wit", CG_LANG_WIT},

    /* Zsh */
    {".zsh", CG_LANG_ZSH},

    /* Zig */
    {".zig", CG_LANG_ZIG},
};

#define EXT_TABLE_SIZE (sizeof(EXT_TABLE) / sizeof(EXT_TABLE[0]))

/* ── Special filename → Language lookup ──────────────────────────── */

typedef struct {
    const char *filename;
    CGLanguage language;
} filename_entry_t;

static const filename_entry_t FILENAME_TABLE[] = {
    {"CMakeLists.txt", CG_LANG_CMAKE},
    {"Dockerfile", CG_LANG_DOCKERFILE},
    {"GNUmakefile", CG_LANG_MAKEFILE},
    {"Makefile", CG_LANG_MAKEFILE},
    {"makefile", CG_LANG_MAKEFILE},
    {"meson.build", CG_LANG_MESON},
    {"meson.options", CG_LANG_MESON},
    {"meson_options.txt", CG_LANG_MESON},
    {"kustomization.yaml", CG_LANG_KUSTOMIZE},
    {"kustomization.yml", CG_LANG_KUSTOMIZE},
    /* Note: FILENAME_TABLE uses case-sensitive strcmp, so mixed-case variants
     * (e.g. "Kustomization.yaml") are not matched here.  They fall through to
     * CG_LANG_YAML and are re-classified by cg_is_kustomize_file() in
     * pass_k8s.c, which performs a case-insensitive comparison.  This is the
     * intended behaviour — no additional entries are needed. */
    {".vimrc", CG_LANG_VIMSCRIPT},
    {".zshrc", CG_LANG_ZSH},
    {".zshenv", CG_LANG_ZSH},
    {".zprofile", CG_LANG_ZSH},
    {"justfile", CG_LANG_JUST},
    {"Justfile", CG_LANG_JUST},
    {".justfile", CG_LANG_JUST},
    {"hyprland.conf", CG_LANG_HYPRLANG},
    {"ssh_config", CG_LANG_SSHCONFIG},
    {"sshd_config", CG_LANG_SSHCONFIG},
    {".ssh/config", CG_LANG_SSHCONFIG},
    {"BUILD", CG_LANG_STARLARK},
    {"BUILD.bazel", CG_LANG_STARLARK},
    {"WORKSPACE", CG_LANG_STARLARK},
    {"WORKSPACE.bazel", CG_LANG_STARLARK},
    {"requirements.txt", CG_LANG_REQUIREMENTS},
    {"requirements-dev.txt", CG_LANG_REQUIREMENTS},
    {"requirements-test.txt", CG_LANG_REQUIREMENTS},
    {"Kconfig", CG_LANG_KCONFIG},
    {"go.mod", CG_LANG_GOMOD},
    {".env", CG_LANG_DOTENV},
    {".env.local", CG_LANG_DOTENV},
    {".gitattributes", CG_LANG_GITATTRIBUTES},

};

#define FILENAME_TABLE_SIZE (sizeof(FILENAME_TABLE) / sizeof(FILENAME_TABLE[0]))

/* ── Language names ──────────────────────────────────────────────── */

static const char *LANG_NAMES[CG_LANG_COUNT] = {
    [CG_LANG_GO] = "Go",
    [CG_LANG_PYTHON] = "Python",
    [CG_LANG_JAVASCRIPT] = "JavaScript",
    [CG_LANG_TYPESCRIPT] = "TypeScript",
    [CG_LANG_TSX] = "TSX",
    [CG_LANG_RUST] = "Rust",
    [CG_LANG_JAVA] = "Java",
    [CG_LANG_CPP] = "C++",
    [CG_LANG_CSHARP] = "C#",
    [CG_LANG_PHP] = "PHP",
    [CG_LANG_LUA] = "Lua",
    [CG_LANG_SCALA] = "Scala",
    [CG_LANG_KOTLIN] = "Kotlin",
    [CG_LANG_RUBY] = "Ruby",
    [CG_LANG_C] = "C",
    [CG_LANG_BASH] = "Bash",
    [CG_LANG_ZIG] = "Zig",
    [CG_LANG_ELIXIR] = "Elixir",
    [CG_LANG_HASKELL] = "Haskell",
    [CG_LANG_OCAML] = "OCaml",
    [CG_LANG_OBJC] = "Objective-C",
    [CG_LANG_SWIFT] = "Swift",
    [CG_LANG_DART] = "Dart",
    [CG_LANG_PERL] = "Perl",
    [CG_LANG_GROOVY] = "Groovy",
    [CG_LANG_ERLANG] = "Erlang",
    [CG_LANG_R] = "R",
    [CG_LANG_HTML] = "HTML",
    [CG_LANG_CSS] = "CSS",
    [CG_LANG_SCSS] = "SCSS",
    [CG_LANG_YAML] = "YAML",
    [CG_LANG_TOML] = "TOML",
    [CG_LANG_HCL] = "HCL",
    [CG_LANG_SQL] = "SQL",
    [CG_LANG_DOCKERFILE] = "Dockerfile",
    [CG_LANG_CLOJURE] = "Clojure",
    [CG_LANG_FSHARP] = "F#",
    [CG_LANG_JULIA] = "Julia",
    [CG_LANG_VIMSCRIPT] = "VimScript",
    [CG_LANG_NIX] = "Nix",
    [CG_LANG_COMMONLISP] = "Common Lisp",
    [CG_LANG_ELM] = "Elm",
    [CG_LANG_FORTRAN] = "Fortran",
    [CG_LANG_CUDA] = "CUDA",
    [CG_LANG_COBOL] = "COBOL",
    [CG_LANG_VERILOG] = "Verilog",
    [CG_LANG_EMACSLISP] = "Emacs Lisp",
    [CG_LANG_JSON] = "JSON",
    [CG_LANG_XML] = "XML",
    [CG_LANG_MARKDOWN] = "Markdown",
    [CG_LANG_MAKEFILE] = "Makefile",
    [CG_LANG_CMAKE] = "CMake",
    [CG_LANG_PROTOBUF] = "Protobuf",
    [CG_LANG_GRAPHQL] = "GraphQL",
    [CG_LANG_VUE] = "Vue",
    [CG_LANG_SVELTE] = "Svelte",
    [CG_LANG_MESON] = "Meson",
    [CG_LANG_GLSL] = "GLSL",
    [CG_LANG_INI] = "INI",
    [CG_LANG_MATLAB] = "MATLAB",
    [CG_LANG_LEAN] = "Lean",
    [CG_LANG_FORM] = "FORM",
    [CG_LANG_MAGMA] = "Magma",
    [CG_LANG_WOLFRAM] = "Wolfram",
    [CG_LANG_KUSTOMIZE] = "Kustomize",
    [CG_LANG_K8S] = "Kubernetes",
    [CG_LANG_PINE] = "PineScript",
    [CG_LANG_SOLIDITY] = "Solidity",
    [CG_LANG_TYPST] = "Typst",
    [CG_LANG_GDSCRIPT] = "GDScript",
    [CG_LANG_GLEAM] = "Gleam",
    [CG_LANG_POWERSHELL] = "PowerShell",
    [CG_LANG_PASCAL] = "Pascal",
    [CG_LANG_DLANG] = "D",
    [CG_LANG_NIM] = "Nim",
    [CG_LANG_SCHEME] = "Scheme",
    [CG_LANG_FENNEL] = "Fennel",
    [CG_LANG_FISH] = "Fish",
    [CG_LANG_AWK] = "AWK",
    [CG_LANG_ZSH] = "Zsh",
    [CG_LANG_TCL] = "Tcl",
    [CG_LANG_ADA] = "Ada",
    [CG_LANG_AGDA] = "Agda",
    [CG_LANG_RACKET] = "Racket",
    [CG_LANG_ODIN] = "Odin",
    [CG_LANG_RESCRIPT] = "ReScript",
    [CG_LANG_PURESCRIPT] = "PureScript",
    [CG_LANG_NICKEL] = "Nickel",
    [CG_LANG_CRYSTAL] = "Crystal",
    [CG_LANG_TEAL] = "Teal",
    [CG_LANG_HARE] = "Hare",
    [CG_LANG_PONY] = "Pony",
    [CG_LANG_LUAU] = "Luau",
    [CG_LANG_QML] = "QML",
    [CG_LANG_CFSCRIPT] = "CFML",
    [CG_LANG_CFML] = "CFML",
    [CG_LANG_JANET] = "Janet",
    [CG_LANG_SWAY] = "Sway",
    [CG_LANG_NASM] = "NASM",
    [CG_LANG_ASSEMBLY] = "Assembly",
    [CG_LANG_ASTRO] = "Astro",
    [CG_LANG_BLADE] = "Blade",
    [CG_LANG_JUST] = "Just",
    [CG_LANG_GOTEMPLATE] = "Go Template",
    [CG_LANG_TEMPL] = "Templ",
    [CG_LANG_LIQUID] = "Liquid",
    [CG_LANG_JINJA2] = "Jinja2",
    [CG_LANG_PRISMA] = "Prisma",
    [CG_LANG_HYPRLANG] = "Hyprlang",
    [CG_LANG_DOTENV] = "DotEnv",
    [CG_LANG_SYSTEMVERILOG] = "SystemVerilog",
    [CG_LANG_DIFF] = "Diff",
    [CG_LANG_WGSL] = "WGSL",
    [CG_LANG_KDL] = "KDL",
    [CG_LANG_JSON5] = "JSON5",
    [CG_LANG_JSONNET] = "Jsonnet",
    [CG_LANG_RON] = "RON",
    [CG_LANG_THRIFT] = "Thrift",
    [CG_LANG_CAPNP] = "Cap'n Proto",
    [CG_LANG_PROPERTIES] = "Properties",
    [CG_LANG_SSHCONFIG] = "SSH Config",
    [CG_LANG_BIBTEX] = "BibTeX",
    [CG_LANG_STARLARK] = "Starlark",
    [CG_LANG_BICEP] = "Bicep",
    [CG_LANG_CSV] = "CSV",
    [CG_LANG_REQUIREMENTS] = "Requirements",
    [CG_LANG_HLSL] = "HLSL",
    [CG_LANG_VHDL] = "VHDL",
    [CG_LANG_DEVICETREE] = "DeviceTree",
    [CG_LANG_LINKERSCRIPT] = "Linker Script",
    [CG_LANG_GN] = "GN",
    [CG_LANG_KCONFIG] = "Kconfig",
    [CG_LANG_BITBAKE] = "BitBake",
    [CG_LANG_SMALI] = "Smali",
    [CG_LANG_TABLEGEN] = "TableGen",
    [CG_LANG_ISPC] = "ISPC",
    [CG_LANG_CAIRO] = "Cairo",
    [CG_LANG_MOVE] = "Move",
    [CG_LANG_SQUIRREL] = "Squirrel",
    [CG_LANG_FUNC] = "FunC",
    [CG_LANG_REGEX] = "Regex",
    [CG_LANG_JSDOC] = "JSDoc",
    [CG_LANG_RST] = "reStructuredText",
    [CG_LANG_BEANCOUNT] = "Beancount",
    [CG_LANG_MERMAID] = "Mermaid",
    [CG_LANG_PUPPET] = "Puppet",
    [CG_LANG_PO] = "PO",
    [CG_LANG_GITATTRIBUTES] = "gitattributes",
    [CG_LANG_GITIGNORE] = "gitignore",
    [CG_LANG_SLANG] = "Slang",
    [CG_LANG_LLVM_IR] = "LLVM IR",
    [CG_LANG_SMITHY] = "Smithy",
    [CG_LANG_WIT] = "WIT",
    [CG_LANG_TLAPLUS] = "TLA+",
    [CG_LANG_PKL] = "Pkl",
    [CG_LANG_GOMOD] = "Go Mod",
    [CG_LANG_APEX] = "Apex",
    [CG_LANG_SOQL] = "SOQL",
    [CG_LANG_SOSL] = "SOSL",

};

/* ── Public API ──────────────────────────────────────────────────── */

CGLanguage cg_language_for_extension(const char *ext) {
    if (!ext || !ext[0]) {
        return CG_LANG_COUNT;
    }

    /* Check user-defined overrides first */
    const cg_userconfig_t *ucfg = cg_get_user_lang_config();
    if (ucfg) {
        CGLanguage ulang = cg_userconfig_lookup(ucfg, ext);
        if (ulang != CG_LANG_COUNT) {
            return ulang;
        }
    }

    for (size_t i = 0; i < EXT_TABLE_SIZE; i++) {
        if (strcmp(EXT_TABLE[i].ext, ext) == 0) {
            return EXT_TABLE[i].language;
        }
    }
    return CG_LANG_COUNT;
}

CGLanguage cg_language_for_filename(const char *filename) {
    if (!filename || !filename[0]) {
        return CG_LANG_COUNT;
    }

    /* Check special filenames first */
    for (size_t i = 0; i < FILENAME_TABLE_SIZE; i++) {
        if (strcmp(FILENAME_TABLE[i].filename, filename) == 0) {
            return FILENAME_TABLE[i].language;
        }
    }

    /* DotEnv variant filenames (".env.local", ".env.production", …): the
     * filename starts with ".env." but its last "extension" (e.g. ".local")
     * is not a real language extension.  Match the dotenv convention used by
     * pass_envscan/pass_infrascan (".env" exact, ".env." prefix, "*.env"
     * suffix) so file-index routing agrees with direct extraction. */
    if (strncmp(filename, ".env.", SLEN(".env.")) == 0) {
        return CG_LANG_DOTENV;
    }

    /* Fall back to extension-based lookup.
     * For compound extensions (e.g. ".blade.php") defined in the user config,
     * scan from the first dot in the basename toward the last, checking user
     * config at each position.  Built-in extensions use the last dot only. */
    const char *last_dot = strrchr(filename, '.');
    if (!last_dot) {
        return CG_LANG_COUNT;
    }

    /* Probe compound extensions (e.g. ".blade.php") from the first dot toward
     * the last. Built-in compounds are checked first so e.g. Laravel Blade
     * templates map to Blade rather than the single-extension fallback (PHP);
     * user config can still add more (#258). */
    static const struct {
        const char *ext;
        CGLanguage lang;
    } COMPOUND_EXT_TABLE[] = {
        {".blade.php", CG_LANG_BLADE},
    };
    const cg_userconfig_t *ucfg = cg_get_user_lang_config();
    const char *p = strchr(filename, '.');
    while (p && p < last_dot) {
        for (size_t i = 0; i < sizeof(COMPOUND_EXT_TABLE) / sizeof(COMPOUND_EXT_TABLE[0]); i++) {
            if (strcmp(p, COMPOUND_EXT_TABLE[i].ext) == 0) {
                return COMPOUND_EXT_TABLE[i].lang;
            }
        }
        if (ucfg) {
            CGLanguage lang = cg_userconfig_lookup(ucfg, p);
            if (lang != CG_LANG_COUNT) {
                return lang;
            }
        }
        p = strchr(p + SKIP_ONE, '.');
    }

    /* Standard single-extension lookup (built-ins + user overrides). */
    return cg_language_for_extension(last_dot);
}

const char *cg_language_name(CGLanguage lang) {
    if (lang < 0 || lang >= CG_LANG_COUNT) {
        return "Unknown";
    }
    return LANG_NAMES[lang] ? LANG_NAMES[lang] : "Unknown";
}

/* ── .m file disambiguation ──────────────────────────────────────── */

/* Simple substring search helper */
static bool str_contains(const char *haystack, const char *needle) {
    return strstr(haystack, needle) != NULL;
}

static bool has_objc_markers(const char *buf) {
    return str_contains(buf, "@interface") || str_contains(buf, "@implementation") ||
           str_contains(buf, "@protocol") || str_contains(buf, "@property") ||
           str_contains(buf, "#import") || str_contains(buf, "@selector") ||
           str_contains(buf, "@encode") || str_contains(buf, "@synthesize") ||
           str_contains(buf, "@dynamic");
}

static bool has_magma_end_markers(const char *buf) {
    return str_contains(buf, "end function;") || str_contains(buf, "end procedure;") ||
           str_contains(buf, "end intrinsic;") || str_contains(buf, "end if;") ||
           str_contains(buf, "end for;") || str_contains(buf, "end while;");
}

/* Check for "intrinsic Name(" or "procedure Name(" patterns. */
static bool has_magma_callable_pattern(const char *buf) {
    const char *markers[] = {"intrinsic ", "procedure "};
    for (int i = 0; i < LANG_SCAN_PASSES; i++) {
        const char *p = strstr(buf, markers[i]);
        if (!p) {
            continue;
        }
        p += strlen(markers[i]);
        while (*p && isalpha((unsigned char)*p)) {
            p++;
        }
        if (*p == '(') {
            return true;
        }
    }
    return false;
}

/* Scan lines for MATLAB-specific markers (function/classdef/%%). */
static bool has_matlab_line_markers(const char *buf) {
    const char *line = buf;
    while (*line) {
        const char *p = line;
        while (*p == ' ' || *p == '\t') {
            p++;
        }
        if (strncmp(p, "function ", SLEN("function ")) == 0 ||
            strncmp(p, "function\t", SLEN("function\t")) == 0 ||
            strncmp(p, "classdef ", SLEN("classdef ")) == 0 ||
            strncmp(p, "classdef\t", SLEN("classdef\t")) == 0 || strncmp(p, "%%", PAIR_LEN) == 0 ||
            (*p == '%' && *(p + SKIP_ONE) != '{')) {
            return true;
        }
        const char *nl = strchr(line, '\n');
        if (!nl) {
            break;
        }
        line = nl + SKIP_ONE;
    }
    return false;
}

CGLanguage cg_disambiguate_m(const char *path) {
    if (!path) {
        return CG_LANG_MATLAB;
    }

    FILE *f = fopen(path, "r");
    if (!f) {
        return CG_LANG_MATLAB;
    }

    /* Read first 4KB */
    char buf[CG_SZ_4K + SKIP_ONE];
    size_t n = fread(buf, SKIP_ONE, CG_SZ_4K, f);
    buf[n] = '\0';
    (void)fclose(f);

    if (has_objc_markers(buf)) {
        return CG_LANG_OBJC;
    }
    if (has_magma_end_markers(buf)) {
        return CG_LANG_MAGMA;
    }
    if ((str_contains(buf, "intrinsic ") || str_contains(buf, "procedure ")) &&
        has_magma_callable_pattern(buf)) {
        return CG_LANG_MAGMA;
    }
    if (has_matlab_line_markers(buf)) {
        return CG_LANG_MATLAB;
    }

    return CG_LANG_MATLAB;
}
