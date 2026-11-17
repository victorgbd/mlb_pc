/* The games' display configuration: one init function per executable stores the screen size
 * (1920x1080), the aspect ratio (16:9 as a float) and the sizes derived from it into a global
 * settings block that the whole renderer reads. Rewriting the immediates of that function
 * makes the game allocate and render everything at another size.
 *
 * BB_GAME_RES=WxH   the game's render and display size (default: untouched, 1920x1080).
 *
 * The init function is found by its code, not by an offset, so every version of the three
 * executables is covered:  mov [r+8],1920  mov [r+0xc],1080 ... stores of 1920/1080 at
 * +0x18/+0x1c, +0x24/+0x28, +0x38/+0x3c, the aspect 0x3fe38e39 at +0x20/+0x2c, and the two
 * values derived from the size (1920/8 and 1080/4) at +0x40/+0x44.
 *
 * Larger targets need a larger GPU memory budget (see grow_budget): run.py computes the
 * amounts (BB_GAME_RT_MB, BB_GAME_RT2_MB, BB_GAME_FREE_MB) and the system direct memory. */
#define _GNU_SOURCE
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void put32(unsigned char *p, uint32_t v) { memcpy(p,&v,4); }
static uint32_t get32(const unsigned char *p) { uint32_t v; memcpy(&v,p,4); return v; }
static uint32_t float_bits(float f) { uint32_t v; memcpy(&v,&f,4); return v; }

/* One size block: `start` is the first store (mov [r+8],1920). Patches the stores in the next
 * `window` bytes that match the known (disp, immediate) pairs; returns how many it changed. */
/* BB_GAME_SKIP=0x38,0x18: block fields (displacements) left at 1920x1080 (diagnostics). */
static uint64_t skip_disp;
static int patch_block(unsigned char *code, size_t start, size_t window, size_t size, uint32_t w, uint32_t h) {
    const unsigned char reg=code[start+1];
    int changed=0;
    uint32_t aspect=float_bits((float)w/(float)h);
    for (size_t i=start;i+7<=start+window && i+7<=size;++i) {
        if (code[i]==0xc7 && code[i+1]==reg) {            /* mov dword [r+disp8], imm32 */
            const unsigned disp=code[i+2];
            const uint32_t imm=get32(code+i+3);
            uint32_t value=imm;
            if (imm==1920 && (disp==0x08 || disp==0x18 || disp==0x24 || disp==0x38)) value=w;
            else if (imm==1080 && (disp==0x0c || disp==0x1c || disp==0x28 || disp==0x3c)) value=h;
            else if (imm==0x3fe38e39 && (disp==0x20 || disp==0x2c)) value=aspect;
            else if (imm==0xf0 && disp==0x40) value=(w+7)/8;
            else if (imm==0x10e && disp==0x44) value=(h+3)/4;
            if (value!=imm && !(skip_disp>>(disp/4)&1)) { put32(code+i+3,value); ++changed; i+=6; }
        } else if (i+10<=size && code[i]==0x48 && code[i+1]==0xb8 && get32(code+i+2)==1920 && get32(code+i+6)==1080) {
            put32(code+i+2,w); put32(code+i+6,h); ++changed;   /* movabs rax, (1080<<32)|1920 -> [r+0x38] */
            i+=9;
        }
    }
    return changed;
}

/* The memory budget table of each executable (module data): entries {u32 id, u32 type, u64 size,
 * u64 alignment, char *name} of 32 bytes that end at id 0. The game checks that every type's
 * entries add up to a constant (type 2: the system direct memory, 4608 MiB, whose entry 3 is
 * the GPU pool; type 6: the GPU pool's heaps). The heaps that hold the render targets are
 * entry 133 (497 MiB; 561 in Uncharted 3) and entry 132 (140 MiB) of type 6; entry 14 is the
 * pool's unreserved remainder. Their allocation failures name them ("Render Target Heap").
 * The pool is one arena whose size the game keeps in 32 bits: it must stay below 4 GiB. */
