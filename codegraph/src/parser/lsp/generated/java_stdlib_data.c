/*
 * java_stdlib_data.c — Curated Java standard-library type/method registry.
 *
 * Strategy:
 *   - java.lang.* — fully covered (the implicit-import package).
 *     Object, String, StringBuilder, StringBuffer, CharSequence, Class,
 *     Throwable + the common subclass tree, Number + boxed primitives,
 *     Math, System, Thread, Iterable, Comparable, Cloneable, Enum, Record,
 *     AutoCloseable, the common Exception types.
 *   - java.util.* — collections + iterators + Optional + Date/Calendar +
 *     Arrays/Collections + Scanner/Random/UUID + Map.Entry.
 *   - java.io.* — streams, readers, writers, File, IOException family.
 *   - java.nio.file.* — Path, Paths, Files (often-used helpers).
 *   - java.util.function — the 21 functional interfaces.
 *   - java.util.stream  — Stream + Collectors entry points.
 *   - java.util.concurrent — ExecutorService, Future, CompletableFuture,
 *     ConcurrentHashMap, the concurrent collection set.
 *   - java.time — LocalDate/LocalTime/LocalDateTime/Duration/Instant.
 *
 * Method signatures use registry-level fidelity: receiver, short name,
 * return type. Param types are intentionally unmodeled (the resolver
 * chooses overloads by arity, with type compatibility scoring breaking
 * ties — see cg_registry_lookup_method_by_args).
 *
 * This is the JLS-spec-aligned slice of the stdlib that 90%+ of real-world
 * Java code touches.
 */

#include "../type_rep.h"
#include "../type_registry.h"
#include "../../arena.h"
#include "../java_lsp.h"
#include <string.h>

#define REG_TYPE(qn_, short_, is_iface_, parents_)            \
    do {                                                      \
        memset(&rt, 0, sizeof(rt));                           \
        rt.qualified_name = (qn_);                            \
        rt.short_name = (short_);                             \
        rt.is_interface = (is_iface_);                        \
        rt.embedded_types = (parents_);                       \
        cg_registry_add_type(reg, rt);                       \
    } while (0)

#define REG_METHOD(class_qn_, method_name_, ret_type_)                                          \
    do {                                                                                        \
        memset(&rf, 0, sizeof(rf));                                                             \
        rf.min_params = -1;                                                                     \
        rf.qualified_name =                                                                     \
            cg_arena_sprintf(arena, "%s.%s", (class_qn_), (method_name_));                     \
        rf.short_name = (method_name_);                                                         \
        rf.receiver_type = (class_qn_);                                                         \
        {                                                                                       \
            const CGType **rets =                                                              \
                (const CGType **)cg_arena_alloc(arena, 2 * sizeof(*rets));                    \
            rets[0] = (ret_type_);                                                              \
            rets[1] = NULL;                                                                     \
            rf.signature = cg_type_func(arena, NULL, NULL, rets);                              \
        }                                                                                       \
        cg_registry_add_func(reg, rf);                                                         \
    } while (0)

#define REG_CTOR(class_qn_, short_name_)                                              \
    do {                                                                              \
        memset(&rf, 0, sizeof(rf));                                                   \
        rf.min_params = -1;                                                           \
        rf.qualified_name =                                                           \
            cg_arena_sprintf(arena, "%s.%s", (class_qn_), (short_name_));            \
        rf.short_name = (short_name_);                                                \
        rf.receiver_type = (class_qn_);                                               \
        {                                                                             \
            const CGType **rets =                                                    \
                (const CGType **)cg_arena_alloc(arena, 2 * sizeof(*rets));          \
            rets[0] = cg_type_named(arena, (class_qn_));                             \
            rets[1] = NULL;                                                           \
            rf.signature = cg_type_func(arena, NULL, NULL, rets);                    \
        }                                                                             \
        cg_registry_add_func(reg, rf);                                               \
    } while (0)

#define REG_FIELD(class_qn_, name_, type_)                                            \
    do {                                                                              \
        const CGRegisteredType *_existing =                                          \
            cg_registry_lookup_type(reg, (class_qn_));                               \
        (void)_existing;                                                              \
        /* Field append handled by REG_TYPE_FIELDS below. */                          \
        /* Placeholder for future per-field appends. */                               \
    } while (0)

