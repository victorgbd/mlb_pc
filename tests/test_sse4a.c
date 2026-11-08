/* cpu_patch.c's SSE4a rewrite (EXTRQ, INSERTQ) against the AMD definition, on a CPU without
 * SSE4a (every Intel CPU): each case loads xmm0/xmm1 (and xmm9 for a REX register), runs the
 * rewritten instruction and compares xmm0 with the reference result. Windows x86-64. */
#define _GNU_SOURCE
#include "cpu_patch.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

#define SNIP(name,text) __asm__(".pushsection .data\n" #name ":\n" text "\n" #name "_end:\n.popsection"); \
    extern const unsigned char name[], name##_end[];
/* rcx = {xmm0 lo, xmm0 hi, xmm1 lo, xmm1 hi}; xmm0 is written back. Flags are pushed before and
 * popped into rax after, to check they survive. */
SNIP(prologue,"movdqu (%rcx),%xmm0\nmovdqu 16(%rcx),%xmm1\nmovdqa %xmm1,%xmm9\nstc\n")
SNIP(epilogue,"\nmovdqu %xmm0,(%rcx)\nsetc %al\nmovzbl %al,%eax\nret")
SNIP(ins_8_8,"insertq $8,$8,%xmm1,%xmm0")
SNIP(ins_16_40,"insertq $16,$40,%xmm1,%xmm0")
SNIP(ins_0_0,"insertq $0,$0,%xmm1,%xmm0")
SNIP(ins_r9,"insertq $12,$4,%xmm9,%xmm0")
SNIP(ins_reg,"insertq %xmm1,%xmm0")
SNIP(ext_8_8,"extrq $8,$8,%xmm0")
SNIP(ext_20_33,"extrq $20,$33,%xmm0")
SNIP(ext_0_4,"extrq $0,$4,%xmm0")
SNIP(ext_reg,"extrq %xmm1,%xmm0")
/* MLB The Show 24, guest offset 0x80e289: insertq xmm0, xmm0, 8, 8 */
SNIP(mlb_same,".byte 0xf2,0x0f,0x78,0xc0,0x08,0x08")

static uint64_t mask(unsigned length) { return length ? (UINT64_C(1)<<length)-1 : ~UINT64_C(0); }
static uint64_t insert(uint64_t d, uint64_t s, unsigned length, unsigned index) {
    const uint64_t m=mask(length);
    return (d&~(m<<index))|((s&m)<<index);
}
static uint64_t extract(uint64_t d, unsigned length, unsigned index) { return d>>index&mask(length); }

static int failures;
static void check(const char *name, const unsigned char *snip, const unsigned char *end, uint64_t x0, uint64_t x1lo,
                  uint64_t x1hi, uint64_t expected) {
    unsigned char *code=VirtualAlloc(NULL,1<<16,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE);
    size_t n=0;
    memcpy(code+n,prologue,(size_t)(prologue_end-prologue)); n+=(size_t)(prologue_end-prologue);
    const size_t at=n;
    memcpy(code+n,snip,(size_t)(end-snip)); n+=(size_t)(end-snip);
    memcpy(code+n,epilogue,(size_t)(epilogue_end-epilogue)); n+=(size_t)(epilogue_end-epilogue);
    const uint64_t range[1][2]={{0,n}};
    cpu_patch_image(code,n,range,1);
    if (code[at]!=0xe9) { printf("FAIL %s: not rewritten\n",name); ++failures; return; }
    uint64_t v[4]={x0,UINT64_C(0x1111222233334444),x1lo,x1hi};
    int carry=((int (*)(uint64_t *))(void *)code)(v);
    const int ok=v[0]==expected && v[1]==UINT64_C(0x1111222233334444) && carry==1;
    printf("%s %s: got %016llx expected %016llx%s\n",ok ? "ok  " : "FAIL",name,(unsigned long long)v[0],
           (unsigned long long)expected,carry==1 ? "" : " (flags lost)");
    failures+=!ok;
}

/* Immediate forms end with two bytes: length, then index (AMD's encoding). */
#define L(s) ((unsigned)s##_end[-2]&63)
#define I(s) ((unsigned)s##_end[-1]&63)
int main(void) {
    const uint64_t d=UINT64_C(0x0123456789abcdef), s=UINT64_C(0xfedcba9876543210);
    check("insertq 8,8",ins_8_8,ins_8_8_end,d,s,0,insert(d,s,L(ins_8_8),I(ins_8_8)));
    check("insertq 16,40",ins_16_40,ins_16_40_end,d,s,0,insert(d,s,L(ins_16_40),I(ins_16_40)));
    check("insertq 0,0",ins_0_0,ins_0_0_end,d,s,0,insert(d,s,0,0));
    check("insertq xmm9",ins_r9,ins_r9_end,d,s,0,insert(d,s,L(ins_r9),I(ins_r9)));
    check("insertq reg",ins_reg,ins_reg_end,d,s,(UINT64_C(24)<<8)|10,insert(d,s,10,24));
    check("insertq same (MLB)",mlb_same,mlb_same_end,d,0,0,insert(d,d,8,8));
    check("extrq 8,8",ext_8_8,ext_8_8_end,d,0,0,extract(d,L(ext_8_8),I(ext_8_8)));
    check("extrq 20,33",ext_20_33,ext_20_33_end,d,0,0,extract(d,L(ext_20_33),I(ext_20_33)));
    check("extrq 0,4",ext_0_4,ext_0_4_end,d,0,0,extract(d,L(ext_0_4),I(ext_0_4)));
    check("extrq reg",ext_reg,ext_reg_end,d,(UINT64_C(12)<<8)|28,0,extract(d,28,12));
    puts(failures ? "SSE4a test FAILED" : "SSE4a test passed");
    return failures!=0;
}
