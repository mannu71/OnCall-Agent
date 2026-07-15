/* grammar_dlopen.c — resolves tree-sitter grammar factories at runtime
 * from a prebuilt shared object (CG_TS_SO, default /opt/codegraph/languages.so).
 * Defines every tree_sitter_<lang>() the lang table references, so the engine
 * links without compiling grammar sources in. Missing symbols resolve to NULL
 * (that language simply will not parse). */
#include <stdlib.h>
#include <stddef.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif
#include "tree_sitter/api.h"
#include "base/log.h"

/* Open the prebuilt grammar library once. CG_TS_SO overrides the path on both
 * platforms; the default is Linux's install path or a DLL next to the binary on
 * Windows (the Windows loader searches the app directory first). A load failure
 * disables EVERY language, so it is logged once at error level rather than
 * silently no-op'ing all parsing. */
static void *cg_ts_handle(void) {
    static void *h = NULL;
    static int tried = 0;
    if (!tried) {
        tried = 1;
        const char *p = getenv("CG_TS_SO");
#ifdef _WIN32
        if (!p || !*p) {
            p = "languages.dll";
        }
        h = (void *)LoadLibraryA(p);
#else
        if (!p || !*p) {
            p = "/opt/codegraph/languages.so";
        }
        h = dlopen(p, RTLD_NOW | RTLD_GLOBAL);
#endif
        if (!h) {
            cg_log_error("grammar.load_failed", "path", p,
                         "detail", "no languages will parse; set CG_TS_SO");
        }
    }
    return h;
}

typedef const TSLanguage *(*cg_ts_fn)(void);

static const TSLanguage *cg_resolve(const char *sym) {
    void *h = cg_ts_handle();
    if (!h) {
        return NULL;
    }
#ifdef _WIN32
    cg_ts_fn fn = (cg_ts_fn)(void (*)(void))GetProcAddress((HMODULE)h, sym);
#else
    cg_ts_fn fn = (cg_ts_fn)dlsym(h, sym);
#endif
    return fn ? fn() : NULL;
}