static int change_entry(unsigned char *image, uint64_t image_size, uint32_t id, uint32_t type, uint64_t min_mb, uint64_t max_mb,
                        int64_t delta_mb, const char *what) {
    int changed=0;
    for (uint64_t i=0;i+32<=image_size;i+=8) {
        if (get32(image+i)!=id || get32(image+i+4)!=type) continue;
        uint64_t size, alignment;
        memcpy(&size,image+i+8,8); memcpy(&alignment,image+i+16,8);
        if (alignment==0 || (alignment&(alignment-1)) || size<(min_mb<<20) || size>(max_mb<<20)) continue;
        if (delta_mb<0 && size<((uint64_t)(-delta_mb)<<20)) continue;
        size=(uint64_t)((int64_t)size+(delta_mb<<20));
        memcpy(image+i+8,&size,8);
        printf("Display: %s (image offset %#llx) %+lld MiB, now %llu MiB\n",what,(unsigned long long)i,(long long)delta_mb,
               (unsigned long long)(size>>20));
        ++changed;
    }
    return changed;
}

static int64_t env_mb(const char *name) {
    const char *v=getenv(name);
    return v && *v ? (int64_t)strtoll(v,NULL,10) : 0;
}

static void grow_budget(unsigned char *image, uint64_t image_size) {
    const int64_t rt=env_mb("BB_GAME_RT_MB"), rt2=env_mb("BB_GAME_RT2_MB"), freed=env_mb("BB_GAME_FREE_MB"), tex=env_mb("BB_GAME_TEX_MB");
    const int64_t pool=rt+rt2-freed-tex;
    if (!rt && !rt2 && !freed && !tex) return;
    if (rt) change_entry(image,image_size,133,6,300,1500,rt,"render target heap");
    if (rt2) change_entry(image,image_size,132,6,100,1000,rt2,"second render target heap");
    if (freed) change_entry(image,image_size,14,6,(uint64_t)freed+4,1000,-freed,"unreserved GPU memory");
    if (tex) change_entry(image,image_size,134,6,(uint64_t)tex+256,2000,-tex,"texture heap");
    if (pool) change_entry(image,image_size,3,2,2000,4000,pool,"GPU memory pool");
    /* The memory validation function compares the system total with a constant
       (movabs rdx, 0x120000000 = 4608 MiB): the constant moves with the pool. */
    static const unsigned char total[10]={0x48,0xba,0,0,0,0x20,1,0,0,0};
    for (uint64_t i=0;i+sizeof(total)<=image_size;++i) {
        if (memcmp(image+i,total,sizeof(total))) continue;
        uint64_t value=(uint64_t)((int64_t)0x120000000ll+(pool<<20));
        memcpy(image+i+2,&value,8);
        printf("Display: memory total check (image offset %#llx) now %llu MiB\n",(unsigned long long)i,(unsigned long long)(value>>20));
    }
}

/* The movie player gives the video decoder 4 frame buffers of 4 MiB (enough for 1920x1088 NV12) and the
 * games read the decoded frame at their display size, so at larger sizes a frame needs more (BB_GAME_MOVIE_MB;
 * the HLE decoder fits the movie into the display size). Registers differ per executable; the code is:
 *   add r64, 0x400000 / mov qword [slot+0x225c0], 0x400000 / ... mov qword [..], 0x30 ...   (per slot)
 *   add r64, 0x400000 / sub r64, r64 / mov eax, 0xffffffff / and r64, rax                (the offset before them) */
static int patch_movie_slots(unsigned char *image, uint64_t image_size, uint32_t bytes) {
    int patched=0;
    for (uint64_t i=0;i+64<image_size;++i) {
        const unsigned char *p=image+i;
        /* the decode path's check of the frame buffer size: cmp qword [r+0x10], 0x400000 */
        if ((p[0]==0x48 || p[0]==0x49) && p[1]==0x81 && p[2]>=0x78 && p[2]<=0x7f && p[3]==0x10 && get32(p+4)==0x400000) { put32(image+i+4,bytes); ++patched; continue; }
        if (!(p[0]==0x48 || p[0]==0x49 || p[0]==0x4c || p[0]==0x4d) || p[1]!=0x81 || (p[2]&0xf8)!=0xc0 || get32(p+3)!=0x400000) continue;
        int site=0;
        /* per-slot: followed by mov qword [mem], 0x400000 and a store of 0x30 */
        if ((p[7]&0xf8)==0x48 && p[8]==0xc7 && p[9]==0x84 && get32(p+15)==0x400000) {
            for (int k=18;k<56;++k) if (p[k]==0xc7 && p[k+1]==0x84 && get32(p+k+7)==0x30) { site=1; break; }
            if (site) { put32(image+i+3,bytes); put32(image+i+15,bytes); patched+=2; continue; }
        }
        /* offset before the slots: add r, 0x400000 ; sub r, r ; mov eax, 0xffffffff ; and r, rax */
        if ((p[7]&0xf8)==0x48 && p[8]==0x29 && p[10]==0xb8 && get32(p+11)==0xffffffff) { put32(image+i+3,bytes); ++patched; }
    }
    return patched;
}

