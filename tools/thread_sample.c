/* Samples where the threads of a running bb-probe.exe are (diagnosis of hangs and busy waits).
 * Usage: thread-sample.exe PID IMAGE_BASE IMAGE_SIZE [SAMPLES] [INTERVAL_MS]
 * For each thread: its name, then per sample the instruction pointer (as a guest offset when it is
 * in the guest image) and the first guest return addresses found on its stack. Build:
 *   clang -O2 tools/thread_sample.c -o out/thread-sample.exe */
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include <stdint.h>

typedef HRESULT (WINAPI *GetDescription)(HANDLE, PWSTR *);

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s PID IMAGE_BASE IMAGE_SIZE [SAMPLES] [INTERVAL_MS]\n", argv[0]); return 2; }
    const DWORD pid = (DWORD)strtoul(argv[1], NULL, 0);
    const unsigned long long base = strtoull(argv[2], NULL, 16), size = strtoull(argv[3], NULL, 0);
    const int samples = argc > 4 ? atoi(argv[4]) : 3, interval = argc > 5 ? atoi(argv[5]) : 300;
    HANDLE process = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!process) { fprintf(stderr, "OpenProcess failed\n"); return 1; }
    /* Modules, so host addresses print as module+offset. */
    struct { unsigned long long base, size; char name[64]; } mods[256]; int nmods = 0;
    HANDLE ms = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    MODULEENTRY32 me = {.dwSize = sizeof(me)};
    for (BOOL ok = Module32First(ms, &me); ok && nmods < 256; ok = Module32Next(ms, &me)) {
        mods[nmods].base = (unsigned long long)(uintptr_t)me.modBaseAddr; mods[nmods].size = me.modBaseSize;
        snprintf(mods[nmods].name, sizeof(mods[nmods].name), "%s", me.szModule); ++nmods;
    }
    CloseHandle(ms);
    GetDescription describe = (GetDescription)(void *)GetProcAddress(GetModuleHandleA("kernel32.dll"), "GetThreadDescription");
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 entry = {.dwSize = sizeof(entry)};
    for (BOOL ok = Thread32First(snapshot, &entry); ok; ok = Thread32Next(snapshot, &entry)) {
        if (entry.th32OwnerProcessID != pid) continue;
        HANDLE thread = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ThreadID);
        if (!thread) continue;
        wchar_t *name = NULL;
        if (describe && SUCCEEDED(describe(thread, &name)) && name && *name) printf("thread %lu '%ls'\n", entry.th32ThreadID, name);
        else printf("thread %lu\n", entry.th32ThreadID);
        if (name) LocalFree(name);
        for (int s = 0; s < samples; ++s) {
            CONTEXT context = {.ContextFlags = CONTEXT_CONTROL | CONTEXT_INTEGER};
            if (SuspendThread(thread) == (DWORD)-1) break;
            BOOL got = GetThreadContext(thread, &context);
            unsigned long long stack[512] = {0};
            SIZE_T read = 0;
            if (got) ReadProcessMemory(process, (void *)context.Rsp, stack, sizeof(stack), &read);
            ResumeThread(thread);
            if (!got) break;
            const unsigned long long rip = context.Rip;
            if (rip - base < size) printf("  guest+0x%llx", rip - base);
            else {
                int m = 0;
                while (m < nmods && rip - mods[m].base >= mods[m].size) ++m;
                if (m < nmods) printf("  %s+0x%llx", mods[m].name, rip - mods[m].base); else printf("  host 0x%llx", rip);
            }
            int shown = 0;
            for (SIZE_T i = 0; i < read / 8 && shown < 8; ++i)
                if (stack[i] - base < size) { printf(" <- 0x%llx", stack[i] - base); ++shown; }
            printf("\n");
            Sleep(interval);
        }
        CloseHandle(thread);
    }
    CloseHandle(snapshot);
    return 0;
}