const TSLanguage *tree_sitter_COBOL(void) { return cg_resolve("tree_sitter_COBOL"); }
const TSLanguage *tree_sitter_ada(void) { return cg_resolve("tree_sitter_ada"); }
const TSLanguage *tree_sitter_agda(void) { return cg_resolve("tree_sitter_agda"); }
const TSLanguage *tree_sitter_apex(void) { return cg_resolve("tree_sitter_apex"); }
const TSLanguage *tree_sitter_asm(void) { return cg_resolve("tree_sitter_asm"); }
const TSLanguage *tree_sitter_astro(void) { return cg_resolve("tree_sitter_astro"); }
const TSLanguage *tree_sitter_awk(void) { return cg_resolve("tree_sitter_awk"); }
const TSLanguage *tree_sitter_bash(void) { return cg_resolve("tree_sitter_bash"); }
const TSLanguage *tree_sitter_beancount(void) { return cg_resolve("tree_sitter_beancount"); }
const TSLanguage *tree_sitter_bibtex(void) { return cg_resolve("tree_sitter_bibtex"); }
const TSLanguage *tree_sitter_bicep(void) { return cg_resolve("tree_sitter_bicep"); }
const TSLanguage *tree_sitter_bitbake(void) { return cg_resolve("tree_sitter_bitbake"); }
const TSLanguage *tree_sitter_blade(void) { return cg_resolve("tree_sitter_blade"); }
const TSLanguage *tree_sitter_c(void) { return cg_resolve("tree_sitter_c"); }
const TSLanguage *tree_sitter_c_sharp(void) { return cg_resolve("tree_sitter_c_sharp"); }
const TSLanguage *tree_sitter_cairo(void) { return cg_resolve("tree_sitter_cairo"); }
const TSLanguage *tree_sitter_capnp(void) { return cg_resolve("tree_sitter_capnp"); }
const TSLanguage *tree_sitter_cfml(void) { return cg_resolve("tree_sitter_cfml"); }
const TSLanguage *tree_sitter_cfscript(void) { return cg_resolve("tree_sitter_cfscript"); }
const TSLanguage *tree_sitter_clojure(void) { return cg_resolve("tree_sitter_clojure"); }
const TSLanguage *tree_sitter_cmake(void) { return cg_resolve("tree_sitter_cmake"); }
const TSLanguage *tree_sitter_commonlisp(void) { return cg_resolve("tree_sitter_commonlisp"); }
const TSLanguage *tree_sitter_cpp(void) { return cg_resolve("tree_sitter_cpp"); }
const TSLanguage *tree_sitter_crystal(void) { return cg_resolve("tree_sitter_crystal"); }
const TSLanguage *tree_sitter_css(void) { return cg_resolve("tree_sitter_css"); }
const TSLanguage *tree_sitter_csv(void) { return cg_resolve("tree_sitter_csv"); }
const TSLanguage *tree_sitter_cuda(void) { return cg_resolve("tree_sitter_cuda"); }
const TSLanguage *tree_sitter_d(void) { return cg_resolve("tree_sitter_d"); }
const TSLanguage *tree_sitter_dart(void) { return cg_resolve("tree_sitter_dart"); }
const TSLanguage *tree_sitter_devicetree(void) { return cg_resolve("tree_sitter_devicetree"); }
const TSLanguage *tree_sitter_diff(void) { return cg_resolve("tree_sitter_diff"); }
const TSLanguage *tree_sitter_dockerfile(void) { return cg_resolve("tree_sitter_dockerfile"); }
const TSLanguage *tree_sitter_dotenv(void) { return cg_resolve("tree_sitter_dotenv"); }
const TSLanguage *tree_sitter_elisp(void) { return cg_resolve("tree_sitter_elisp"); }
const TSLanguage *tree_sitter_elixir(void) { return cg_resolve("tree_sitter_elixir"); }
const TSLanguage *tree_sitter_elm(void) { return cg_resolve("tree_sitter_elm"); }
const TSLanguage *tree_sitter_erlang(void) { return cg_resolve("tree_sitter_erlang"); }
const TSLanguage *tree_sitter_fennel(void) { return cg_resolve("tree_sitter_fennel"); }
const TSLanguage *tree_sitter_fish(void) { return cg_resolve("tree_sitter_fish"); }
const TSLanguage *tree_sitter_form(void) { return cg_resolve("tree_sitter_form"); }
const TSLanguage *tree_sitter_fortran(void) { return cg_resolve("tree_sitter_fortran"); }
const TSLanguage *tree_sitter_fsharp(void) { return cg_resolve("tree_sitter_fsharp"); }
const TSLanguage *tree_sitter_func(void) { return cg_resolve("tree_sitter_func"); }
const TSLanguage *tree_sitter_gdscript(void) { return cg_resolve("tree_sitter_gdscript"); }
const TSLanguage *tree_sitter_gitattributes(void) { return cg_resolve("tree_sitter_gitattributes"); }
const TSLanguage *tree_sitter_gitignore(void) { return cg_resolve("tree_sitter_gitignore"); }
const TSLanguage *tree_sitter_gleam(void) { return cg_resolve("tree_sitter_gleam"); }
const TSLanguage *tree_sitter_glsl(void) { return cg_resolve("tree_sitter_glsl"); }
const TSLanguage *tree_sitter_gn(void) { return cg_resolve("tree_sitter_gn"); }
const TSLanguage *tree_sitter_go(void) { return cg_resolve("tree_sitter_go"); }
const TSLanguage *tree_sitter_gomod(void) { return cg_resolve("tree_sitter_gomod"); }
const TSLanguage *tree_sitter_gotmpl(void) { return cg_resolve("tree_sitter_gotmpl"); }
const TSLanguage *tree_sitter_graphql(void) { return cg_resolve("tree_sitter_graphql"); }
const TSLanguage *tree_sitter_groovy(void) { return cg_resolve("tree_sitter_groovy"); }
const TSLanguage *tree_sitter_hare(void) { return cg_resolve("tree_sitter_hare"); }
const TSLanguage *tree_sitter_haskell(void) { return cg_resolve("tree_sitter_haskell"); }
const TSLanguage *tree_sitter_hcl(void) { return cg_resolve("tree_sitter_hcl"); }
const TSLanguage *tree_sitter_hlsl(void) { return cg_resolve("tree_sitter_hlsl"); }
const TSLanguage *tree_sitter_html(void) { return cg_resolve("tree_sitter_html"); }
const TSLanguage *tree_sitter_hyprlang(void) { return cg_resolve("tree_sitter_hyprlang"); }
const TSLanguage *tree_sitter_ini(void) { return cg_resolve("tree_sitter_ini"); }
const TSLanguage *tree_sitter_ispc(void) { return cg_resolve("tree_sitter_ispc"); }
const TSLanguage *tree_sitter_janet_simple(void) { return cg_resolve("tree_sitter_janet_simple"); }
const TSLanguage *tree_sitter_java(void) { return cg_resolve("tree_sitter_java"); }
const TSLanguage *tree_sitter_javascript(void) { return cg_resolve("tree_sitter_javascript"); }
const TSLanguage *tree_sitter_jinja2(void) { return cg_resolve("tree_sitter_jinja2"); }
const TSLanguage *tree_sitter_jsdoc(void) { return cg_resolve("tree_sitter_jsdoc"); }
const TSLanguage *tree_sitter_json(void) { return cg_resolve("tree_sitter_json"); }
const TSLanguage *tree_sitter_json5(void) { return cg_resolve("tree_sitter_json5"); }
const TSLanguage *tree_sitter_jsonnet(void) { return cg_resolve("tree_sitter_jsonnet"); }
const TSLanguage *tree_sitter_julia(void) { return cg_resolve("tree_sitter_julia"); }
const TSLanguage *tree_sitter_just(void) { return cg_resolve("tree_sitter_just"); }
const TSLanguage *tree_sitter_kconfig(void) { return cg_resolve("tree_sitter_kconfig"); }
const TSLanguage *tree_sitter_kdl(void) { return cg_resolve("tree_sitter_kdl"); }
const TSLanguage *tree_sitter_kotlin(void) { return cg_resolve("tree_sitter_kotlin"); }
const TSLanguage *tree_sitter_lean(void) { return cg_resolve("tree_sitter_lean"); }
const TSLanguage *tree_sitter_linkerscript(void) { return cg_resolve("tree_sitter_linkerscript"); }
const TSLanguage *tree_sitter_liquid(void) { return cg_resolve("tree_sitter_liquid"); }
const TSLanguage *tree_sitter_llvm(void) { return cg_resolve("tree_sitter_llvm"); }
const TSLanguage *tree_sitter_lua(void) { return cg_resolve("tree_sitter_lua"); }
const TSLanguage *tree_sitter_luau(void) { return cg_resolve("tree_sitter_luau"); }
const TSLanguage *tree_sitter_magma(void) { return cg_resolve("tree_sitter_magma"); }
const TSLanguage *tree_sitter_make(void) { return cg_resolve("tree_sitter_make"); }
const TSLanguage *tree_sitter_markdown(void) { return cg_resolve("tree_sitter_markdown"); }
const TSLanguage *tree_sitter_matlab(void) { return cg_resolve("tree_sitter_matlab"); }
const TSLanguage *tree_sitter_mermaid(void) { return cg_resolve("tree_sitter_mermaid"); }
const TSLanguage *tree_sitter_meson(void) { return cg_resolve("tree_sitter_meson"); }
const TSLanguage *tree_sitter_move(void) { return cg_resolve("tree_sitter_move"); }
const TSLanguage *tree_sitter_nasm(void) { return cg_resolve("tree_sitter_nasm"); }
const TSLanguage *tree_sitter_nickel(void) { return cg_resolve("tree_sitter_nickel"); }
const TSLanguage *tree_sitter_nix(void) { return cg_resolve("tree_sitter_nix"); }
const TSLanguage *tree_sitter_objc(void) { return cg_resolve("tree_sitter_objc"); }
const TSLanguage *tree_sitter_ocaml(void) { return cg_resolve("tree_sitter_ocaml"); }
const TSLanguage *tree_sitter_odin(void) { return cg_resolve("tree_sitter_odin"); }
const TSLanguage *tree_sitter_pascal(void) { return cg_resolve("tree_sitter_pascal"); }
const TSLanguage *tree_sitter_perl(void) { return cg_resolve("tree_sitter_perl"); }
const TSLanguage *tree_sitter_php_only(void) { return cg_resolve("tree_sitter_php_only"); }
const TSLanguage *tree_sitter_pine(void) { return cg_resolve("tree_sitter_pine"); }
const TSLanguage *tree_sitter_pkl(void) { return cg_resolve("tree_sitter_pkl"); }
const TSLanguage *tree_sitter_po(void) { return cg_resolve("tree_sitter_po"); }
const TSLanguage *tree_sitter_pony(void) { return cg_resolve("tree_sitter_pony"); }
const TSLanguage *tree_sitter_powershell(void) { return cg_resolve("tree_sitter_powershell"); }
const TSLanguage *tree_sitter_prisma(void) { return cg_resolve("tree_sitter_prisma"); }
const TSLanguage *tree_sitter_properties(void) { return cg_resolve("tree_sitter_properties"); }
const TSLanguage *tree_sitter_proto(void) { return cg_resolve("tree_sitter_proto"); }
const TSLanguage *tree_sitter_puppet(void) { return cg_resolve("tree_sitter_puppet"); }
const TSLanguage *tree_sitter_purescript(void) { return cg_resolve("tree_sitter_purescript"); }
const TSLanguage *tree_sitter_python(void) { return cg_resolve("tree_sitter_python"); }
const TSLanguage *tree_sitter_qmljs(void) { return cg_resolve("tree_sitter_qmljs"); }
const TSLanguage *tree_sitter_r(void) { return cg_resolve("tree_sitter_r"); }
const TSLanguage *tree_sitter_racket(void) { return cg_resolve("tree_sitter_racket"); }
const TSLanguage *tree_sitter_regex(void) { return cg_resolve("tree_sitter_regex"); }
const TSLanguage *tree_sitter_requirements(void) { return cg_resolve("tree_sitter_requirements"); }
const TSLanguage *tree_sitter_rescript(void) { return cg_resolve("tree_sitter_rescript"); }
const TSLanguage *tree_sitter_ron(void) { return cg_resolve("tree_sitter_ron"); }
const TSLanguage *tree_sitter_rst(void) { return cg_resolve("tree_sitter_rst"); }
const TSLanguage *tree_sitter_ruby(void) { return cg_resolve("tree_sitter_ruby"); }
const TSLanguage *tree_sitter_rust(void) { return cg_resolve("tree_sitter_rust"); }
const TSLanguage *tree_sitter_scala(void) { return cg_resolve("tree_sitter_scala"); }
const TSLanguage *tree_sitter_scheme(void) { return cg_resolve("tree_sitter_scheme"); }
const TSLanguage *tree_sitter_scss(void) { return cg_resolve("tree_sitter_scss"); }
const TSLanguage *tree_sitter_slang(void) { return cg_resolve("tree_sitter_slang"); }
const TSLanguage *tree_sitter_smali(void) { return cg_resolve("tree_sitter_smali"); }
const TSLanguage *tree_sitter_smithy(void) { return cg_resolve("tree_sitter_smithy"); }
const TSLanguage *tree_sitter_solidity(void) { return cg_resolve("tree_sitter_solidity"); }
const TSLanguage *tree_sitter_soql(void) { return cg_resolve("tree_sitter_soql"); }
const TSLanguage *tree_sitter_sosl(void) { return cg_resolve("tree_sitter_sosl"); }
const TSLanguage *tree_sitter_sql(void) { return cg_resolve("tree_sitter_sql"); }
const TSLanguage *tree_sitter_squirrel(void) { return cg_resolve("tree_sitter_squirrel"); }
const TSLanguage *tree_sitter_ssh_config(void) { return cg_resolve("tree_sitter_ssh_config"); }
const TSLanguage *tree_sitter_starlark(void) { return cg_resolve("tree_sitter_starlark"); }
const TSLanguage *tree_sitter_svelte(void) { return cg_resolve("tree_sitter_svelte"); }
const TSLanguage *tree_sitter_sway(void) { return cg_resolve("tree_sitter_sway"); }
const TSLanguage *tree_sitter_swift(void) { return cg_resolve("tree_sitter_swift"); }
const TSLanguage *tree_sitter_systemverilog(void) { return cg_resolve("tree_sitter_systemverilog"); }
const TSLanguage *tree_sitter_tablegen(void) { return cg_resolve("tree_sitter_tablegen"); }
const TSLanguage *tree_sitter_tcl(void) { return cg_resolve("tree_sitter_tcl"); }
const TSLanguage *tree_sitter_teal(void) { return cg_resolve("tree_sitter_teal"); }
const TSLanguage *tree_sitter_templ(void) { return cg_resolve("tree_sitter_templ"); }
const TSLanguage *tree_sitter_thrift(void) { return cg_resolve("tree_sitter_thrift"); }
const TSLanguage *tree_sitter_tlaplus(void) { return cg_resolve("tree_sitter_tlaplus"); }
const TSLanguage *tree_sitter_toml(void) { return cg_resolve("tree_sitter_toml"); }
const TSLanguage *tree_sitter_tsx(void) { return cg_resolve("tree_sitter_tsx"); }
const TSLanguage *tree_sitter_typescript(void) { return cg_resolve("tree_sitter_typescript"); }
const TSLanguage *tree_sitter_typst(void) { return cg_resolve("tree_sitter_typst"); }
const TSLanguage *tree_sitter_verilog(void) { return cg_resolve("tree_sitter_verilog"); }
const TSLanguage *tree_sitter_vhdl(void) { return cg_resolve("tree_sitter_vhdl"); }
const TSLanguage *tree_sitter_vim(void) { return cg_resolve("tree_sitter_vim"); }
const TSLanguage *tree_sitter_vue(void) { return cg_resolve("tree_sitter_vue"); }
const TSLanguage *tree_sitter_wgsl(void) { return cg_resolve("tree_sitter_wgsl"); }
const TSLanguage *tree_sitter_wit(void) { return cg_resolve("tree_sitter_wit"); }
const TSLanguage *tree_sitter_wolfram(void) { return cg_resolve("tree_sitter_wolfram"); }
const TSLanguage *tree_sitter_xml(void) { return cg_resolve("tree_sitter_xml"); }
const TSLanguage *tree_sitter_yaml(void) { return cg_resolve("tree_sitter_yaml"); }
const TSLanguage *tree_sitter_zig(void) { return cg_resolve("tree_sitter_zig"); }
const TSLanguage *tree_sitter_zsh(void) { return cg_resolve("tree_sitter_zsh"); }
