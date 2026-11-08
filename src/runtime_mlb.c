/* System libraries MLB The Show 24 (CUSA43942) imports that the Uncharted runtime did not need.
 *
 * Same contract as runtime_services.c: an offline, single-user console. Services that need a
 * console feature the PC does not have (USB storage, the web browser, PSN sign-in, PS VR, share
 * play, live streaming) report "not available" in the way the game is prepared for; the rest
 * work: JSON parsing (libSceJson2), random numbers, temporary data, PNG headers.
 * Audio3d: the game's 3D audio port runs, and its AudioOut ports (the mixed output) play through
 * runtime_audio.c; sound objects pushed into the 3D port are not rendered yet.
 * Every function is logged the first time it is called, so a log shows which ones the game uses. */
#define _GNU_SOURCE
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>
#include <limits.h>
#ifdef _WIN32
#include <windows.h>
#include <bcrypt.h>
#endif

#define ERR(n) ((int32_t)(UINT32_C(0x80020000)|(n)))
#define ONCE(what) do { static atomic_int seen; if (!atomic_exchange(&seen,1)) printf("Runtime: %s\n",what); } while (0)

/* ---------------------------------------------------------------- kernel ---- */
static ABI int32_t ok_zero(void) { return 0; }
static ABI void *null_pointer(void) { return NULL; }
static ABI int32_t elf_phdr_match_addr(void *info, void *address) { (void)info; (void)address; return 0; }
static ABI int32_t is_signal_return(void *context) { (void)context; return 0; }
static ABI void pthread_cxa_finalize(void *dso) { (void)dso; }
static ABI int32_t kernel_sleep(uint32_t seconds) {
    struct timespec t={.tv_sec=seconds,.tv_nsec=0}; nanosleep(&t,NULL); return 0;
}
typedef struct { int64_t sec, nsec; } GuestTimespec;
static ABI int32_t posix_nanosleep(const GuestTimespec *request, GuestTimespec *remain) {
    if (!request || request->nsec<0 || request->nsec>=1000000000) { *runtime_errno()=22; return -1; }
    struct timespec t={.tv_sec=(time_t)request->sec,.tv_nsec=(long)request->nsec};
    nanosleep(&t,NULL);
    if (remain) remain->sec=remain->nsec=0;
    return 0;
}
/* Base PS4 (not PS4 Pro): the game picks its base-console render path. */
static ABI int32_t is_neo_mode(void) { return 0; }
/* Thread-exit bookkeeping libc reports to the runtime linker: nothing to keep here. */
static ABI void rtld_thread_atexit(void) {}
static ABI void set_thread_atexit(void *value) { (void)value; }
static ABI int32_t set_thread_dtors(void *function) { (void)function; return 0; }
/* Unwinding info (C++ exceptions): module tables are not exposed to the guest yet. */
static ABI int32_t module_info_for_unwind(uintptr_t address, int flags, void *info) {
    (void)flags; (void)info;
    static atomic_int reports;
    if (atomic_fetch_add(&reports,1)<8) printf("Runtime: sceKernelGetModuleInfoForUnwind(%#llx) not provided\n",(unsigned long long)address);
    return ERR(3);
}
static ABI int32_t module_info_from_addr(uintptr_t address, int flags, void *info) { return module_info_for_unwind(address,flags,info); }
static ABI int32_t module_segment_info(void *info) { (void)info; return ERR(3); }
/* Game modules are linked at load time (scripts/link_modules.py). Loading one of them again
 * (MLB loads PS4/ffmpeg.prx although the eboot already needs it) returns a module ID, as the
 * console does for a loaded module; any other module is refused. */
static ABI int32_t load_start_module(const char *path, uint64_t argc, const void *argv, uint32_t flags, const void *option, int32_t *result) {
    (void)argc; (void)argv; (void)flags; (void)option;
    static const char *const linked[]={"/app0/PS4/ffmpeg.prx","/app0/sce_module/libc.prx","/app0/sce_module/libSceFios2.prx"};
    for (size_t i=0;path && i<sizeof(linked)/sizeof(*linked);++i)
        if (!strcmp(path,linked[i])) {
            printf("Runtime: sceKernelLoadStartModule(%s): already linked\n",path);
            if (result) *result=0;
            return 0x10+(int32_t)i;
        }
    printf("Runtime: sceKernelLoadStartModule(%s) refused\n",path ? path : "(null)");
    if (result) *result=0;
    return ERR(2);
}

/* ------------------------------------------------------------- libSceJson2 ---- */
/* sce::Json objects live in guest memory with the SDK's sizes: Value is 16 bytes, String 8.
 * A Value holds a marker and a pointer to a host node; every node embeds its own Value face,
 * which is what operator[] returns a reference to. Strings are owned char pointers. Parsed
 * trees are never freed (the game parses a few small documents). */
