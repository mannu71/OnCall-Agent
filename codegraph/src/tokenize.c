/* tokenize.c — identifier tokenizer (camel/snake aware). */
#include "tokenize.h"

#include <stdlib.h>
#include <string.h>
#include <ctype.h>

void toklist_free(toklist *t) {
    for (size_t i = 0; i < t->len; i++) free(t->items[i]);
    free(t->items);
    t->items = NULL; t->len = t->cap = 0;
}

static void push(toklist *t, const char *s, size_t n) {
    if (n < 2) return;                 /* drop 1-char fragments */
    if (n > 64) n = 64;
    char *w = malloc(n + 1);
    for (size_t i = 0; i < n; i++) w[i] = (char)tolower((unsigned char)s[i]);
    w[n] = '\0';
    if (t->len == t->cap) {
        t->cap = t->cap ? t->cap * 2 : 8;
        t->items = realloc(t->items, t->cap * sizeof(char *));
    }
    t->items[t->len++] = w;
}

/* Split a run of identifier characters into camel/snake sub-tokens. */
static void split_ident(const char *s, size_t n, toklist *out) {
    size_t start = 0;
    for (size_t i = 1; i <= n; i++) {
        int boundary = 0;
        if (i == n) {
            boundary = 1;
        } else {
            char prev = s[i - 1], cur = s[i];
            if (cur == '_') { /* snake separator handled below */ }
            /* lower/digit -> Upper : fooBar | foo2Bar */
            if (islower((unsigned char)prev) && isupper((unsigned char)cur)) boundary = 1;
            /* Upper -> Upper+lower : HTTPServer -> HTTP | Server */
            if (isupper((unsigned char)prev) && isupper((unsigned char)cur) &&
                i + 1 < n && islower((unsigned char)s[i + 1])) boundary = 1;
        }
        if (s[i - 1] == '_') {
            /* end token before the underscore */
            if (i - 1 > start) push(out, s + start, (i - 1) - start);
            start = i;
            continue;
        }
        if (boundary) {
            push(out, s + start, i - start);
            start = i;
        }
    }
}

void tokenize(const char *text, toklist *out) {
    if (!text) return;
    size_t n = strlen(text);
    size_t i = 0;
    while (i < n) {
        if (isalnum((unsigned char)text[i]) || text[i] == '_') {
            size_t j = i;
            while (j < n && (isalnum((unsigned char)text[j]) || text[j] == '_')) j++;
            split_ident(text + i, j - i, out);
            i = j;
        } else {
            i++;
        }
    }
}