/* MLB The Show: BB_MLB_RES=WxH, the game's render and display size (another shape: 16:10, 21:9,
 * 32:9). The video out setup picks its size from two tables in rodata, {1920x1080, 2560x1440,
 * 3200x1800, 3840x2160} (PS4 Pro) followed by {1280x720, 1920x1080}, and copies the entry into the
 * renderer's size globals; the 1920x1080 entries are rewritten. The renderer reads the size from
 * there (projection, post-processing and the UI layout follow it), but the display frame buffers
 * are allocated for a fixed 1920x1080 (mov edx,0x780; mov ecx,0x438; mov r8d,1; the surface size
 * call), which is patched too.
 * Limit: above a 1920x1088 area of 128x64-aligned pixels the title screen draws with a depth target
 * whose registers are garbage (z_format 2) and the GPU stops; that memory has not been found, so
 * larger sizes are refused.
 * The UI (HUD, scoreboard, presentation banners) is not drawn at all in a match when the height is
 * not a multiple of 8 (2688x756 and 2432x810 had none; 2688x760, 2560x720, 2176x928 do), so both
 * sides are rounded to the nearest multiple of 8. */
static void patch_mlb_table(unsigned char *image, uint64_t image_size) {
    const char *res=getenv("BB_MLB_RES");
    unsigned w=0, h=0;
    if (!res || sscanf(res,"%ux%u",&w,&h)!=2) return;
    w=(w+4)&~7u; h=(h+4)&~7u;
    if (w==1920 && h==1080) return;
    const uint64_t area=(uint64_t)((w+127)&~127u)*((h+63)&~63u);
    if (w<640 || h<360 || area>UINT64_C(1920)*1088) {
        printf("Display: BB_MLB_RES %ux%u refused (the game works up to a 1920x1088 area, 128x64 aligned)\n",w,h);
        return;
    }
    static const uint32_t sig[12]={1920,1080,2560,1440,3200,1800,3840,2160,1280,720,1920,1080};
    int found=0;
    for (uint64_t i=0;i+sizeof(sig)<=image_size;i+=4) {
        if (memcmp(image+i,sig,sizeof(sig))) continue;
        put32(image+i,w); put32(image+i+4,h);
        put32(image+i+40,w); put32(image+i+44,h);
        ++found;
    }
    static const unsigned char fb[]={0xba,0x80,0x07,0,0,0xb9,0x38,0x04,0,0,0x41,0xb8,1,0,0,0};
    int buffers=0;
    for (uint64_t i=0;i+sizeof(fb)<=image_size;++i) {
        if (memcmp(image+i,fb,sizeof(fb))) continue;
        put32(image+i+1,w); put32(image+i+6,h); ++buffers;
    }
    /* The main camera's aspect ratio (camera +0x318) comes from a constant table {16/9, 4/3}
       (lea rcx,[table]; vmovss xmm0,[rcx+rdx*4]), not from the size. The picture follows the size
       globals, but culling and the SSAO's view reconstruction used this aspect: at a wider size the
       ground pieces at the sides were culled (black bands on the field) and the 3D view was
       stretched sideways. */
    int aspect=0;
    {
        static const uint32_t table[2]={0x3fe38e39,0x3faaaaab};
        const float value=(float)w/(float)h;
        for (uint64_t i=0;i+sizeof(table)<=image_size;i+=4) {
            if (memcmp(image+i,table,sizeof(table))) continue;
            memcpy(image+i,&value,4); ++aspect;
        }
    }
    /* Render targets created with a fixed fraction of 1920x1080 instead of the size globals
       (mov esi,W; mov edx,H before the create call): half size 960x544 (the SSAO chain: Linear Depth
       Buffer, SSAOOffscreen, AO Buffer 2) and 1/8 size 240x136 (ExposureWeightingRT, DownsampledFB).
       The SSAO setup also stores their texel size as a constant pair (movabs rax, {1/960, 1/544}).
       The full size ones (ScratchSurface0-2, ZBuffer1920, SubsurfaceRT/Final, spfx, depth, snapshot)
       stay 1920x1080: resized, the skin (subsurface) turned black. */
    const unsigned half_w=((w+1)/2+15)&~15u, half_h=((h+1)/2+15)&~15u;
    const unsigned eighth_w=((w+7)/8+7)&~7u, eighth_h=((h+7)/8+7)&~7u;
    const struct { uint32_t w, h, new_w, new_h; } sizes[]={{960,544,half_w,half_h}, {240,136,eighth_w,eighth_h}};
    int targets=0;
    for (size_t s=0;s<sizeof(sizes)/sizeof(sizes[0]);++s) {
        for (uint64_t i=0;i+10<=image_size;++i) {
            unsigned char *p=image+i;
            if (p[0]==0xbe && get32(p+1)==sizes[s].w && p[5]==0xba && get32(p+6)==sizes[s].h) {
                put32(p+1,sizes[s].new_w); put32(p+6,sizes[s].new_h); ++targets; i+=9;
            }
        }
    }
    int texel=0;
    {
        const float inv[2]={1.0f/(float)half_w,1.0f/(float)half_h};
        static const unsigned char old[10]={0x48,0xb8,0x89,0x88,0x88,0x3a,0xf1,0xf0,0xf0,0x3a};
        for (uint64_t i=0;i+sizeof(old)<=image_size;++i) {
            if (memcmp(image+i,old,sizeof(old))) continue;
            memcpy(image+i+2,inv,8); ++texel;
        }
    }
    printf("Display: BB_MLB_RES %ux%u: %d size table(s), %d frame buffer allocation(s), %d camera aspect(s), "
           "%d reduced render targets (half %ux%u), %d SSAO texel size(s)\n",w,h,found,buffers,aspect,targets,
           half_w,half_h,texel);
}