enum { J_NULL, J_BOOL, J_INT, J_UINT, J_REAL, J_STRING, J_ARRAY, J_OBJECT };
#define J_MAGIC UINT32_C(0x4a534f4e)
typedef struct JNode JNode;
typedef struct { uint32_t reserved, magic; JNode *node; } JValue;
typedef struct { char *text; } JString;
struct JNode {
    JValue face;
    int type;
    int boolean;
    int64_t integer;
    double real;
    JString string;
    size_t count, capacity;
    char **keys;
    JNode **items;
};
static JNode null_node={.face={0,J_MAGIC,&null_node}};
static JString empty_string={(char *)""};
static JNode *jnode(const JValue *v) { return v && v->magic==J_MAGIC ? v->node : NULL; }
static JNode *jnew(int type) {
    JNode *n=calloc(1,sizeof(*n));
    if (!n) return NULL;
    n->face=(JValue){0,J_MAGIC,n}; n->type=type; return n;
}
static int jappend(JNode *parent, char *key, JNode *child) {
    if (parent->count==parent->capacity) {
        size_t capacity=parent->capacity ? parent->capacity*2 : 8;
        JNode **items=realloc(parent->items,capacity*sizeof(*items));
        char **keys=realloc(parent->keys,capacity*sizeof(*keys));
        if (items) parent->items=items;
        if (keys) parent->keys=keys;
        if (!items || !keys) return -1;
        parent->capacity=capacity;
    }
    parent->keys[parent->count]=key; parent->items[parent->count++]=child; return 0;
}
typedef struct { const char *at, *end; int depth; } JParser;
static void jspace(JParser *p) { while (p->at<p->end && (*p->at==' ' || *p->at=='\t' || *p->at=='\n' || *p->at=='\r')) ++p->at; }
static void jutf8(char **out, unsigned code) {
    char *o=*out;
    if (code<0x80) *o++=(char)code;
    else if (code<0x800) { *o++=(char)(0xc0|code>>6); *o++=(char)(0x80|(code&0x3f)); }
    else if (code<0x10000) { *o++=(char)(0xe0|code>>12); *o++=(char)(0x80|(code>>6&0x3f)); *o++=(char)(0x80|(code&0x3f)); }
    else { *o++=(char)(0xf0|code>>18); *o++=(char)(0x80|(code>>12&0x3f)); *o++=(char)(0x80|(code>>6&0x3f)); *o++=(char)(0x80|(code&0x3f)); }
    *out=o;
}
static int jhex4(JParser *p, unsigned *code) {
    if (p->end-p->at<4) return -1;
    unsigned v=0;
    for (int i=0;i<4;++i) {
        char c=*p->at++; v<<=4;
        if (c>='0' && c<='9') v|=(unsigned)(c-'0');
        else if (c>='a' && c<='f') v|=(unsigned)(c-'a'+10);
        else if (c>='A' && c<='F') v|=(unsigned)(c-'A'+10);
        else return -1;
    }
    *code=v; return 0;
}
static char *jstring(JParser *p) {
    if (p->at>=p->end || *p->at!='"') return NULL;
    ++p->at;
    /* Escapes never grow the text (\uXXXX is 6 bytes, at most 4 in UTF-8). */
    const char *start=p->at;
    while (p->at<p->end && *p->at!='"') p->at+=*p->at=='\\' ? 2 : 1;
    if (p->at>=p->end) return NULL;
    char *text=malloc((size_t)(p->at-start)+1), *o=text;
    if (!text) return NULL;
    p->at=start;
    while (*p->at!='"') {
        char c=*p->at++;
        if (c!='\\') { *o++=c; continue; }
        c=*p->at++;
        switch (c) {
        case 'b': *o++='\b'; break; case 'f': *o++='\f'; break; case 'n': *o++='\n'; break;
        case 'r': *o++='\r'; break; case 't': *o++='\t'; break;
        case 'u': {
            unsigned code;
            if (jhex4(p,&code)) { free(text); return NULL; }
            if (code>=0xd800 && code<0xdc00 && p->end-p->at>=6 && p->at[0]=='\\' && p->at[1]=='u') {
                unsigned low; p->at+=2;
                if (jhex4(p,&low)) { free(text); return NULL; }
                code=0x10000+((code-0xd800)<<10)+(low-0xdc00);
            }
            jutf8(&o,code); break;
        }
        default: *o++=c;
        }
    }
    ++p->at; *o=0; return text;
}
static JNode *jvalue(JParser *p);
static JNode *jcontainer(JParser *p, int object) {
    JNode *n=jnew(object ? J_OBJECT : J_ARRAY);
    if (!n) return NULL;
    ++p->at; jspace(p);
    if (p->at<p->end && *p->at==(object ? '}' : ']')) { ++p->at; return n; }
    for (;;) {
        char *key=NULL;
        jspace(p);
        if (object) {
            if (!(key=jstring(p))) return NULL;
            jspace(p);
            if (p->at>=p->end || *p->at++!=':') return NULL;
        }
        JNode *child=jvalue(p);
        if (!child || jappend(n,key,child)) return NULL;
        jspace(p);
        if (p->at>=p->end) return NULL;
        char c=*p->at++;
        if (c==',') continue;
        if (c==(object ? '}' : ']')) return n;
        return NULL;
    }
}
static JNode *jvalue(JParser *p) {
    jspace(p);
    if (p->at>=p->end || ++p->depth>256) return NULL;
    JNode *n=NULL;
    char c=*p->at;
    if (c=='{' || c=='[') n=jcontainer(p,c=='{');
    else if (c=='"') { char *s=jstring(p); if (s && (n=jnew(J_STRING))) n->string.text=s; }
    else if (p->end-p->at>=4 && !memcmp(p->at,"true",4)) { p->at+=4; if ((n=jnew(J_BOOL))) n->boolean=1; }
    else if (p->end-p->at>=5 && !memcmp(p->at,"false",5)) { p->at+=5; n=jnew(J_BOOL); }
    else if (p->end-p->at>=4 && !memcmp(p->at,"null",4)) { p->at+=4; n=jnew(J_NULL); }
    else if (c=='-' || (c>='0' && c<='9')) {
        char number[64]; size_t length=0; int real=0;
        while (p->at<p->end && length<sizeof(number)-1 && *p->at && strchr("+-0123456789.eE",*p->at)) {
            if (strchr(".eE",*p->at)) real=1;
            number[length++]=*p->at++;
        }
        number[length]=0;
        if (real) { if ((n=jnew(J_REAL))) n->real=strtod(number,NULL); }
        else if (number[0]!='-' && strtoull(number,NULL,10)>(unsigned long long)INT64_MAX) {
            if ((n=jnew(J_UINT))) n->integer=(int64_t)strtoull(number,NULL,10);
        } else if ((n=jnew(J_INT))) n->integer=strtoll(number,NULL,10);
        if (n && n->type!=J_REAL) n->real=(double)n->integer;
    }
    --p->depth;
    return n;
}
/* The null-access callback is accepted; missing members return the null Value instead. */
static ABI void json_initializer_ctor(void *self) { (void)self; }
static ABI void json_initializer_dtor(void *self) { (void)self; }
static ABI int32_t json_initialize(void *self, const void *parameter) { (void)self; (void)parameter; ONCE("libSceJson2 initialized"); return 0; }
static ABI void json_allocator_ctor(void *self) { (void)self; }
static ABI void json_allocator_dtor(void *self) { (void)self; }
static ABI void json_allocator_error(void *self, int32_t error, uint64_t size, void *user) {
    (void)self; (void)user; printf("Runtime: sce::Json allocator error %#x (%llu bytes)\n",(unsigned)error,(unsigned long long)size);
}
static ABI int32_t json_set_null_callback(void *callback, void *context) { (void)callback; (void)context; return 0; }
static ABI void json_value_ctor(JValue *v) { if (v) *v=(JValue){0,J_MAGIC,NULL}; }
static ABI void json_value_dtor(JValue *v) {
    JNode *n=jnode(v);
    if (v && (!n || v!=&n->face)) *v=(JValue){0,0,NULL};
}
static ABI void json_value_clear(JValue *v) {
    JNode *n=jnode(v);
    if (!n) return;
    if (v==&n->face) { if (n!=&null_node) { n->type=J_NULL; n->count=0; } }
    else v->node=NULL;
}
static ABI uint64_t json_value_count(const JValue *v) {
    JNode *n=jnode(v);
    return n && (n->type==J_ARRAY || n->type==J_OBJECT) ? n->count : 0;
}
static ABI int32_t json_value_type(const JValue *v) { JNode *n=jnode(v); return n ? n->type : J_NULL; }
static ABI const JString *json_value_string(const JValue *v) {
    JNode *n=jnode(v);
    return n && n->type==J_STRING ? &n->string : &empty_string;
}
static ABI const JValue *json_value_key(const JValue *v, const char *key) {
    JNode *n=jnode(v);
    if (n && n->type==J_OBJECT && key)
        for (size_t i=0;i<n->count;++i) if (n->keys[i] && !strcmp(n->keys[i],key)) return &n->items[i]->face;
    return &null_node.face;
}
static ABI const JValue *json_value_index(const JValue *v, uint64_t index) {
    JNode *n=jnode(v);
    if (n && (n->type==J_ARRAY || n->type==J_OBJECT) && index<n->count) return &n->items[index]->face;
    return &null_node.face;
}
static ABI int32_t json_parse(JValue *v, const char *buffer, uint64_t size) {
    if (!v || !buffer) return (int32_t)0x80920001;
    JParser p={buffer,buffer+size,0};
    JNode *root=jvalue(&p);
    if (!root) { printf("Runtime: sce::Json::Parser::parse failed (%llu bytes)\n",(unsigned long long)size); return (int32_t)0x80920101; }
    *v=(JValue){0,J_MAGIC,root};
    return 0;
}
static ABI void json_string_copy(JString *s, const JString *other) {
    const char *text=other && other->text ? other->text : "";
    s->text=strdup(text);
}
/* Copies made by the game are not freed: a String it built inline would not hold our pointer. */
static ABI void json_string_dtor(JString *s) { (void)s; }
static ABI const char *json_string_cstr(const JString *s) { return s && s->text ? s->text : ""; }
static ABI int json_string_empty(const JString *s) { return !s || !s->text || !*s->text; }
static ABI int32_t json_string_compare(const JString *s, const char *text) { return strcmp(json_string_cstr(s),text ? text : ""); }