void cg_java_stdlib_register(CGTypeRegistry *reg, CGArena *arena) {
    CGRegisteredType rt;
    CGRegisteredFunc rf;

    /* ── Type-parent lists (must be static so addresses outlive the call) ── */
    static const char *no_parents[] = {NULL};
    static const char *parents_object[] = {"java.lang.Object", NULL};
    static const char *parents_throwable[] = {"java.lang.Object", NULL};
    static const char *parents_exception[] = {"java.lang.Throwable", NULL};
    static const char *parents_error[] = {"java.lang.Throwable", NULL};
    static const char *parents_runtime_exc[] = {"java.lang.Exception", NULL};
    static const char *parents_io_exc[] = {"java.lang.Exception", NULL};
    static const char *parents_number[] = {"java.lang.Object", NULL};
    static const char *parents_integer[] = {"java.lang.Number", NULL};
    static const char *parents_long[] = {"java.lang.Number", NULL};
    static const char *parents_double[] = {"java.lang.Number", NULL};
    static const char *parents_float[] = {"java.lang.Number", NULL};
    static const char *parents_short[] = {"java.lang.Number", NULL};
    static const char *parents_byte[] = {"java.lang.Number", NULL};
    static const char *parents_string[] = {"java.lang.Object", NULL};
    static const char *parents_charseq[] = {NULL};
    static const char *parents_iterable[] = {NULL};
    static const char *parents_collection[] = {"java.lang.Iterable", NULL};
    static const char *parents_list[] = {"java.util.Collection", NULL};
    static const char *parents_set[] = {"java.util.Collection", NULL};
    static const char *parents_queue[] = {"java.util.Collection", NULL};
    static const char *parents_deque[] = {"java.util.Queue", NULL};
    static const char *parents_map[] = {NULL};
    static const char *parents_map_entry[] = {NULL};
    static const char *parents_iterator[] = {NULL};
    static const char *parents_arraylist[] = {"java.util.List", NULL};
    static const char *parents_linkedlist[] = {"java.util.List", NULL};
    static const char *parents_hashset[] = {"java.util.Set", NULL};
    static const char *parents_treeset[] = {"java.util.Set", NULL};
    static const char *parents_linkedhashset[] = {"java.util.Set", NULL};
    static const char *parents_hashmap[] = {"java.util.Map", NULL};
    static const char *parents_treemap[] = {"java.util.Map", NULL};
    static const char *parents_linkedhashmap[] = {"java.util.Map", NULL};
    static const char *parents_concurrent_hashmap[] = {"java.util.Map", NULL};

    static const char *parents_inputstream[] = {"java.lang.AutoCloseable", NULL};
    static const char *parents_outputstream[] = {"java.lang.AutoCloseable", NULL};
    static const char *parents_reader[] = {"java.lang.AutoCloseable", NULL};
    static const char *parents_writer[] = {"java.lang.AutoCloseable", NULL};
    static const char *parents_buffered_reader[] = {"java.io.Reader", NULL};
    static const char *parents_buffered_writer[] = {"java.io.Writer", NULL};
    static const char *parents_print_stream[] = {"java.io.OutputStream", NULL};
    static const char *parents_print_writer[] = {"java.io.Writer", NULL};
    static const char *parents_file_input_stream[] = {"java.io.InputStream", NULL};
    static const char *parents_file_output_stream[] = {"java.io.OutputStream", NULL};
    static const char *parents_file_reader[] = {"java.io.Reader", NULL};
    static const char *parents_file_writer[] = {"java.io.Writer", NULL};
    static const char *parents_io_exception[] = {"java.lang.Exception", NULL};
    static const char *parents_runtime_exc_chain[] = {"java.lang.RuntimeException", NULL};
    /* Parent lists for types previously registered with inline compound
     * literals. A compound literal has automatic (block) storage duration,
     * so storing its address into the registry left a dangling stack pointer
     * once the REG_TYPE statement's block ended — an AddressSanitizer
     * stack-use-after-scope when the inheritance walk later read
     * rt->embedded_types[0]. These must be static so their addresses outlive
     * the call, exactly like the parent lists above. */
    static const char *parents_gregorian_calendar[] = {"java.util.Calendar", NULL};
    static const char *parents_file_not_found_exc[] = {"java.io.IOException", NULL};
    static const char *parents_closeable[] = {"java.lang.AutoCloseable", NULL};
    static const char *parents_unary_operator[] = {"java.util.function.Function", NULL};
    static const char *parents_binary_operator[] = {"java.util.function.BiFunction", NULL};
    static const char *parents_completable_future[] = {"java.util.concurrent.Future", NULL};
    static const char *parents_reentrant_lock[] = {"java.util.concurrent.locks.Lock", NULL};

    /* ── java.lang ─────────────────────────────────────────────── */
    REG_TYPE("java.lang.Object", "Object", false, no_parents);
    REG_TYPE("java.lang.Class", "Class", false, parents_object);
    REG_TYPE("java.lang.ClassLoader", "ClassLoader", false, parents_object);
    REG_TYPE("java.lang.CharSequence", "CharSequence", true, parents_charseq);
    REG_TYPE("java.lang.String", "String", false, parents_string);
    REG_TYPE("java.lang.StringBuilder", "StringBuilder", false, parents_object);
    REG_TYPE("java.lang.StringBuffer", "StringBuffer", false, parents_object);
    REG_TYPE("java.lang.Number", "Number", false, parents_number);
    REG_TYPE("java.lang.Integer", "Integer", false, parents_integer);
    REG_TYPE("java.lang.Long", "Long", false, parents_long);
    REG_TYPE("java.lang.Short", "Short", false, parents_short);
    REG_TYPE("java.lang.Byte", "Byte", false, parents_byte);
    REG_TYPE("java.lang.Float", "Float", false, parents_float);
    REG_TYPE("java.lang.Double", "Double", false, parents_double);
    REG_TYPE("java.lang.Boolean", "Boolean", false, parents_object);
    REG_TYPE("java.lang.Character", "Character", false, parents_object);
    REG_TYPE("java.lang.Void", "Void", false, parents_object);
    REG_TYPE("java.lang.Iterable", "Iterable", true, parents_iterable);
    REG_TYPE("java.lang.Comparable", "Comparable", true, no_parents);
    REG_TYPE("java.lang.Cloneable", "Cloneable", true, no_parents);
    REG_TYPE("java.lang.Runnable", "Runnable", true, no_parents);
    REG_TYPE("java.lang.AutoCloseable", "AutoCloseable", true, no_parents);
    REG_TYPE("java.lang.Math", "Math", false, parents_object);
    REG_TYPE("java.lang.System", "System", false, parents_object);
    REG_TYPE("java.lang.Thread", "Thread", false, parents_object);
    REG_TYPE("java.lang.Process", "Process", false, parents_object);
    REG_TYPE("java.lang.ProcessBuilder", "ProcessBuilder", false, parents_object);
    REG_TYPE("java.lang.StackTraceElement", "StackTraceElement", false, parents_object);
    REG_TYPE("java.lang.Enum", "Enum", false, parents_object);
    REG_TYPE("java.lang.Record", "Record", false, parents_object);
    REG_TYPE("java.lang.Throwable", "Throwable", false, parents_throwable);
    REG_TYPE("java.lang.Exception", "Exception", false, parents_exception);
    REG_TYPE("java.lang.Error", "Error", false, parents_error);
    REG_TYPE("java.lang.RuntimeException", "RuntimeException", false, parents_runtime_exc);
    REG_TYPE("java.lang.NullPointerException", "NullPointerException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.IllegalArgumentException", "IllegalArgumentException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.IllegalStateException", "IllegalStateException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.IndexOutOfBoundsException", "IndexOutOfBoundsException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.ArrayIndexOutOfBoundsException", "ArrayIndexOutOfBoundsException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.ArithmeticException", "ArithmeticException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.ClassCastException", "ClassCastException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.ClassNotFoundException", "ClassNotFoundException", false,
             parents_exception);
    REG_TYPE("java.lang.NumberFormatException", "NumberFormatException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.UnsupportedOperationException", "UnsupportedOperationException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.InterruptedException", "InterruptedException", false, parents_exception);
    REG_TYPE("java.lang.SecurityException", "SecurityException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.lang.NoSuchMethodException", "NoSuchMethodException", false, parents_exception);
    REG_TYPE("java.lang.NoSuchFieldException", "NoSuchFieldException", false, parents_exception);

    /* Annotation-marker types. */
    REG_TYPE("java.lang.Override", "Override", true, no_parents);
    REG_TYPE("java.lang.Deprecated", "Deprecated", true, no_parents);
    REG_TYPE("java.lang.SuppressWarnings", "SuppressWarnings", true, no_parents);
    REG_TYPE("java.lang.FunctionalInterface", "FunctionalInterface", true, no_parents);

    /* ── Object methods ───────────────────────────────────────── */
    REG_METHOD("java.lang.Object", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Object", "hashCode", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Object", "equals", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Object", "getClass", cg_type_named(arena, "java.lang.Class"));
    REG_METHOD("java.lang.Object", "wait", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Object", "notify", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Object", "notifyAll", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Object", "clone", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.lang.Object", "finalize", cg_type_builtin(arena, "void"));

    /* ── String methods ───────────────────────────────────────── */
    REG_METHOD("java.lang.String", "length", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "isBlank", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "charAt", cg_type_builtin(arena, "char"));
    REG_METHOD("java.lang.String", "codePointAt", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "equals", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "equalsIgnoreCase", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "compareTo", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "compareToIgnoreCase", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "indexOf", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "lastIndexOf", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "contains", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "startsWith", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "endsWith", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "matches", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.String", "concat", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "substring", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "trim", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "strip", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "stripLeading", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "stripTrailing", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "toLowerCase", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "toUpperCase", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "replace", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "replaceAll", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "replaceFirst", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "split",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.String")));
    REG_METHOD("java.lang.String", "toCharArray", cg_type_slice(arena, cg_type_builtin(arena, "char")));
    REG_METHOD("java.lang.String", "getBytes", cg_type_slice(arena, cg_type_builtin(arena, "byte")));
    REG_METHOD("java.lang.String", "intern", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "format", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "valueOf", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "join", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "repeat", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "lines", cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.lang.String", "chars", cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.lang.String", "codePoints",
               cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.lang.String", "hashCode", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.String", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.String", "toCharArray", cg_type_slice(arena, cg_type_builtin(arena, "char")));
    REG_CTOR("java.lang.String", "String");

    /* ── StringBuilder / StringBuffer ─────────────────────────── */
    REG_METHOD("java.lang.StringBuilder", "append",
               cg_type_named(arena, "java.lang.StringBuilder"));
    REG_METHOD("java.lang.StringBuilder", "insert",
               cg_type_named(arena, "java.lang.StringBuilder"));
    REG_METHOD("java.lang.StringBuilder", "delete",
               cg_type_named(arena, "java.lang.StringBuilder"));
    REG_METHOD("java.lang.StringBuilder", "deleteCharAt",
               cg_type_named(arena, "java.lang.StringBuilder"));
    REG_METHOD("java.lang.StringBuilder", "replace",
               cg_type_named(arena, "java.lang.StringBuilder"));
    REG_METHOD("java.lang.StringBuilder", "reverse",
               cg_type_named(arena, "java.lang.StringBuilder"));
    REG_METHOD("java.lang.StringBuilder", "toString",
               cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.StringBuilder", "length", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.StringBuilder", "charAt", cg_type_builtin(arena, "char"));
    REG_METHOD("java.lang.StringBuilder", "setLength", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.StringBuilder", "indexOf", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.StringBuilder", "substring",
               cg_type_named(arena, "java.lang.String"));
    REG_CTOR("java.lang.StringBuilder", "StringBuilder");

    REG_METHOD("java.lang.StringBuffer", "append",
               cg_type_named(arena, "java.lang.StringBuffer"));
    REG_METHOD("java.lang.StringBuffer", "toString",
               cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.StringBuffer", "length", cg_type_builtin(arena, "int"));
    REG_CTOR("java.lang.StringBuffer", "StringBuffer");

    /* ── CharSequence ─────────────────────────────────────────── */
    REG_METHOD("java.lang.CharSequence", "length", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.CharSequence", "charAt", cg_type_builtin(arena, "char"));
    REG_METHOD("java.lang.CharSequence", "subSequence",
               cg_type_named(arena, "java.lang.CharSequence"));
    REG_METHOD("java.lang.CharSequence", "toString",
               cg_type_named(arena, "java.lang.String"));

    /* ── Number + boxed types ─────────────────────────────────── */
    REG_METHOD("java.lang.Number", "intValue", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Number", "longValue", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Number", "doubleValue", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Number", "floatValue", cg_type_builtin(arena, "float"));
    REG_METHOD("java.lang.Number", "shortValue", cg_type_builtin(arena, "short"));
    REG_METHOD("java.lang.Number", "byteValue", cg_type_builtin(arena, "byte"));

    REG_METHOD("java.lang.Integer", "intValue", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "parseInt", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "valueOf", cg_type_named(arena, "java.lang.Integer"));
    REG_METHOD("java.lang.Integer", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Integer", "compare", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "compareTo", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "equals", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Integer", "hashCode", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "max", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "min", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "sum", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "bitCount", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Integer", "toBinaryString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Integer", "toHexString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Integer", "toOctalString", cg_type_named(arena, "java.lang.String"));

    REG_METHOD("java.lang.Long", "longValue", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Long", "parseLong", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Long", "valueOf", cg_type_named(arena, "java.lang.Long"));
    REG_METHOD("java.lang.Long", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Long", "compareTo", cg_type_builtin(arena, "int"));

    REG_METHOD("java.lang.Double", "doubleValue", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Double", "parseDouble", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Double", "valueOf", cg_type_named(arena, "java.lang.Double"));
    REG_METHOD("java.lang.Double", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Double", "isNaN", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Double", "isInfinite", cg_type_builtin(arena, "boolean"));

    REG_METHOD("java.lang.Float", "floatValue", cg_type_builtin(arena, "float"));
    REG_METHOD("java.lang.Float", "parseFloat", cg_type_builtin(arena, "float"));
    REG_METHOD("java.lang.Float", "valueOf", cg_type_named(arena, "java.lang.Float"));

    REG_METHOD("java.lang.Boolean", "booleanValue", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Boolean", "parseBoolean", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Boolean", "valueOf", cg_type_named(arena, "java.lang.Boolean"));
    REG_METHOD("java.lang.Boolean", "toString", cg_type_named(arena, "java.lang.String"));

    REG_METHOD("java.lang.Character", "charValue", cg_type_builtin(arena, "char"));
    REG_METHOD("java.lang.Character", "isDigit", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Character", "isLetter", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Character", "isLetterOrDigit", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Character", "isWhitespace", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Character", "isUpperCase", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Character", "isLowerCase", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Character", "toUpperCase", cg_type_builtin(arena, "char"));
    REG_METHOD("java.lang.Character", "toLowerCase", cg_type_builtin(arena, "char"));
    REG_METHOD("java.lang.Character", "getNumericValue", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.Character", "valueOf", cg_type_named(arena, "java.lang.Character"));

    REG_METHOD("java.lang.Byte", "byteValue", cg_type_builtin(arena, "byte"));
    REG_METHOD("java.lang.Byte", "parseByte", cg_type_builtin(arena, "byte"));
    REG_METHOD("java.lang.Byte", "valueOf", cg_type_named(arena, "java.lang.Byte"));

    REG_METHOD("java.lang.Short", "shortValue", cg_type_builtin(arena, "short"));
    REG_METHOD("java.lang.Short", "parseShort", cg_type_builtin(arena, "short"));
    REG_METHOD("java.lang.Short", "valueOf", cg_type_named(arena, "java.lang.Short"));

    /* ── Math ─────────────────────────────────────────────────── */
    REG_METHOD("java.lang.Math", "abs", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "min", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "max", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "sqrt", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "cbrt", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "pow", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "exp", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "log", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "log10", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "sin", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "cos", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "tan", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "asin", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "acos", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "atan", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "atan2", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "floor", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "ceil", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "round", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Math", "random", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "signum", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "hypot", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "floorDiv", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Math", "floorMod", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Math", "addExact", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Math", "subtractExact", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Math", "multiplyExact", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Math", "toRadians", cg_type_builtin(arena, "double"));
    REG_METHOD("java.lang.Math", "toDegrees", cg_type_builtin(arena, "double"));

    /* ── System ───────────────────────────────────────────────── */
    REG_METHOD("java.lang.System", "currentTimeMillis", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.System", "nanoTime", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.System", "exit", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.System", "getenv", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.System", "getProperty", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.System", "setProperty", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.System", "lineSeparator", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.System", "arraycopy", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.System", "identityHashCode", cg_type_builtin(arena, "int"));
    REG_METHOD("java.lang.System", "gc", cg_type_builtin(arena, "void"));

    /* ── Thread ───────────────────────────────────────────────── */
    REG_METHOD("java.lang.Thread", "start", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "run", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "join", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "interrupt", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "isAlive", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Thread", "sleep", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "currentThread", cg_type_named(arena, "java.lang.Thread"));
    REG_METHOD("java.lang.Thread", "yield", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "getName", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Thread", "setName", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Thread", "getId", cg_type_builtin(arena, "long"));
    REG_METHOD("java.lang.Thread", "isInterrupted", cg_type_builtin(arena, "boolean"));

    /* ── Class ────────────────────────────────────────────────── */
    REG_METHOD("java.lang.Class", "getName", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Class", "getSimpleName", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Class", "getCanonicalName", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Class", "isInterface", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Class", "isArray", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Class", "isAssignableFrom", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Class", "isInstance", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.lang.Class", "newInstance", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.lang.Class", "forName", cg_type_named(arena, "java.lang.Class"));
    REG_METHOD("java.lang.Class", "getSuperclass", cg_type_named(arena, "java.lang.Class"));
    REG_METHOD("java.lang.Class", "getInterfaces",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.Class")));

    /* ── Iterable / Iterator ──────────────────────────────────── */
    REG_METHOD("java.lang.Iterable", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_METHOD("java.lang.Iterable", "forEach", cg_type_builtin(arena, "void"));

    /* ── Throwable methods ────────────────────────────────────── */
    REG_METHOD("java.lang.Throwable", "getMessage", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Throwable", "getLocalizedMessage",
               cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Throwable", "getCause", cg_type_named(arena, "java.lang.Throwable"));
    REG_METHOD("java.lang.Throwable", "initCause",
               cg_type_named(arena, "java.lang.Throwable"));
    REG_METHOD("java.lang.Throwable", "printStackTrace", cg_type_builtin(arena, "void"));
    REG_METHOD("java.lang.Throwable", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.lang.Throwable", "getStackTrace",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.StackTraceElement")));

    /* ── AutoCloseable ────────────────────────────────────────── */
    REG_METHOD("java.lang.AutoCloseable", "close", cg_type_builtin(arena, "void"));

    /* ── Comparable ───────────────────────────────────────────── */
    REG_METHOD("java.lang.Comparable", "compareTo", cg_type_builtin(arena, "int"));

    /* ── Runnable ─────────────────────────────────────────────── */
    REG_METHOD("java.lang.Runnable", "run", cg_type_builtin(arena, "void"));

    /* ── java.util ────────────────────────────────────────────── */
    REG_TYPE("java.util.Collection", "Collection", true, parents_collection);
    REG_TYPE("java.util.List", "List", true, parents_list);
    REG_TYPE("java.util.Set", "Set", true, parents_set);
    REG_TYPE("java.util.Queue", "Queue", true, parents_queue);
    REG_TYPE("java.util.Deque", "Deque", true, parents_deque);
    REG_TYPE("java.util.Map", "Map", true, parents_map);
    REG_TYPE("java.util.Map.Entry", "Entry", true, parents_map_entry);
    REG_TYPE("java.util.Iterator", "Iterator", true, parents_iterator);
    REG_TYPE("java.util.ListIterator", "ListIterator", true, parents_iterator);
    REG_TYPE("java.util.Spliterator", "Spliterator", true, no_parents);
    REG_TYPE("java.util.Comparator", "Comparator", true, no_parents);

    REG_TYPE("java.util.ArrayList", "ArrayList", false, parents_arraylist);
    REG_TYPE("java.util.LinkedList", "LinkedList", false, parents_linkedlist);
    REG_TYPE("java.util.Vector", "Vector", false, parents_arraylist);
    REG_TYPE("java.util.Stack", "Stack", false, parents_arraylist);
    REG_TYPE("java.util.HashSet", "HashSet", false, parents_hashset);
    REG_TYPE("java.util.TreeSet", "TreeSet", false, parents_treeset);
    REG_TYPE("java.util.LinkedHashSet", "LinkedHashSet", false, parents_linkedhashset);
    REG_TYPE("java.util.HashMap", "HashMap", false, parents_hashmap);
    REG_TYPE("java.util.TreeMap", "TreeMap", false, parents_treemap);
    REG_TYPE("java.util.LinkedHashMap", "LinkedHashMap", false, parents_linkedhashmap);
    REG_TYPE("java.util.ArrayDeque", "ArrayDeque", false, parents_deque);
    REG_TYPE("java.util.PriorityQueue", "PriorityQueue", false, parents_queue);

    REG_TYPE("java.util.Optional", "Optional", false, parents_object);
    REG_TYPE("java.util.OptionalInt", "OptionalInt", false, parents_object);
    REG_TYPE("java.util.OptionalLong", "OptionalLong", false, parents_object);
    REG_TYPE("java.util.OptionalDouble", "OptionalDouble", false, parents_object);
    REG_TYPE("java.util.Date", "Date", false, parents_object);
    REG_TYPE("java.util.Calendar", "Calendar", false, parents_object);
    REG_TYPE("java.util.GregorianCalendar", "GregorianCalendar", false,
             parents_gregorian_calendar);
    REG_TYPE("java.util.TimeZone", "TimeZone", false, parents_object);
    REG_TYPE("java.util.Locale", "Locale", false, parents_object);
    REG_TYPE("java.util.UUID", "UUID", false, parents_object);
    REG_TYPE("java.util.Random", "Random", false, parents_object);
    REG_TYPE("java.util.Scanner", "Scanner", false, parents_object);
    REG_TYPE("java.util.Arrays", "Arrays", false, parents_object);
    REG_TYPE("java.util.Collections", "Collections", false, parents_object);
    REG_TYPE("java.util.Objects", "Objects", false, parents_object);
    REG_TYPE("java.util.Properties", "Properties", false, parents_hashmap);
    REG_TYPE("java.util.regex.Pattern", "Pattern", false, parents_object);
    REG_TYPE("java.util.regex.Matcher", "Matcher", false, parents_object);

    /* ── Collection methods ───────────────────────────────────── */
    REG_METHOD("java.util.Collection", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Collection", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "contains", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "containsAll", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_METHOD("java.util.Collection", "toArray",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.Object")));
    REG_METHOD("java.util.Collection", "add", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "addAll", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "remove", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "removeAll", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "retainAll", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Collection", "clear", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Collection", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.Collection", "parallelStream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.Collection", "forEach", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Collection", "removeIf", cg_type_builtin(arena, "boolean"));

    /* ── List methods ─────────────────────────────────────────── */
    REG_METHOD("java.util.List", "get", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.List", "set", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.List", "add", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.List", "remove", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.List", "indexOf", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.List", "lastIndexOf", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.List", "subList", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.List", "of", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.List", "copyOf", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.List", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.List", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.List", "contains", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.List", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_METHOD("java.util.List", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.List", "forEach", cg_type_builtin(arena, "void"));

    /* ── ArrayList ────────────────────────────────────────────── */
    REG_METHOD("java.util.ArrayList", "get", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.ArrayList", "set", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.ArrayList", "add", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.ArrayList", "remove", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.ArrayList", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.ArrayList", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.ArrayList", "indexOf", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.ArrayList", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_METHOD("java.util.ArrayList", "clear", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.ArrayList", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.ArrayList", "toArray",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.Object")));
    REG_METHOD("java.util.ArrayList", "subList", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.ArrayList", "trimToSize", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.ArrayList", "ensureCapacity", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.ArrayList", "forEach", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.ArrayList", "removeIf", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.List", "removeIf", cg_type_builtin(arena, "boolean"));
    REG_CTOR("java.util.ArrayList", "ArrayList");

    REG_METHOD("java.util.LinkedList", "addFirst", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.LinkedList", "addLast", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.LinkedList", "removeFirst", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.LinkedList", "removeLast", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.LinkedList", "getFirst", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.LinkedList", "getLast", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.LinkedList", "peek", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.LinkedList", "poll", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.LinkedList", "offer", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.LinkedList", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.LinkedList", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_CTOR("java.util.LinkedList", "LinkedList");

    /* ── Set methods ──────────────────────────────────────────── */
    REG_METHOD("java.util.Set", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Set", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Set", "contains", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Set", "add", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Set", "remove", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Set", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_METHOD("java.util.Set", "of", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Set", "copyOf", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Set", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.Set", "forEach", cg_type_builtin(arena, "void"));

    REG_METHOD("java.util.HashSet", "add", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashSet", "remove", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashSet", "contains", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashSet", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.HashSet", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashSet", "iterator", cg_type_named(arena, "java.util.Iterator"));
    REG_METHOD("java.util.HashSet", "clear", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.HashSet", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_CTOR("java.util.HashSet", "HashSet");

    REG_METHOD("java.util.TreeSet", "first", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.TreeSet", "last", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.TreeSet", "headSet", cg_type_named(arena, "java.util.SortedSet"));
    REG_METHOD("java.util.TreeSet", "tailSet", cg_type_named(arena, "java.util.SortedSet"));
    REG_CTOR("java.util.TreeSet", "TreeSet");

    REG_CTOR("java.util.LinkedHashSet", "LinkedHashSet");

    /* ── Map methods ──────────────────────────────────────────── */
    REG_METHOD("java.util.Map", "get", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "put", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "remove", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "containsKey", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Map", "containsValue", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Map", "keySet", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Map", "values", cg_type_named(arena, "java.util.Collection"));
    REG_METHOD("java.util.Map", "entrySet", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Map", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Map", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Map", "putAll", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Map", "clear", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Map", "getOrDefault", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "putIfAbsent", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "computeIfAbsent", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "compute", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "merge", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map", "of", cg_type_named(arena, "java.util.Map"));
    REG_METHOD("java.util.Map", "copyOf", cg_type_named(arena, "java.util.Map"));
    REG_METHOD("java.util.Map", "ofEntries", cg_type_named(arena, "java.util.Map"));
    REG_METHOD("java.util.Map", "entry", cg_type_named(arena, "java.util.Map.Entry"));
    REG_METHOD("java.util.Map", "forEach", cg_type_builtin(arena, "void"));

    REG_METHOD("java.util.Map.Entry", "getKey", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map.Entry", "getValue", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Map.Entry", "setValue", cg_type_named(arena, "java.lang.Object"));

    REG_METHOD("java.util.HashMap", "get", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.HashMap", "put", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.HashMap", "remove", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.HashMap", "containsKey", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashMap", "containsValue", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashMap", "size", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.HashMap", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.HashMap", "keySet", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.HashMap", "values", cg_type_named(arena, "java.util.Collection"));
    REG_METHOD("java.util.HashMap", "entrySet", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.HashMap", "clear", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.HashMap", "getOrDefault", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.HashMap", "putIfAbsent", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.HashMap", "forEach", cg_type_builtin(arena, "void"));
    REG_CTOR("java.util.HashMap", "HashMap");

    REG_METHOD("java.util.TreeMap", "firstKey", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.TreeMap", "lastKey", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.TreeMap", "headMap", cg_type_named(arena, "java.util.SortedMap"));
    REG_METHOD("java.util.TreeMap", "tailMap", cg_type_named(arena, "java.util.SortedMap"));
    REG_CTOR("java.util.TreeMap", "TreeMap");

    REG_CTOR("java.util.LinkedHashMap", "LinkedHashMap");

    /* ── Iterator methods ─────────────────────────────────────── */
    REG_METHOD("java.util.Iterator", "hasNext", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Iterator", "next", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Iterator", "remove", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Iterator", "forEachRemaining", cg_type_builtin(arena, "void"));

    /* ── Optional ─────────────────────────────────────────────── */
    REG_METHOD("java.util.Optional", "get", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Optional", "isPresent", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Optional", "isEmpty", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Optional", "orElse", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Optional", "orElseGet", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Optional", "orElseThrow", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Optional", "ifPresent", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Optional", "ifPresentOrElse", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Optional", "map", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.Optional", "flatMap", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.Optional", "filter", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.Optional", "of", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.Optional", "ofNullable", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.Optional", "empty", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.Optional", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));

    /* ── Arrays / Collections / Objects helpers ───────────────── */
    REG_METHOD("java.util.Arrays", "asList", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.Arrays", "stream",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.Arrays", "sort", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Arrays", "binarySearch", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Arrays", "fill", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Arrays", "copyOf",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.Object")));
    REG_METHOD("java.util.Arrays", "copyOfRange",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.Object")));
    REG_METHOD("java.util.Arrays", "equals", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Arrays", "hashCode", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Arrays", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Arrays", "deepEquals", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Arrays", "deepToString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Arrays", "deepHashCode", cg_type_builtin(arena, "int"));

    REG_METHOD("java.util.Collections", "sort", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Collections", "reverse", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Collections", "shuffle", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Collections", "min", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Collections", "max", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Collections", "emptyList", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.Collections", "emptySet", cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Collections", "emptyMap", cg_type_named(arena, "java.util.Map"));
    REG_METHOD("java.util.Collections", "singletonList",
               cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.Collections", "singleton",
               cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Collections", "unmodifiableList",
               cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.Collections", "unmodifiableSet",
               cg_type_named(arena, "java.util.Set"));
    REG_METHOD("java.util.Collections", "unmodifiableMap",
               cg_type_named(arena, "java.util.Map"));
    REG_METHOD("java.util.Collections", "frequency", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Collections", "binarySearch", cg_type_builtin(arena, "int"));

    REG_METHOD("java.util.Objects", "equals", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Objects", "hashCode", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Objects", "hash", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Objects", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Objects", "isNull", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Objects", "nonNull", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Objects", "requireNonNull", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.Objects", "requireNonNullElse",
               cg_type_named(arena, "java.lang.Object"));

    /* ── UUID, Random, Scanner ────────────────────────────────── */
    REG_METHOD("java.util.UUID", "randomUUID", cg_type_named(arena, "java.util.UUID"));
    REG_METHOD("java.util.UUID", "fromString", cg_type_named(arena, "java.util.UUID"));
    REG_METHOD("java.util.UUID", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.UUID", "getMostSignificantBits", cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.UUID", "getLeastSignificantBits", cg_type_builtin(arena, "long"));
    REG_CTOR("java.util.UUID", "UUID");

    REG_METHOD("java.util.Random", "nextInt", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Random", "nextLong", cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.Random", "nextDouble", cg_type_builtin(arena, "double"));
    REG_METHOD("java.util.Random", "nextFloat", cg_type_builtin(arena, "float"));
    REG_METHOD("java.util.Random", "nextBoolean", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Random", "nextGaussian", cg_type_builtin(arena, "double"));
    REG_METHOD("java.util.Random", "setSeed", cg_type_builtin(arena, "void"));
    REG_CTOR("java.util.Random", "Random");

    REG_METHOD("java.util.Scanner", "next", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Scanner", "nextLine", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Scanner", "nextInt", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Scanner", "nextLong", cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.Scanner", "nextDouble", cg_type_builtin(arena, "double"));
    REG_METHOD("java.util.Scanner", "hasNext", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Scanner", "hasNextLine", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Scanner", "hasNextInt", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Scanner", "close", cg_type_builtin(arena, "void"));
    REG_CTOR("java.util.Scanner", "Scanner");

    /* ── Locale / Date / Calendar / TimeZone ──────────────────── */
    REG_METHOD("java.util.Locale", "getLanguage", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Locale", "getCountry", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Locale", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.Locale", "getDefault", cg_type_named(arena, "java.util.Locale"));
    REG_CTOR("java.util.Locale", "Locale");

    REG_METHOD("java.util.Date", "getTime", cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.Date", "setTime", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Date", "before", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Date", "after", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.Date", "compareTo", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Date", "toString", cg_type_named(arena, "java.lang.String"));
    REG_CTOR("java.util.Date", "Date");

    REG_METHOD("java.util.Calendar", "getInstance", cg_type_named(arena, "java.util.Calendar"));
    REG_METHOD("java.util.Calendar", "getTime", cg_type_named(arena, "java.util.Date"));
    REG_METHOD("java.util.Calendar", "set", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.Calendar", "get", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.Calendar", "add", cg_type_builtin(arena, "void"));

    REG_METHOD("java.util.TimeZone", "getDefault", cg_type_named(arena, "java.util.TimeZone"));
    REG_METHOD("java.util.TimeZone", "getID", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.TimeZone", "getTimeZone", cg_type_named(arena, "java.util.TimeZone"));

    /* ── regex ────────────────────────────────────────────────── */
    REG_METHOD("java.util.regex.Pattern", "compile",
               cg_type_named(arena, "java.util.regex.Pattern"));
    REG_METHOD("java.util.regex.Pattern", "matcher",
               cg_type_named(arena, "java.util.regex.Matcher"));
    REG_METHOD("java.util.regex.Pattern", "matches", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.regex.Pattern", "split",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.String")));
    REG_METHOD("java.util.regex.Pattern", "pattern", cg_type_named(arena, "java.lang.String"));

    REG_METHOD("java.util.regex.Matcher", "matches", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.regex.Matcher", "find", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.regex.Matcher", "group", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.regex.Matcher", "groupCount", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.regex.Matcher", "start", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.regex.Matcher", "end", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.regex.Matcher", "replaceAll", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.util.regex.Matcher", "replaceFirst",
               cg_type_named(arena, "java.lang.String"));

    /* ── java.io ──────────────────────────────────────────────── */
    REG_TYPE("java.io.InputStream", "InputStream", false, parents_inputstream);
    REG_TYPE("java.io.OutputStream", "OutputStream", false, parents_outputstream);
    REG_TYPE("java.io.Reader", "Reader", false, parents_reader);
    REG_TYPE("java.io.Writer", "Writer", false, parents_writer);
    REG_TYPE("java.io.BufferedReader", "BufferedReader", false, parents_buffered_reader);
    REG_TYPE("java.io.BufferedWriter", "BufferedWriter", false, parents_buffered_writer);
    REG_TYPE("java.io.PrintStream", "PrintStream", false, parents_print_stream);
    REG_TYPE("java.io.PrintWriter", "PrintWriter", false, parents_print_writer);
    REG_TYPE("java.io.FileInputStream", "FileInputStream", false, parents_file_input_stream);
    REG_TYPE("java.io.FileOutputStream", "FileOutputStream", false, parents_file_output_stream);
    REG_TYPE("java.io.FileReader", "FileReader", false, parents_file_reader);
    REG_TYPE("java.io.FileWriter", "FileWriter", false, parents_file_writer);
    REG_TYPE("java.io.File", "File", false, parents_object);
    REG_TYPE("java.io.IOException", "IOException", false, parents_io_exception);
    REG_TYPE("java.io.FileNotFoundException", "FileNotFoundException", false,
             parents_file_not_found_exc);
    REG_TYPE("java.io.UncheckedIOException", "UncheckedIOException", false,
             parents_runtime_exc_chain);
    REG_TYPE("java.io.Serializable", "Serializable", true, no_parents);
    REG_TYPE("java.io.Closeable", "Closeable", true,
             parents_closeable);
    REG_TYPE("java.io.Flushable", "Flushable", true, no_parents);

    REG_METHOD("java.io.PrintStream", "println", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintStream", "print", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintStream", "printf", cg_type_named(arena, "java.io.PrintStream"));
    REG_METHOD("java.io.PrintStream", "format", cg_type_named(arena, "java.io.PrintStream"));
    REG_METHOD("java.io.PrintStream", "write", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintStream", "flush", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintStream", "close", cg_type_builtin(arena, "void"));

    REG_METHOD("java.io.PrintWriter", "println", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintWriter", "print", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintWriter", "printf", cg_type_named(arena, "java.io.PrintWriter"));
    REG_METHOD("java.io.PrintWriter", "flush", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.PrintWriter", "close", cg_type_builtin(arena, "void"));
    REG_CTOR("java.io.PrintWriter", "PrintWriter");

    REG_METHOD("java.io.InputStream", "read", cg_type_builtin(arena, "int"));
    REG_METHOD("java.io.InputStream", "close", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.InputStream", "available", cg_type_builtin(arena, "int"));
    REG_METHOD("java.io.InputStream", "skip", cg_type_builtin(arena, "long"));
    REG_METHOD("java.io.InputStream", "readAllBytes",
               cg_type_slice(arena, cg_type_builtin(arena, "byte")));

    REG_METHOD("java.io.OutputStream", "write", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.OutputStream", "flush", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.OutputStream", "close", cg_type_builtin(arena, "void"));

    REG_METHOD("java.io.Reader", "read", cg_type_builtin(arena, "int"));
    REG_METHOD("java.io.Reader", "close", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.Reader", "ready", cg_type_builtin(arena, "boolean"));

    REG_METHOD("java.io.Writer", "write", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.Writer", "flush", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.Writer", "close", cg_type_builtin(arena, "void"));

    REG_METHOD("java.io.BufferedReader", "readLine", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.io.BufferedReader", "lines",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.io.BufferedReader", "close", cg_type_builtin(arena, "void"));
    REG_CTOR("java.io.BufferedReader", "BufferedReader");

    REG_METHOD("java.io.BufferedWriter", "write", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.BufferedWriter", "newLine", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.BufferedWriter", "flush", cg_type_builtin(arena, "void"));
    REG_METHOD("java.io.BufferedWriter", "close", cg_type_builtin(arena, "void"));
    REG_CTOR("java.io.BufferedWriter", "BufferedWriter");

    REG_METHOD("java.io.File", "exists", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "isFile", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "isDirectory", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "canRead", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "canWrite", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "getName", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.io.File", "getPath", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.io.File", "getAbsolutePath", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.io.File", "getCanonicalPath", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.io.File", "getParent", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.io.File", "getParentFile", cg_type_named(arena, "java.io.File"));
    REG_METHOD("java.io.File", "length", cg_type_builtin(arena, "long"));
    REG_METHOD("java.io.File", "lastModified", cg_type_builtin(arena, "long"));
    REG_METHOD("java.io.File", "mkdir", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "mkdirs", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "delete", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "renameTo", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.io.File", "list",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.String")));
    REG_METHOD("java.io.File", "listFiles",
               cg_type_slice(arena, cg_type_named(arena, "java.io.File")));
    REG_METHOD("java.io.File", "toPath", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.io.File", "toURI", cg_type_named(arena, "java.net.URI"));
    REG_CTOR("java.io.File", "File");

    /* ── java.nio.file ───────────────────────────────────────── */
    REG_TYPE("java.nio.file.Path", "Path", true, no_parents);
    REG_TYPE("java.nio.file.Paths", "Paths", false, parents_object);
    REG_TYPE("java.nio.file.Files", "Files", false, parents_object);

    REG_METHOD("java.nio.file.Path", "getFileName", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "getParent", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "getRoot", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "resolve", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "resolveSibling",
               cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "relativize", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "normalize", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "toAbsolutePath",
               cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Path", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.nio.file.Path", "toFile", cg_type_named(arena, "java.io.File"));
    REG_METHOD("java.nio.file.Path", "of", cg_type_named(arena, "java.nio.file.Path"));

    REG_METHOD("java.nio.file.Paths", "get", cg_type_named(arena, "java.nio.file.Path"));

    REG_METHOD("java.nio.file.Files", "exists", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.nio.file.Files", "isDirectory", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.nio.file.Files", "isRegularFile", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.nio.file.Files", "readString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.nio.file.Files", "writeString", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Files", "readAllLines", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.nio.file.Files", "readAllBytes",
               cg_type_slice(arena, cg_type_builtin(arena, "byte")));
    REG_METHOD("java.nio.file.Files", "lines",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.nio.file.Files", "list",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.nio.file.Files", "walk",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.nio.file.Files", "createDirectory",
               cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Files", "createDirectories",
               cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Files", "createFile",
               cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Files", "delete", cg_type_builtin(arena, "void"));
    REG_METHOD("java.nio.file.Files", "deleteIfExists", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.nio.file.Files", "copy", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Files", "move", cg_type_named(arena, "java.nio.file.Path"));
    REG_METHOD("java.nio.file.Files", "size", cg_type_builtin(arena, "long"));

    /* ── java.util.function (the 21 functional interfaces) ──── */
    REG_TYPE("java.util.function.Function", "Function", true, no_parents);
    REG_TYPE("java.util.function.BiFunction", "BiFunction", true, no_parents);
    REG_TYPE("java.util.function.Predicate", "Predicate", true, no_parents);
    REG_TYPE("java.util.function.BiPredicate", "BiPredicate", true, no_parents);
    REG_TYPE("java.util.function.Consumer", "Consumer", true, no_parents);
    REG_TYPE("java.util.function.BiConsumer", "BiConsumer", true, no_parents);
    REG_TYPE("java.util.function.Supplier", "Supplier", true, no_parents);
    REG_TYPE("java.util.function.UnaryOperator", "UnaryOperator", true,
             parents_unary_operator);
    REG_TYPE("java.util.function.BinaryOperator", "BinaryOperator", true,
             parents_binary_operator);
    REG_TYPE("java.util.function.IntFunction", "IntFunction", true, no_parents);
    REG_TYPE("java.util.function.LongFunction", "LongFunction", true, no_parents);
    REG_TYPE("java.util.function.DoubleFunction", "DoubleFunction", true, no_parents);
    REG_TYPE("java.util.function.IntPredicate", "IntPredicate", true, no_parents);
    REG_TYPE("java.util.function.LongPredicate", "LongPredicate", true, no_parents);
    REG_TYPE("java.util.function.DoublePredicate", "DoublePredicate", true, no_parents);
    REG_TYPE("java.util.function.IntConsumer", "IntConsumer", true, no_parents);
    REG_TYPE("java.util.function.LongConsumer", "LongConsumer", true, no_parents);
    REG_TYPE("java.util.function.DoubleConsumer", "DoubleConsumer", true, no_parents);
    REG_TYPE("java.util.function.IntSupplier", "IntSupplier", true, no_parents);
    REG_TYPE("java.util.function.LongSupplier", "LongSupplier", true, no_parents);
    REG_TYPE("java.util.function.DoubleSupplier", "DoubleSupplier", true, no_parents);
    REG_TYPE("java.util.function.BooleanSupplier", "BooleanSupplier", true, no_parents);
    REG_TYPE("java.util.function.ToIntFunction", "ToIntFunction", true, no_parents);
    REG_TYPE("java.util.function.ToLongFunction", "ToLongFunction", true, no_parents);
    REG_TYPE("java.util.function.ToDoubleFunction", "ToDoubleFunction", true, no_parents);

    REG_METHOD("java.util.function.Function", "apply", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.function.Function", "compose",
               cg_type_named(arena, "java.util.function.Function"));
    REG_METHOD("java.util.function.Function", "andThen",
               cg_type_named(arena, "java.util.function.Function"));
    REG_METHOD("java.util.function.Function", "identity",
               cg_type_named(arena, "java.util.function.Function"));

    REG_METHOD("java.util.function.BiFunction", "apply",
               cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.function.BiFunction", "andThen",
               cg_type_named(arena, "java.util.function.BiFunction"));

    REG_METHOD("java.util.function.Predicate", "test", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.function.Predicate", "and",
               cg_type_named(arena, "java.util.function.Predicate"));
    REG_METHOD("java.util.function.Predicate", "or",
               cg_type_named(arena, "java.util.function.Predicate"));
    REG_METHOD("java.util.function.Predicate", "negate",
               cg_type_named(arena, "java.util.function.Predicate"));
    REG_METHOD("java.util.function.Predicate", "isEqual",
               cg_type_named(arena, "java.util.function.Predicate"));

    REG_METHOD("java.util.function.Consumer", "accept", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.function.Consumer", "andThen",
               cg_type_named(arena, "java.util.function.Consumer"));

    REG_METHOD("java.util.function.Supplier", "get", cg_type_named(arena, "java.lang.Object"));

    REG_METHOD("java.util.function.UnaryOperator", "identity",
               cg_type_named(arena, "java.util.function.UnaryOperator"));
    REG_METHOD("java.util.function.UnaryOperator", "apply",
               cg_type_named(arena, "java.lang.Object"));

    /* ── java.util.stream ────────────────────────────────────── */
    REG_TYPE("java.util.stream.Stream", "Stream", true, no_parents);
    REG_TYPE("java.util.stream.IntStream", "IntStream", true, no_parents);
    REG_TYPE("java.util.stream.LongStream", "LongStream", true, no_parents);
    REG_TYPE("java.util.stream.DoubleStream", "DoubleStream", true, no_parents);
    REG_TYPE("java.util.stream.Collectors", "Collectors", false, parents_object);
    REG_TYPE("java.util.stream.Collector", "Collector", true, no_parents);

    REG_METHOD("java.util.stream.Stream", "filter",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "map",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "flatMap",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "mapToInt",
               cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.util.stream.Stream", "mapToLong",
               cg_type_named(arena, "java.util.stream.LongStream"));
    REG_METHOD("java.util.stream.Stream", "mapToDouble",
               cg_type_named(arena, "java.util.stream.DoubleStream"));
    REG_METHOD("java.util.stream.Stream", "sorted",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "distinct",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "limit",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "skip",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "peek",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "forEach", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.stream.Stream", "forEachOrdered", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.stream.Stream", "toArray",
               cg_type_slice(arena, cg_type_named(arena, "java.lang.Object")));
    REG_METHOD("java.util.stream.Stream", "toList", cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.stream.Stream", "reduce", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.stream.Stream", "collect", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.stream.Stream", "count", cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.stream.Stream", "anyMatch", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.stream.Stream", "allMatch", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.stream.Stream", "noneMatch", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.stream.Stream", "findFirst",
               cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.stream.Stream", "findAny",
               cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.stream.Stream", "min", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.stream.Stream", "max", cg_type_named(arena, "java.util.Optional"));
    REG_METHOD("java.util.stream.Stream", "of",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "empty",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "concat",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "iterate",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.Stream", "generate",
               cg_type_named(arena, "java.util.stream.Stream"));

    REG_METHOD("java.util.stream.IntStream", "sum", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.stream.IntStream", "average",
               cg_type_named(arena, "java.util.OptionalDouble"));
    REG_METHOD("java.util.stream.IntStream", "max",
               cg_type_named(arena, "java.util.OptionalInt"));
    REG_METHOD("java.util.stream.IntStream", "min",
               cg_type_named(arena, "java.util.OptionalInt"));
    REG_METHOD("java.util.stream.IntStream", "count", cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.stream.IntStream", "boxed",
               cg_type_named(arena, "java.util.stream.Stream"));
    REG_METHOD("java.util.stream.IntStream", "filter",
               cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.util.stream.IntStream", "map",
               cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.util.stream.IntStream", "range",
               cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.util.stream.IntStream", "rangeClosed",
               cg_type_named(arena, "java.util.stream.IntStream"));
    REG_METHOD("java.util.stream.IntStream", "of",
               cg_type_named(arena, "java.util.stream.IntStream"));

    REG_METHOD("java.util.stream.Collectors", "toList",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "toSet",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "toMap",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "joining",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "groupingBy",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "partitioningBy",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "counting",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "summingInt",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "averagingDouble",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "mapping",
               cg_type_named(arena, "java.util.stream.Collector"));
    REG_METHOD("java.util.stream.Collectors", "reducing",
               cg_type_named(arena, "java.util.stream.Collector"));

    /* ── java.util.concurrent ────────────────────────────────── */
    REG_TYPE("java.util.concurrent.ExecutorService", "ExecutorService", true, no_parents);
    REG_TYPE("java.util.concurrent.Executors", "Executors", false, parents_object);
    REG_TYPE("java.util.concurrent.Future", "Future", true, no_parents);
    REG_TYPE("java.util.concurrent.CompletableFuture", "CompletableFuture", false,
             parents_completable_future);
    REG_TYPE("java.util.concurrent.ConcurrentHashMap", "ConcurrentHashMap", false,
             parents_concurrent_hashmap);
    REG_TYPE("java.util.concurrent.ConcurrentMap", "ConcurrentMap", true, parents_map);
    REG_TYPE("java.util.concurrent.TimeUnit", "TimeUnit", false, parents_object);
    REG_TYPE("java.util.concurrent.atomic.AtomicInteger", "AtomicInteger", false, parents_object);
    REG_TYPE("java.util.concurrent.atomic.AtomicLong", "AtomicLong", false, parents_object);
    REG_TYPE("java.util.concurrent.atomic.AtomicBoolean", "AtomicBoolean", false, parents_object);
    REG_TYPE("java.util.concurrent.atomic.AtomicReference", "AtomicReference", false,
             parents_object);
    REG_TYPE("java.util.concurrent.locks.Lock", "Lock", true, no_parents);
    REG_TYPE("java.util.concurrent.locks.ReentrantLock", "ReentrantLock", false,
             parents_reentrant_lock);
    REG_TYPE("java.util.concurrent.locks.ReadWriteLock", "ReadWriteLock", true, no_parents);

    REG_METHOD("java.util.concurrent.ExecutorService", "submit",
               cg_type_named(arena, "java.util.concurrent.Future"));
    REG_METHOD("java.util.concurrent.ExecutorService", "execute", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.concurrent.ExecutorService", "shutdown", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.concurrent.ExecutorService", "shutdownNow",
               cg_type_named(arena, "java.util.List"));
    REG_METHOD("java.util.concurrent.ExecutorService", "awaitTermination",
               cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.concurrent.ExecutorService", "isShutdown",
               cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.concurrent.ExecutorService", "isTerminated",
               cg_type_builtin(arena, "boolean"));

    REG_METHOD("java.util.concurrent.Executors", "newFixedThreadPool",
               cg_type_named(arena, "java.util.concurrent.ExecutorService"));
    REG_METHOD("java.util.concurrent.Executors", "newSingleThreadExecutor",
               cg_type_named(arena, "java.util.concurrent.ExecutorService"));
    REG_METHOD("java.util.concurrent.Executors", "newCachedThreadPool",
               cg_type_named(arena, "java.util.concurrent.ExecutorService"));
    REG_METHOD("java.util.concurrent.Executors", "newScheduledThreadPool",
               cg_type_named(arena, "java.util.concurrent.ExecutorService"));

    REG_METHOD("java.util.concurrent.Future", "get", cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.concurrent.Future", "isDone", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.concurrent.Future", "cancel", cg_type_builtin(arena, "boolean"));

    REG_METHOD("java.util.concurrent.CompletableFuture", "thenApply",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "thenAccept",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "thenCompose",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "thenCombine",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "exceptionally",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "join",
               cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "supplyAsync",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "runAsync",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "completedFuture",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "allOf",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));
    REG_METHOD("java.util.concurrent.CompletableFuture", "anyOf",
               cg_type_named(arena, "java.util.concurrent.CompletableFuture"));

    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "get", cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "set", cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "incrementAndGet",
               cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "decrementAndGet",
               cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "getAndIncrement",
               cg_type_builtin(arena, "int"));
    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "compareAndSet",
               cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.util.concurrent.atomic.AtomicInteger", "addAndGet",
               cg_type_builtin(arena, "int"));
    REG_CTOR("java.util.concurrent.atomic.AtomicInteger", "AtomicInteger");

    REG_METHOD("java.util.concurrent.atomic.AtomicLong", "get",
               cg_type_builtin(arena, "long"));
    REG_METHOD("java.util.concurrent.atomic.AtomicLong", "incrementAndGet",
               cg_type_builtin(arena, "long"));
    REG_CTOR("java.util.concurrent.atomic.AtomicLong", "AtomicLong");

    REG_METHOD("java.util.concurrent.atomic.AtomicReference", "get",
               cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.concurrent.atomic.AtomicReference", "set",
               cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.concurrent.atomic.AtomicReference", "compareAndSet",
               cg_type_builtin(arena, "boolean"));
    REG_CTOR("java.util.concurrent.atomic.AtomicReference", "AtomicReference");

    REG_METHOD("java.util.concurrent.locks.ReentrantLock", "lock",
               cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.concurrent.locks.ReentrantLock", "unlock",
               cg_type_builtin(arena, "void"));
    REG_METHOD("java.util.concurrent.locks.ReentrantLock", "tryLock",
               cg_type_builtin(arena, "boolean"));
    REG_CTOR("java.util.concurrent.locks.ReentrantLock", "ReentrantLock");

    REG_METHOD("java.util.concurrent.ConcurrentHashMap", "put",
               cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.concurrent.ConcurrentHashMap", "get",
               cg_type_named(arena, "java.lang.Object"));
    REG_METHOD("java.util.concurrent.ConcurrentHashMap", "putIfAbsent",
               cg_type_named(arena, "java.lang.Object"));
    REG_CTOR("java.util.concurrent.ConcurrentHashMap", "ConcurrentHashMap");

    /* ── java.time ───────────────────────────────────────────── */
    REG_TYPE("java.time.LocalDate", "LocalDate", false, parents_object);
    REG_TYPE("java.time.LocalTime", "LocalTime", false, parents_object);
    REG_TYPE("java.time.LocalDateTime", "LocalDateTime", false, parents_object);
    REG_TYPE("java.time.ZonedDateTime", "ZonedDateTime", false, parents_object);
    REG_TYPE("java.time.OffsetDateTime", "OffsetDateTime", false, parents_object);
    REG_TYPE("java.time.Instant", "Instant", false, parents_object);
    REG_TYPE("java.time.Duration", "Duration", false, parents_object);
    REG_TYPE("java.time.Period", "Period", false, parents_object);
    REG_TYPE("java.time.ZoneId", "ZoneId", false, parents_object);
    REG_TYPE("java.time.format.DateTimeFormatter", "DateTimeFormatter", false, parents_object);

    REG_METHOD("java.time.LocalDate", "now", cg_type_named(arena, "java.time.LocalDate"));
    REG_METHOD("java.time.LocalDate", "of", cg_type_named(arena, "java.time.LocalDate"));
    REG_METHOD("java.time.LocalDate", "parse", cg_type_named(arena, "java.time.LocalDate"));
    REG_METHOD("java.time.LocalDate", "plusDays", cg_type_named(arena, "java.time.LocalDate"));
    REG_METHOD("java.time.LocalDate", "minusDays", cg_type_named(arena, "java.time.LocalDate"));
    REG_METHOD("java.time.LocalDate", "getYear", cg_type_builtin(arena, "int"));
    REG_METHOD("java.time.LocalDate", "getMonth", cg_type_named(arena, "java.time.Month"));
    REG_METHOD("java.time.LocalDate", "getDayOfMonth", cg_type_builtin(arena, "int"));
    REG_METHOD("java.time.LocalDate", "getDayOfWeek",
               cg_type_named(arena, "java.time.DayOfWeek"));
    REG_METHOD("java.time.LocalDate", "isAfter", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.time.LocalDate", "isBefore", cg_type_builtin(arena, "boolean"));
    REG_METHOD("java.time.LocalDate", "format", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.time.LocalDate", "toString", cg_type_named(arena, "java.lang.String"));

    REG_METHOD("java.time.LocalDateTime", "now",
               cg_type_named(arena, "java.time.LocalDateTime"));
    REG_METHOD("java.time.LocalDateTime", "of",
               cg_type_named(arena, "java.time.LocalDateTime"));
    REG_METHOD("java.time.LocalDateTime", "parse",
               cg_type_named(arena, "java.time.LocalDateTime"));
    REG_METHOD("java.time.LocalDateTime", "plusHours",
               cg_type_named(arena, "java.time.LocalDateTime"));
    REG_METHOD("java.time.LocalDateTime", "minusHours",
               cg_type_named(arena, "java.time.LocalDateTime"));
    REG_METHOD("java.time.LocalDateTime", "format", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.time.LocalDateTime", "toString", cg_type_named(arena, "java.lang.String"));

    REG_METHOD("java.time.Instant", "now", cg_type_named(arena, "java.time.Instant"));
    REG_METHOD("java.time.Instant", "ofEpochMilli", cg_type_named(arena, "java.time.Instant"));
    REG_METHOD("java.time.Instant", "ofEpochSecond", cg_type_named(arena, "java.time.Instant"));
    REG_METHOD("java.time.Instant", "toEpochMilli", cg_type_builtin(arena, "long"));
    REG_METHOD("java.time.Instant", "getEpochSecond", cg_type_builtin(arena, "long"));
    REG_METHOD("java.time.Instant", "plus", cg_type_named(arena, "java.time.Instant"));
    REG_METHOD("java.time.Instant", "minus", cg_type_named(arena, "java.time.Instant"));

    REG_METHOD("java.time.Duration", "ofSeconds", cg_type_named(arena, "java.time.Duration"));
    REG_METHOD("java.time.Duration", "ofMillis", cg_type_named(arena, "java.time.Duration"));
    REG_METHOD("java.time.Duration", "ofMinutes", cg_type_named(arena, "java.time.Duration"));
    REG_METHOD("java.time.Duration", "ofHours", cg_type_named(arena, "java.time.Duration"));
    REG_METHOD("java.time.Duration", "ofDays", cg_type_named(arena, "java.time.Duration"));
    REG_METHOD("java.time.Duration", "between", cg_type_named(arena, "java.time.Duration"));
    REG_METHOD("java.time.Duration", "toMillis", cg_type_builtin(arena, "long"));
    REG_METHOD("java.time.Duration", "toSeconds", cg_type_builtin(arena, "long"));
    REG_METHOD("java.time.Duration", "toMinutes", cg_type_builtin(arena, "long"));

    REG_METHOD("java.time.ZoneId", "of", cg_type_named(arena, "java.time.ZoneId"));
    REG_METHOD("java.time.ZoneId", "systemDefault", cg_type_named(arena, "java.time.ZoneId"));
    REG_METHOD("java.time.ZoneId", "getId", cg_type_named(arena, "java.lang.String"));

    REG_METHOD("java.time.format.DateTimeFormatter", "ofPattern",
               cg_type_named(arena, "java.time.format.DateTimeFormatter"));
    REG_METHOD("java.time.format.DateTimeFormatter", "format",
               cg_type_named(arena, "java.lang.String"));

    /* ── java.net (minimal) ──────────────────────────────────── */
    REG_TYPE("java.net.URI", "URI", false, parents_object);
    REG_TYPE("java.net.URL", "URL", false, parents_object);
    REG_METHOD("java.net.URI", "create", cg_type_named(arena, "java.net.URI"));
    REG_METHOD("java.net.URI", "toString", cg_type_named(arena, "java.lang.String"));
    REG_METHOD("java.net.URI", "toURL", cg_type_named(arena, "java.net.URL"));
    REG_METHOD("java.net.URL", "openStream", cg_type_named(arena, "java.io.InputStream"));
    REG_METHOD("java.net.URL", "toString", cg_type_named(arena, "java.lang.String"));
    REG_CTOR("java.net.URL", "URL");
    REG_CTOR("java.net.URI", "URI");
}