void runtime_display_patch(unsigned char *image, uint64_t image_size) {
    patch_mlb_table(image,image_size);
    const char *res=getenv("BB_GAME_RES");
    unsigned w=0, h=0;
    if (!res || sscanf(res,"%ux%u",&w,&h)!=2 || w<640 || h<360 || w>7680 || h>4320 || (w==1920 && h==1080)) return;
    w&=~1u; h&=~1u;
    grow_budget(image,image_size);
    for (const char *t=getenv("BB_GAME_SKIP");t && *t;) { char *end; unsigned long d=strtoul(t,&end,16); if (end==t) break; if (d<256) skip_disp|=1ull<<(d/4); t=*end ? end+1 : end; }
    size_t blocks=0, stores=0;
    /* mov dword [r+8],1920 ; mov dword [r+0xc],1080  (same base register) */
    for (uint64_t i=0;i+14<=image_size;++i) {
        if (image[i]!=0xc7 || (image[i+1]&0xf8)!=0x40 || image[i+2]!=0x08 || get32(image+i+3)!=1920) continue;
        if (image[i+7]!=0xc7 || image[i+8]!=image[i+1] || image[i+9]!=0x0c || get32(image+i+10)!=1080) continue;
        stores+=(size_t)patch_block(image,(size_t)i,0x1e0,(size_t)image_size,w,h);
        ++blocks;
    }
    { const int64_t movie=env_mb("BB_GAME_MOVIE_MB"); if (movie>4) printf("Display: movie frame buffers %lld MiB: %d places\n",(long long)movie,patch_movie_slots(image,image_size,(uint32_t)movie<<20)); }
    printf("Display: BB_GAME_RES %ux%u: %zu configuration block(s), %zu stores changed\n",w,h,blocks,stores);
}