/* ---------------------------------------------------------- libSceAudio3d ---- */
#define AUDIO3D_INVALID_PORT ((int32_t)0x80EA0002)
#define AUDIO3D_INVALID_PARAMETER ((int32_t)0x80EA0004)
#define AUDIO3D_PORTS 4
typedef struct { uint64_t size; uint32_t granularity, rate, max_objects, queue_depth, buffer_mode, pad, beds; } Audio3dOpen;
static struct { int used; uint32_t granularity, queue_depth; uint64_t next_push_ns; } audio3d_ports[AUDIO3D_PORTS];
static atomic_uint audio3d_next_object=1;
static pthread_mutex_t audio3d_lock=PTHREAD_MUTEX_INITIALIZER;
static uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000000000u+(uint64_t)t.tv_nsec; }
static ABI int32_t audio3d_initialize(int64_t reserved) {
    (void)reserved;
    int32_t (ABI *init)(void)=runtime_audio_function("sceAudioOutInit");
    if (init) init();
    ONCE("Audio3d initialized (3D objects are not rendered; AudioOut ports play)");
    return 0;
}
static ABI int32_t audio3d_terminate(void) { return 0; }
static ABI int32_t audio3d_port_open(int32_t user, const Audio3dOpen *open, uint32_t *port) {
    (void)user;
    if (!open || !port) return AUDIO3D_INVALID_PARAMETER;
    pthread_mutex_lock(&audio3d_lock);
    int id=-1;
    for (int i=0;i<AUDIO3D_PORTS;++i) if (!audio3d_ports[i].used) { id=i; break; }
    if (id>=0) {
        audio3d_ports[id].used=1;
        audio3d_ports[id].granularity=open->granularity ? open->granularity : 256;
        audio3d_ports[id].queue_depth=open->queue_depth ? open->queue_depth : 2;
        audio3d_ports[id].next_push_ns=0;
    }
    pthread_mutex_unlock(&audio3d_lock);
    if (id<0) return (int32_t)0x80EA0006;
    *port=(uint32_t)id;
    printf("Runtime: Audio3d port %d opened (granularity %u, %u objects, queue %u)\n",id,audio3d_ports[id].granularity,open->max_objects,audio3d_ports[id].queue_depth);
    return 0;
}
static int audio3d_valid(uint32_t port) { return port<AUDIO3D_PORTS && audio3d_ports[port].used; }
static ABI int32_t audio3d_port_close(uint32_t port) {
    if (!audio3d_valid(port)) return AUDIO3D_INVALID_PORT;
    audio3d_ports[port].used=0; return 0;
}
static ABI int32_t audio3d_port_attributes(uint32_t port, uint32_t *capabilities, uint32_t *count) {
    static const uint32_t supported[]={1,2,3,4,5,6,7};
    if (!audio3d_valid(port)) return AUDIO3D_INVALID_PORT;
    if (!count) return AUDIO3D_INVALID_PARAMETER;
    if (capabilities) memcpy(capabilities,supported,sizeof(supported));
    *count=sizeof(supported)/sizeof(*supported); return 0;
}
static ABI int32_t audio3d_object_reserve(uint32_t port, uint32_t *object) {
    if (!audio3d_valid(port)) return AUDIO3D_INVALID_PORT;
    if (!object) return AUDIO3D_INVALID_PARAMETER;
    *object=atomic_fetch_add(&audio3d_next_object,1); return 0;
}
static ABI int32_t audio3d_port_ok(uint32_t port) { return audio3d_valid(port) ? 0 : AUDIO3D_INVALID_PORT; }
/* Push with blocking: one granule of audio time per push, like the hardware queue drains. */
static ABI int32_t audio3d_port_push(uint32_t port, int blocking) {
    if (!audio3d_valid(port)) return AUDIO3D_INVALID_PORT;
    uint64_t period=(uint64_t)audio3d_ports[port].granularity*1000000000u/48000u, now=now_ns();
    uint64_t *next=&audio3d_ports[port].next_push_ns;
    if (*next<now || *next>now+period*8) *next=now;
    if (blocking && *next>now) { struct timespec t={0,(long)(*next-now)}; nanosleep(&t,NULL); }
    *next+=period; return 0;
}
static ABI int32_t audio3d_queue_level(uint32_t port, uint32_t *level, uint32_t *available) {
    if (!audio3d_valid(port)) return AUDIO3D_INVALID_PORT;
    if (level) *level=0;
    if (available) *available=audio3d_ports[port].queue_depth;
    return 0;
}
static ABI int32_t audio3d_out_open(uint32_t port, int32_t user, int32_t type, int32_t index, uint32_t length, uint32_t freq, uint32_t param) {
    (void)port;
    int32_t (ABI *open)(int32_t,int32_t,int32_t,uint32_t,uint32_t,uint32_t)=runtime_audio_function("sceAudioOutOpen");
    return open ? open(user,type,index,length,freq,param) : (int32_t)0x80EA0001;
}
static ABI int32_t audio3d_out_close(int32_t handle) {
    int32_t (ABI *close)(int32_t)=runtime_audio_function("sceAudioOutClose");
    return close ? close(handle) : (int32_t)0x80EA0001;
}
static ABI int32_t audio3d_out_outputs(const void *params, uint32_t count) {
    int32_t (ABI *outputs)(const void *,uint32_t)=runtime_audio_function("sceAudioOutOutputs");
    return outputs ? outputs(params,count) : (int32_t)0x80EA0001;
}

