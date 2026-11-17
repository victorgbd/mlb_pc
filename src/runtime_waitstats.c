/* BB_WAIT_STATS=1 (diagnosis): time each guest thread spends blocked in the runtime's waits
 * (semaphores, condition variables, sleeps, event queues, vblank, GPU idle), by thread name and
 * kind, printed every 5 s. Shows what the game's threads wait for while a frame takes too long. */
#define _GNU_SOURCE
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#endif

typedef struct { char thread[32]; const char *kind; uint64_t ns, count; } WaitEntry;
#define MAX_ENTRIES 512
static WaitEntry entries[MAX_ENTRIES];
static size_t entry_count;
static volatile int table_lock;
static uint64_t window_start;

static uint64_t now_ns(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000u+(uint64_t)t.tv_nsec;
}

int runtime_wait_stats_on(void) {
    static int on=-1;
    if (on<0) { const char *env=getenv("BB_WAIT_STATS"); on=env && env[0]=='1'; }
    return on;
}

uint64_t runtime_wait_begin(void) { return runtime_wait_stats_on() ? now_ns() : 0; }

static int by_ns(const void *a,const void *b) {
    const WaitEntry *x=a,*y=b;
    return x->ns<y->ns ? 1 : x->ns>y->ns ? -1 : 0;
}

static void print_window(uint64_t now) {
    const double seconds=(double)(now-window_start)/1e9;
    WaitEntry copy[MAX_ENTRIES];
    const size_t n=entry_count;
    memcpy(copy,entries,n*sizeof(WaitEntry));
    for (size_t i=0;i<entry_count;++i) entries[i].ns=entries[i].count=0;
    window_start=now;
    qsort(copy,n,sizeof(WaitEntry),by_ns);
    printf("Waits over %.1f s (ms blocked per second, calls/s):\n",seconds);
    for (size_t i=0,shown=0;i<n && shown<32;++i) {
        if (copy[i].kind[0]!='>' && copy[i].ns<(uint64_t)(seconds*5e6)) continue; /* under 0.5% */
        if (!copy[i].count) continue;
        printf("  %-24s %-22s %7.1f ms/s %8.0f/s\n",copy[i].thread,copy[i].kind,copy[i].ns/1e6/seconds,copy[i].count/seconds);
        ++shown;
    }
}

void runtime_wait_end(const char *kind,uint64_t start) {
    if (!start) return;
    const uint64_t now=now_ns(), waited=now-start;
    char thread[32];
    const char *name=runtime_thread_name_here();
    if (name && *name) snprintf(thread,sizeof(thread),"%s",name);
    else {
#ifdef _WIN32
        snprintf(thread,sizeof(thread),"tid %lu",(unsigned long)GetCurrentThreadId());
#else
        snprintf(thread,sizeof(thread),"host");
#endif
    }
    while (__atomic_exchange_n(&table_lock,1,__ATOMIC_ACQUIRE)) {}
    if (!window_start) window_start=now;
    size_t i=0;
    for (;i<entry_count;++i) if (entries[i].kind==kind && !strcmp(entries[i].thread,thread)) break;
    if (i==entry_count && entry_count<MAX_ENTRIES) {
        memcpy(entries[i].thread,thread,sizeof(thread)); entries[i].kind=kind; entries[i].ns=entries[i].count=0;
        ++entry_count;
    }
    if (i<entry_count) { entries[i].ns+=waited; ++entries[i].count; }
    if (now-window_start>=5000000000ull) print_window(now);
    __atomic_store_n(&table_lock,0,__ATOMIC_RELEASE);
}
