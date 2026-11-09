#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the real enhanced-LRC parser with ASAN/UBSAN; no device or network."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile

app = Path(__file__).resolve().parents[1]
source = r'''
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lrc.h"

static lrc_line_t lines[32];
static lrc_word_t words[128];
static char text[4096], plain[4096];
static int synced, wc;
static int parse(const char *raw, int word_cap) {
    memset(lines, 0xA5, sizeof lines);
    memset(words, 0xA5, sizeof words);
    return lrc_parse_words(raw, lines, 32, text, sizeof text,
                           plain, sizeof plain, &synced, words, word_cap, &wc);
}
static lrc_word_t word(int line, int i) {
    assert(i >= 0 && i < lines[line].word_count);
    int at = lines[line].word_first + i;
    assert(at >= 0 && at < wc);
    return words[at];
}
static void check_word(int line, int i, long start, long end,
                       int begin, int finish, const char *expected) {
    lrc_word_t w = word(line, i);
    assert(w.start_ms == start && w.end_ms == end);
    assert(w.start_byte == begin && w.end_byte == finish);
    const char *s = text + lines[line].text;
    assert(finish <= (int)strlen(s));
    assert(finish - begin == (int)strlen(expected));
    assert(!memcmp(s + begin, expected, (size_t)(finish - begin)));
}
static void invariants(int n) {
    for(int i = 0; i < n; i++) {
        assert(i == 0 || lines[i].ms >= lines[i - 1].ms);
        assert(lines[i].word_count >= 0 && lines[i].word_first >= 0);
        assert(lines[i].word_first + lines[i].word_count <= wc);
        const char *s = text + lines[i].text;
        int len = (int)strlen(s), last = -1;
        for(int j = 0; j < lines[i].word_count; j++) {
            lrc_word_t w = word(i, j);
            assert(w.start_ms >= 0 && (w.end_ms == -1 || w.end_ms >= w.start_ms));
            assert(w.start_byte >= 0 && w.end_byte > w.start_byte && w.end_byte <= len);
            assert(w.start_byte >= last);
            assert(((unsigned char)s[w.start_byte] & 0xC0) != 0x80);
            assert(((unsigned char)s[w.end_byte] & 0xC0) != 0x80);
            last = w.end_byte;
        }
    }
}
static void estimates(void) {
    int n = parse("[00:01]a bb ccc\n[00:07.003]last line", 128);
    char original[4096]; memcpy(original, text, sizeof text);
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 0) == 1);
    assert(wc == 3 && lines[1].word_count == 0);
    check_word(0, 0, 1000, 2001, 0, 1, "a");
    check_word(0, 1, 2001, 4002, 2, 4, "bb");
    check_word(0, 2, 4002, 7003, 5, 8, "ccc");
    assert(!memcmp(text, original, sizeof text));
    /* Repeated invocation does not replace or duplicate any existing spans. */
    lrc_word_t saved[128]; memcpy(saved, words, sizeof words);
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, -1) == 0);
    assert(!memcmp(saved, words, sizeof words));
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 9003) == 1);
    assert(lines[1].word_count == 2 && word(1, 1).end_ms == 9003);
    invariants(n);

    n = parse("[00:01]<00:01>exact <00:01.2>timing\n[00:02]estimated words\n[00:04]tail", 128);
    int exact_count = wc; memcpy(saved, words, sizeof words);
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 0) == 1);
    assert(lines[0].word_count == exact_count && !memcmp(saved, words, (size_t)exact_count * sizeof *words));
    assert(word(0, 1).end_ms == -1); /* Unspecified exact tail remains exact/unknown. */
    assert(lines[1].word_count == 2 && lines[2].word_count == 0);

    /* Enhanced input retains provenance even if its exact spans exceeded the
     * parser's capacity, or a subsequent tag was invalid. Never replace it. */
    n = parse("[00:01][00:03]<00:01>a<00:01.2>b<00:01.4>c\n[00:05]ordinary\n[00:06]tail", 1);
    assert(wc == 0 && lines[0].has_word_timing && lines[1].has_word_timing);
    assert(!lines[2].has_word_timing && !lines[3].has_word_timing);
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 0) == 1);
    assert(!lines[0].word_count && !lines[1].word_count && lines[2].word_count == 1);
    n = parse("[00:01]<00:01>a<00:99>b\n[00:03]tail", 128);
    assert(lines[0].has_word_timing && !lines[0].word_count);
    assert(!lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 0));

    n = parse("[offset:+250]\n[00:01][00:01][00:03]é 界\n[00:05]tail", 128);
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 0) == 3);
    check_word(0, 0, 750, 1750, 0, 2, "é");
    check_word(0, 1, 1750, 2750, 3, 6, "界");
    check_word(1, 0, 750, 1750, 0, 2, "é");
    check_word(2, 1, 3750, 4750, 3, 6, "界");
    invariants(n);

    /* Unicode scalars, not encoded bytes, determine weights. Composed and
     * decomposed accents have identical weight and remain intact in spans. */
    const char unicode[] = "e\xCC\x81 \xC3\xA9";
    assert(lrc_estimate_line_words(unicode, sizeof unicode, 1000, 3000, words, 128) == 2);
    assert(words[0].start_byte == 0 && words[0].end_byte == 3 && words[0].end_ms == 2000);
    assert(words[1].start_byte == 4 && words[1].end_byte == 6 && words[1].start_ms == 2000);
    const char spaces[] = " \t\xC3\xA9\xC2\xA0\xE7\x95\x8C\xE3\x80\x80\r\n";
    assert(lrc_estimate_line_words(spaces, sizeof spaces, 0, 2000, words, 128) == 2);
    assert(words[0].start_byte == 2 && words[0].end_byte == 4 && words[0].end_ms == 1000);
    assert(words[1].start_byte == 6 && words[1].end_byte == 9 && words[1].end_ms == 2000);
    const char joiner[] = "\xF0\x9F\x91\xA9\xE2\x80\x8D\xF0\x9F\x92\xBB hello";
    assert(lrc_estimate_line_words(joiner, sizeof joiner, 0, 1000, words, 128) == 2);
    assert(words[0].start_byte == 0 && words[0].end_byte == 11 && words[1].start_byte == 12);

    memset(words, 0xA5, sizeof words); memcpy(saved, words, sizeof words);
    const char *invalid[] = {"\x80", "\xC0\xAF", "\xED\xA0\x80", "\xF4\x90\x80\x80", "ok \xE2\x82"};
    for(unsigned i = 0; i < sizeof invalid / sizeof invalid[0]; i++) {
        assert(!lrc_estimate_line_words(invalid[i], (int)strlen(invalid[i]) + 1, 0, 1000, words, 128));
        assert(!memcmp(saved, words, sizeof words));
    }
    const char no_nul[] = {'a', 'b'};
    assert(!lrc_estimate_line_words(no_nul, sizeof no_nul, 0, 1000, words, 128));
    assert(!lrc_estimate_line_words("one two", 8, 0, 1000, words, 1));
    assert(!lrc_estimate_line_words("one two", 8, 0, 1, words, 128));
    assert(!lrc_estimate_line_words("one", 4, -1, 1000, words, 128));
    assert(!lrc_estimate_line_words("one", 4, 1000, 1000, words, 128));
    assert(!lrc_estimate_line_words("one", 4, 1000, -1, words, 128));
    assert(!lrc_estimate_line_words(" \t", 3, 0, 1000, words, 128));
    assert(!memcmp(saved, words, sizeof words));
    assert(lrc_estimate_line_words("a b", 4, LONG_MAX - 20, LONG_MAX, words, 2) == 2);
    assert(words[0].start_ms == LONG_MAX - 20 && words[0].end_ms == LONG_MAX - 10 && words[1].end_ms == LONG_MAX);
    assert(lrc_estimate_line_words("a bb ccc", 9, 0, LONG_MAX, words, 3) == 3);
    assert(words[0].end_ms > 0 && words[1].end_ms > words[0].end_ms && words[2].end_ms == LONG_MAX);

    n = parse("[00:01]too many words\n[00:02]ok\n[00:03]tail", 128);
    assert(lrc_estimate_words(lines, n, text, sizeof text, words, 1, &wc, 0) == 1);
    assert(!lines[0].word_count && lines[1].word_count == 1 && wc == 1);
    n = parse("[00:01]first\n[00:02]second", 128);
    lines[1].ms = 0;
    assert(!lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 4000));
    assert(wc == 0 && !lines[0].word_count && !lines[1].word_count);
    n = parse("untimed plain lyrics", 128);
    assert(!lrc_estimate_words(lines, n, text, sizeof text, words, 128, &wc, 4000));
    /* Every UTF-8 prefix uses a true allocation boundary; incomplete scalars
     * must decline the estimate without writing past any input/output buffer. */
    for(size_t i = 0; i <= strlen(joiner); i++) {
        char *prefix = malloc(i + 1); assert(prefix);
        memcpy(prefix, joiner, i); prefix[i] = 0;
        (void)lrc_estimate_line_words(prefix, (int)i + 1, 0, 1000, words, 2);
        free(prefix);
    }
    puts("PASS: optional estimated words, exact precedence, Unicode/combining/whitespace byte spans, duplicate times, known/unknown tail, bounded capacity and overflow");
}
int main(void) {
    estimates();
    int n = parse("[00:01]<00:01> Hello  <00:02.25>world<00:03>\n[00:05]next", 128);
    assert(n == 2 && synced && wc == 2 && lines[0].word_count == 2);
    assert(!strcmp(text + lines[0].text, "Hello world"));
    assert(!strcmp(plain, "Hello world\nnext\n"));
    check_word(0, 0, 1000, 2250, 0, 5, "Hello");
    check_word(0, 1, 2250, 3000, 6, 11, "world");
    assert(lines[1].word_count == 0);
    invariants(n);

    n = parse("[00:02]Sing <00:02.50>along\n[00:05]next", 128);
    assert(n == 2 && wc == 2);
    check_word(0, 0, 2000, 2500, 0, 4, "Sing");
    check_word(0, 1, 2500, -1, 5, 10, "along");
    /* A later line is not an explicit word-end timestamp. */
    assert(word(0, 1).end_ms == -1);

    n = parse("[00:10][00:02]<00:10>é <00:11>界<00:12>\n[offset:+500]", 128);
    assert(n == 2 && synced && wc == 4);
    assert(lines[0].ms == 1500 && lines[1].ms == 9500);
    assert(!strcmp(text + lines[0].text, "é 界"));
    check_word(0, 0, 1500, 2500, 0, 2, "é");
    check_word(0, 1, 2500, 3500, 3, 6, "界");
    check_word(1, 0, 9500, 10500, 0, 2, "é");
    check_word(1, 1, 10500, 11500, 3, 6, "界");
    invariants(n);

    n = parse("[offset:+2000]\n[00:01][00:10]<00:01>a<00:02>b<00:03>", 128);
    assert(n == 2 && lines[0].ms == 0 && lines[1].ms == 8000);
    check_word(0, 0, 0, 0, 0, 1, "a");
    check_word(0, 1, 0, 1000, 1, 2, "b");
    check_word(1, 0, 8000, 9000, 0, 1, "a");
    check_word(1, 1, 9000, 10000, 1, 2, "b");
    n = parse("[offset:-250]\n[00:01]<00:01>a<00:02>", 128);
    check_word(0, 0, 1250, 2250, 0, 1, "a");

    n = parse("[00:01]<00:01>a<00:02>\n[00:01]<00:01>b<00:02>", 128);
    assert(n == 2 && !strcmp(text + lines[0].text, "a") &&
           !strcmp(text + lines[1].text, "b"));
    assert(lrc_index_at(lines, n, 1000) == 1);

    const char *invalid[] = {
        "[00:01]<00:01>a<00:99>b",
        "[00:01]<00:02>a<00:01>b",
        "[00:01]<00:01>a<00:01>b",
        "[00:01]<00:01>a<00:no>b",
        "[00:01]<00:01>a<00:02",
        "[00:01]<00:00.50>a<00:02>",
        "[00:01]<00:01>a<1.2>b",
    };
    for(unsigned i = 0; i < sizeof invalid / sizeof invalid[0]; i++) {
        n = parse(invalid[i], 128);
        assert(n == 1 && wc == 0 && lines[0].word_count == 0);
    }
    n = parse("[00:01]<00:01>literal <angle> text<00:02>", 128);
    assert(n == 1 && wc == 1 && !strcmp(text + lines[0].text, "literal <angle> text"));
    n = parse("[00:01]<00:01><00:02><00:03>\n[00:04]next", 128);
    assert(n == 2 && wc == 0 && !strcmp(text + lines[0].text, ""));
    puts("PASS: exact span timing, terminal/unknown end, offsets, repeats, UTF-8 and invalid timing");

    n = parse("[00:01][00:10]<00:01>a<00:02>b<00:03>\n"
              "[00:20]<00:20>later<00:21>", 2);
    assert(n == 3 && wc == 1);
    assert(!lines[0].word_count && !lines[1].word_count && lines[2].word_count == 1);
    check_word(2, 0, 20000, 21000, 0, 5, "later");
    n = parse("[00:01]<00:01>a<00:02>b<00:03>", 1);
    assert(n == 1 && wc == 0 && !lines[0].word_count);
    unsigned char guard[sizeof(lrc_word_t)];
    memset(guard, 0xA5, sizeof guard);
    assert(!memcmp(&words[1], guard, sizeof guard));
    n = lrc_parse_words("[00:01][00:10]<00:01>a<00:02>b<00:03>",
                         lines, 1, text, sizeof text, NULL, 0, &synced, words, 2, &wc);
    assert(n == 1 && !synced && wc == 2 && lines[0].word_count == 2);
    n = lrc_parse_words("unused", NULL, 0, NULL, 0, NULL, 0, &synced, NULL, 0, &wc);
    assert(n == 0 && synced == 0 && wc == 0);
    puts("PASS: bounded capacity, whole-line fallback and limited line output");

    const char *legacy =
        "[ar:Example]\n[offset:+250]\n[00:02][00:01]<00:00.50>héllo\n"
        "[00:03]third\nplain  words\n";
    n = lrc_parse(legacy, lines, 32, text, sizeof text, plain, sizeof plain, &synced);
    assert(n == 3 && synced && lines[0].ms == 750 && lines[1].ms == 1750 &&
           lines[2].ms == 2750 && !strcmp(text + lines[0].text, "héllo"));
    assert(!strcmp(plain, "héllo\nthird\nplain words\n"));
    for(int i = 0; i < n; i++) assert(lines[i].word_count == 0);
    n = parse("[00:01]one\n[00:02]two", 128);
    assert(n == 2 && synced && wc == 0);
    n = parse("plain words\nanother line", 128);
    assert(!n && !synced && !wc && !strcmp(plain, "plain words\nanother line\n"));
    n = lrc_parse("[00:01]one\nplain", NULL, 0, text, sizeof text,
                  plain, sizeof plain, &synced);
    assert(n == 0 && !synced && !strcmp(plain, "one\nplain\n"));
    puts("PASS: unchanged legacy line/plain behavior");

    /* Every truncated prefix gets its own exact allocation: sanitizer checks
     * timestamp lookahead at an actual allocation boundary, not a padded buffer. */
    const char *fixture =
        "[offset:+250]\n[01:02.123][02:03.456]<01:02.123>héllo "
        "<01:03.456>世界<01:04.789>\n[05:06]tail";
    for(size_t k = 0; k <= strlen(fixture); k++) {
        char *prefix = malloc(k + 1); assert(prefix);
        memcpy(prefix, fixture, k); prefix[k] = 0;
        n = parse(prefix, 128);
        invariants(n);
        free(prefix);
    }
    puts("PASS: allocation-boundary truncation under ASAN/UBSAN");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="diskos-lyrics-word-test-") as tmp:
    path = Path(tmp)
    (path / "test.c").write_text(source)
    cc = shlex.split(os.environ.get("CC", "cc"))
    subprocess.run(cc + [
        "-D_GNU_SOURCE", "-std=gnu11", "-O1", "-g",
        "-Wall", "-Wextra", "-Wno-unused-function",
        "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
        "-I" + str(app), str(path / "test.c"), "-o", str(path / "test"),
    ], check=True)
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
    subprocess.run([str(path / "test")], env=env, check=True)
print("COMPLETE lyrics_word_test.py")