/* --------------------------------------------------- AudioOut, Ajm extras ---- */
static ABI int32_t audio_system_state(void *state) { if (state) memset(state,0,16); return 0; }
/* ATRAC9 configuration data (4 bytes, MSB first): sync 0xFE, sample rate index (4 bits), channel
 * configuration index (3), a zero bit, frame bytes - 1 (11), superframe index (2); tables as in
 * LibAtrac9. The output layout is not documented: MLB The Show reads the frame samples at +0xC and
 * the frame bytes at +0x10 (it doubles both until a frame holds 480 samples, so 0 hung its audio
 * streaming thread). Sample rate, channels and frames per superframe go to +0, +4 and +8. */
static ABI int32_t at9_parse_config(const uint8_t *config, uint32_t *info) {
    static const uint32_t rates[16]={11025,12000,16000,22050,24000,32000,44100,48000,
                                     44100,48000,64000,88200,96000,128000,176400,192000};
    static const uint8_t frame_power[16]={6,6,7,7,7,8,8,8,6,6,7,7,7,8,8,8};
    static const uint8_t channels[6]={1,2,2,6,8,4};
    if (!config || !info) return (int32_t)0x80930005;              /* AJM_ERROR_INVALID_ADDRESS */
    const unsigned rate=config[1]>>4, layout=config[1]>>1&7;
    if (config[0]!=0xfe || (config[1]&1) || layout>5) return (int32_t)0x80930003; /* INVALID_PARAMETER */
    const unsigned frame_bytes=((unsigned)config[2]<<3|config[3]>>5)+1, superframe=config[3]>>3&3;
    info[0]=rates[rate];
    info[1]=channels[layout];
    info[2]=1u<<superframe;
    info[3]=1u<<frame_power[rate];
    info[4]=frame_bytes;
    return 0;
}

/* ------------------------------------------------- unavailable services ---- */
#define USB_STORAGE_NOT_FOUND ((int32_t)0x80fc0002)
static ABI int32_t usb_storage_init(void *param) { (void)param; ONCE("USB storage: no devices"); return 0; }
static ABI int32_t usb_storage_list(void *list) { if (list) memset(list,0,8); return 0; }
static ABI int32_t usb_storage_not_found(void) { return USB_STORAGE_NOT_FOUND; }
static ABI int32_t usb_storage_is_exist(void) { return 0; }
#define DIALOG_NOT_SUPPORTED ((int32_t)0x80B80001)
/* Dialog status: 0 none, 1 initialized, 2 running, 3 finished. Opening a dialog that does not
 * exist here is refused; the game sees it never ran. */
static ABI int32_t dialog_initialize(void) { return 0; }
static ABI int32_t dialog_open_refused(const void *param) { (void)param; ONCE("a system dialog was refused (browser, sign-in or USB)"); return DIALOG_NOT_SUPPORTED; }
static ABI int32_t dialog_status_initialized(void) { return 1; }
static ABI int32_t dialog_result_none(void *result) { (void)result; return DIALOG_NOT_SUPPORTED; }
static ABI int32_t gesture_open(int32_t input, const void *param) { (void)input; (void)param; return 1; }
static ABI int32_t coredump_register(void *handler, uint64_t stack, void *user) { (void)handler; (void)stack; (void)user; return 0; }
static ABI int32_t coredump_no_info(void) { return (int32_t)0x81180005; }
#define HMD_NOT_INITIALIZED ((int32_t)0x81110001)
static ABI int32_t hmd_unavailable(void) { return HMD_NOT_INITIALIZED; }
#define GAME_INTENT_NO_INTENT ((int32_t)0x80553105)
static ABI int32_t game_intent_receive(void *intent) { (void)intent; return GAME_INTENT_NO_INTENT; }
static ABI int32_t game_intent_property(const void *intent, const char *key, char *value, uint64_t size) {
    (void)intent; (void)key;
    if (value && size) value[0]=0;
    return (int32_t)0x80553102;
}

/* ------------------------------------------------------- small services ---- */
static ABI int32_t random_number(uint8_t *buffer, uint64_t size) {
    if (!buffer || size>64) return (int32_t)0x817C0016;
#ifdef _WIN32
    if (BCryptGenRandom(NULL,buffer,(ULONG)size,BCRYPT_USE_SYSTEM_PREFERRED_RNG)) return (int32_t)0x817C0016;
#else
    FILE *f=fopen("/dev/urandom","rb");
    if (!f || fread(buffer,1,size,f)!=size) { if (f) fclose(f); return (int32_t)0x817C0016; }
    fclose(f);
#endif
    return 0;
}
/* Temporary data: /temp0 is always mounted in the user folder (runtime_file.c). */
typedef struct { char data[16]; } MountPoint;
static ABI int32_t temp_mount2(int32_t option, MountPoint *mount) {
    (void)option;
    if (!mount) return (int32_t)0x80D90002;
    snprintf(mount->data,sizeof(mount->data),"/temp0");
    return 0;
}
static ABI int32_t temp_format(const MountPoint *mount) { (void)mount; return 0; }
static ABI int32_t temp_space(const MountPoint *mount, uint64_t *kb) { (void)mount; if (kb) *kb=4u<<20; return 0; }
/* Display safe area: the whole screen (ratio 1.0). */
static ABI int32_t safe_area(void *info) {
    if (!info) return (int32_t)0x80A10003;
    float ratio=1.0f; memset(info,0,128); memcpy(info,&ratio,sizeof(ratio)); return 0;
}
/* One argument: the setting is written to *value (the eboot calls it with a stack int and ORs it with the result). */
static ABI int32_t platform_privacy(int32_t *value) { if (!value) return (int32_t)0x80A10003; *value=0; return 0; }
/* User colour: blue (0), the first user's colour on a console. */
static ABI int32_t user_color(int32_t user, int32_t *color) { (void)user; if (!color) return (int32_t)0x80960005; *color=0; return 0; }
static ABI int32_t savedata_get_param(void) { return (int32_t)0x809F0008; } /* SAVE_DATA_ERROR_NOT_MOUNTED */
/* RFC 3339 (2024-03-28T19:05:00Z, with an optional fraction and offset) to ticks: microseconds since year 1. */
static ABI int32_t rtc_parse_rfc3339(uint64_t *tick, const char *text) {
    if (!tick || !text) return (int32_t)0x80B50002;
    int y,mo,d,h,mi; double s;
    if (sscanf(text,"%d-%d-%dT%d:%d:%lf",&y,&mo,&d,&h,&mi,&s)!=6) return (int32_t)0x80B50002;
    int a=(14-mo)/12, yy=y+4800-a, mm=mo+12*a-3;
    int64_t jdn=d+(153*mm+2)/5+365LL*yy+yy/4-yy/100+yy/400-32045;
    int64_t days=jdn-1721426; /* JDN of 0001-01-01 */
    int64_t us=((days*24+h)*60+mi)*60000000LL+(int64_t)(s*1000000.0);
    const char *zone=text+strcspn(text,"Zz+-");
    if (zone>text+10 && (*zone=='+' || *zone=='-')) {
        int zh=0,zm=0; sscanf(zone+1,"%d:%d",&zh,&zm);
        int64_t offset=(int64_t)(zh*60+zm)*60000000LL;
        us+=*zone=='+' ? -offset : offset;
    }
    *tick=(uint64_t)us; return 0;
}
static ABI int32_t heap_trace_info(void *info) { if (info) memset(info,0,16); return 0; }
static ABI int32_t disc_map_query(const char *path, int64_t offset, int64_t bytes, int *flags, int *a, int *b) {
    (void)path; (void)offset; (void)bytes;
    if (flags) *flags=0;
    if (a) *a=0;
    if (b) *b=0;
    return 0;
}
static ABI int32_t sysmodule_load_internal(uint32_t id, uint64_t argc, const void *argv, uint32_t flags, int32_t *result) {
    (void)argc; (void)argv; (void)flags;
    if (result) *result=0;
    printf("Runtime: sceSysmoduleLoadModuleInternalWithArg(%#x)\n",id);
    return 0;
}
static ABI int32_t ime_panel_size(const void *param, const void *extended, uint32_t *width, uint32_t *height) {
    (void)param; (void)extended;
    if (width) *width=793;
    if (height) *height=408;
    return 0;
}
static ABI int32_t ult_initialize(void) { ONCE("sceUltInitialize"); return 0; }

/* ----------------------------------------------------------------- table ---- */
static const RuntimeExport exports[]={
    /* libkernel */
    {"__elf_phdr_match_addr",elf_phdr_match_addr}, {"__pthread_cxa_finalize",pthread_cxa_finalize},
    {"_is_signal_return",is_signal_return}, {"_nanosleep",posix_nanosleep},
    {"_sceKernelRtldThreadAtexitDecrement",rtld_thread_atexit}, {"_sceKernelRtldThreadAtexitIncrement",rtld_thread_atexit},
    {"_sceKernelSetThreadAtexitCount",set_thread_atexit}, {"_sceKernelSetThreadAtexitReport",set_thread_atexit},
    {"_sceKernelSetThreadDtors",set_thread_dtors},
    {"sceKernelGetModuleInfoForUnwind",module_info_for_unwind}, {"sceKernelGetModuleInfoFromAddr",module_info_from_addr},
    {"sceSysmoduleGetModuleInfoForUnwind",module_info_for_unwind},
    {"sceKernelInternalMemoryGetModuleSegmentInfo",module_segment_info},
    {"sceKernelGetSanitizerMallocReplaceExternal",null_pointer}, {"sceKernelGetSanitizerNewReplaceExternal",null_pointer},
    {"sceKernelIsAddressSanitizerEnabled",ok_zero}, {"sceKernelIsNeoMode",is_neo_mode},
    {"sceKernelLoadStartModule",load_start_module}, {"sceKernelSleep",kernel_sleep},
    /* libSceJson2 */
    {"_ZN3sce4Json11Initializer10initializeEPKNS0_13InitParameterE",json_initialize},
    {"_ZN3sce4Json11InitializerC1Ev",json_initializer_ctor}, {"_ZN3sce4Json11InitializerD1Ev",json_initializer_dtor},
    {"_ZN3sce4Json12MemAllocator11notifyErrorEimPv",json_allocator_error},
    {"_ZN3sce4Json12MemAllocatorC2Ev",json_allocator_ctor}, {"_ZN3sce4Json12MemAllocatorD2Ev",json_allocator_dtor},
    {"_ZN3sce4Json5Value21setNullAccessCallBackEPFRKS1_NS0_9ValueTypeEPS2_PvES6_",json_set_null_callback},
    {"_ZN3sce4Json5Value5clearEv",json_value_clear}, {"_ZN3sce4Json5ValueC1Ev",json_value_ctor},
    {"_ZN3sce4Json5ValueD1Ev",json_value_dtor}, {"_ZN3sce4Json6Parser5parseERNS0_5ValueEPKcm",json_parse},
    {"_ZN3sce4Json6StringC1ERKS1_",json_string_copy}, {"_ZN3sce4Json6StringD1Ev",json_string_dtor},
    {"_ZNK3sce4Json5Value5countEv",json_value_count}, {"_ZNK3sce4Json5Value7getTypeEv",json_value_type},
    {"_ZNK3sce4Json5Value9getStringEv",json_value_string}, {"_ZNK3sce4Json5ValueixEPKc",json_value_key},
    {"_ZNK3sce4Json5ValueixEm",json_value_index}, {"_ZNK3sce4Json6String5c_strEv",json_string_cstr},
    {"_ZNK3sce4Json6String5emptyEv",json_string_empty}, {"_ZNK3sce4Json6String7compareEPKc",json_string_compare},
    /* libSceAudio3d */
    {"sceAudio3dInitialize",audio3d_initialize}, {"sceAudio3dTerminate",audio3d_terminate},
    {"sceAudio3dPortOpen",audio3d_port_open}, {"sceAudio3dPortClose",audio3d_port_close},
    {"sceAudio3dPortGetAttributesSupported",audio3d_port_attributes}, {"sceAudio3dPortSetAttribute",audio3d_port_ok},
    {"sceAudio3dPortAdvance",audio3d_port_ok}, {"sceAudio3dPortPush",audio3d_port_push},
    {"sceAudio3dPortGetQueueLevel",audio3d_queue_level},
    {"sceAudio3dObjectReserve",audio3d_object_reserve}, {"sceAudio3dObjectUnreserve",audio3d_port_ok},
    {"sceAudio3dObjectSetAttributes",audio3d_port_ok},
    {"sceAudio3dAudioOutOpen",audio3d_out_open}, {"sceAudio3dAudioOutClose",audio3d_out_close},
    {"sceAudio3dAudioOutOutputs",audio3d_out_outputs},
    /* AudioOut, Ajm */
    {"sceAudioOutGetSystemState",audio_system_state}, {"sceAudioOutMasteringInit",ok_zero},
    {"sceAudioOutSetMixLevelPadSpk",ok_zero},
    {"sceAjmDecAt9ParseConfigData",at9_parse_config}, {"sceAjmMemoryUnregister",ok_zero},
    /* USB storage and its dialog */
    {"sceUsbStorageInit",usb_storage_init}, {"sceUsbStorageGetDeviceList",usb_storage_list},
    {"sceUsbStorageGetDeviceInfo",usb_storage_not_found}, {"sceUsbStorageIsExist",usb_storage_is_exist},
    {"sceUsbStorageRegisterCallback",ok_zero}, {"sceUsbStorageRequestMap",usb_storage_not_found},
    {"sceUsbStorageRequestUnmap",usb_storage_not_found},
    {"sceUsbStorageDialogInitialize",dialog_initialize}, {"sceUsbStorageDialogOpen",dialog_open_refused},
    {"sceUsbStorageDialogUpdateStatus",dialog_status_initialized}, {"sceUsbStorageDialogGetResult",dialog_result_none},
    {"sceUsbStorageDialogTerminate",ok_zero},
    /* Web browser and PSN sign-in dialogs */
    {"sceWebBrowserDialogInitialize",dialog_initialize}, {"sceWebBrowserDialogOpen",dialog_open_refused},
    {"sceWebBrowserDialogUpdateStatus",dialog_status_initialized}, {"sceWebBrowserDialogTerminate",ok_zero},
    {"sceSigninDialogInitialize",dialog_initialize}, {"sceSigninDialogOpen",dialog_open_refused},
    {"sceSigninDialogUpdateStatus",dialog_status_initialized}, {"sceSigninDialogTerminate",ok_zero},
    {"sceErrorDialogClose",ok_zero}, {"sceImeDialogGetPanelSizeExtended",ime_panel_size},
    /* Touch pad gestures: no touch events */
    {"sceSystemGestureOpen",gesture_open}, {"sceSystemGestureClose",ok_zero},
    {"sceSystemGestureCreateTouchRecognizer",ok_zero}, {"sceSystemGestureResetTouchRecognizer",ok_zero},
    {"sceSystemGestureUpdateTouchRecognizer",ok_zero}, {"sceSystemGestureGetTouchEventsCount",ok_zero},
    {"sceSystemGestureGetTouchEventByIndex",ok_zero}, {"sceSystemGestureGetTouchEventByEventID",ok_zero},
    {"sceSystemGestureInitializePrimitiveTouchRecognizer",ok_zero},
    {"sceSystemGestureUpdatePrimitiveTouchRecognizer",ok_zero},
    {"sceSystemGestureResetPrimitiveTouchRecognizer",ok_zero},
    {"sceSystemGestureFinalizePrimitiveTouchRecognizer",ok_zero},
    /* Core dumps, PS VR, game intents, live streaming, share play, screenshots, recording */
    {"sceCoredumpRegisterCoredumpHandler",coredump_register}, {"sceCoredumpUnregisterCoredumpHandler",ok_zero},
    {"sceCoredumpWriteUserData",ok_zero}, {"sceCoredumpGetStopInfoGpu",coredump_no_info},
    {"sceHmdReprojectionAddDisplayBuffer",hmd_unavailable}, {"sceHmdReprojectionSetDisplayBuffers",hmd_unavailable},
    {"sceNpGameIntentInitialize",ok_zero}, {"sceNpGameIntentTerminate",ok_zero},
    {"sceNpGameIntentReceiveIntent",game_intent_receive}, {"sceNpGameIntentGetPropertyValueString",game_intent_property},
    {"sceGameLiveStreamingInitialize",ok_zero}, {"sceGameLiveStreamingTerminate",ok_zero},
    {"sceGameLiveStreamingPermitLiveStreaming",ok_zero}, {"sceGameLiveStreamingPermitServerSideRecording",ok_zero},
    {"sceSharePlayInitialize",ok_zero}, {"sceSharePlaySetProhibition",ok_zero},
    {"sceScreenShotDisable",ok_zero}, {"sceScreenShotSetParam",ok_zero},
    {"sceVideoRecordingSetInfo",ok_zero}, {"sceVideoOutRegisterStereoBuffers",ok_zero},
    /* Small services */
    {"sceRandomGetRandomNumber",random_number},
    {"sceAppContentTemporaryDataMount2",temp_mount2}, {"sceAppContentTemporaryDataFormat",temp_format},
    {"sceAppContentTemporaryDataGetAvailableSpaceKb",temp_space},
    {"sceSystemServiceGetDisplaySafeAreaInfo",safe_area}, {"sceSystemServiceGetPlatformPrivacySetting",platform_privacy},
    {"sceUserServiceGetUserColor",user_color}, {"sceSaveDataGetParam",savedata_get_param},
    {"sceRtcParseRFC3339",rtc_parse_rfc3339}, {"sceLibcHeapGetTraceInfo",heap_trace_info},
    {"sceSysmoduleLoadModuleInternalWithArg",sysmodule_load_internal},
    {"scePlayGoClose",ok_zero}, {"scePlayGoTerminate",ok_zero}, {"sceUltInitialize",ult_initialize},
};
/* Imports aerolib has no name for, by scoped NID. */
static const struct { const char *nid; void *function; } unnamed[]={
    {"fJgP+wqifno",disc_map_query},  /* libSceDiscMap: is a file range on the disc (no) */
};
uintptr_t runtime_mlb_resolve(const char *name) {
    uintptr_t found=RUNTIME_LOOKUP(exports,name);
    if (found) return found;
    for (size_t i=0;i<sizeof(unnamed)/sizeof(*unnamed);++i)
        if (!strncmp(name,unnamed[i].nid,11) && (name[11]=='#' || !name[11])) return (uintptr_t)unnamed[i].function;
    return 0;
}
