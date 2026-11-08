/* libScePngDec and libSceJpegDec on stb_image (third_party/stb, public domain).
 * Structures and return values follow shadPS4's implementations (src/core/libraries/libpng and
 * jpeg). Decoding always goes through 8-bit RGBA; 16-bit PNGs are reduced to 8 bits, as the
 * PS4 decoder does without the 16-bit attribute. */
#define _GNU_SOURCE
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Weverything"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wimplicit-fallthrough"
#pragma GCC diagnostic ignored "-Wtype-limits"
#pragma GCC diagnostic ignored "-Wmisleading-indentation"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#endif
#define STB_IMAGE_IMPLEMENTATION
#define STB_IMAGE_STATIC
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#include "third_party/stb/stb_image.h"
#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

#define HANDLE_MAGIC UINT32_C(0x44454349) /* "ICED" */
typedef struct { uint32_t magic, max_width; } Decoder;

/* ---- PNG ---- */
#define PNG_INVALID_ADDR ((int32_t)0x80690001)
#define PNG_INVALID_SIZE ((int32_t)0x80690002)
#define PNG_INVALID_PARAM ((int32_t)0x80690003)
#define PNG_INVALID_HANDLE ((int32_t)0x80690004)
#define PNG_INVALID_DATA ((int32_t)0x80690010)
#define PNG_DECODE_ERROR ((int32_t)0x80690012)
typedef struct { const uint8_t *data; uint32_t size, reserved; } PngParse;
typedef struct { uint32_t width, height; uint16_t color_space, bit_depth; uint32_t flags; } PngInfo;
typedef struct { uint32_t this_size, attribute, max_width; } PngCreate;
typedef struct {
    const uint8_t *data; uint8_t *image; uint32_t size, image_size;
    uint16_t pixel_format, alpha; uint32_t pitch;
} PngDecode;

static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]; }
/* Reads IHDR and looks for tRNS before the image data. */
static int32_t png_header(const uint8_t *data, uint32_t size, PngInfo *info) {
    static const uint8_t signature[8]={0x89,'P','N','G','\r','\n',0x1a,'\n'};
    if (!data) return PNG_INVALID_ADDR;
    if (size<33 || memcmp(data,signature,8) || memcmp(data+12,"IHDR",4)) return PNG_INVALID_DATA;
    static const uint16_t spaces[7]={2,0,3,4,18,0,19};
    uint8_t type=data[25];
    if (type>6 || !spaces[type]) return PNG_INVALID_DATA;
    PngInfo r={be32(data+16),be32(data+20),spaces[type],data[24],data[28]==1 ? 1u : 0u};
    for (uint32_t at=8; at+8<=size;) {
        uint32_t length=be32(data+at);
        if (!memcmp(data+at+4,"tRNS",4)) { r.flags|=2; break; }
        if (!memcmp(data+at+4,"IDAT",4) || length>size-at-8) break;
        at+=length+12;
    }
    if (info) *info=r;
    return 0;
}
static ABI int32_t png_query(const PngCreate *param) {
    if (!param) return PNG_INVALID_PARAM;
    if (param->attribute>1) return PNG_INVALID_ADDR;
    if (param->max_width-1>1000000) return PNG_INVALID_SIZE;
    return (int32_t)sizeof(Decoder);
}
static ABI int32_t png_create(const PngCreate *param, void *memory, uint32_t size, void **handle) {
    if (!param || param->attribute>1) return PNG_INVALID_PARAM;
    if (!memory || !handle) return PNG_INVALID_ADDR;
    if (size<sizeof(Decoder) || param->max_width-1>1000000) return PNG_INVALID_SIZE;
    *(Decoder *)memory=(Decoder){HANDLE_MAGIC,param->max_width};
    *handle=memory; return 0;
}
static ABI int32_t png_delete(void *handle) {
    if (!handle || ((Decoder *)handle)->magic!=HANDLE_MAGIC) return PNG_INVALID_HANDLE;
    ((Decoder *)handle)->magic=0; return 0;
}
static ABI int32_t png_parse(const PngParse *param, PngInfo *info) {
    if (!param) return PNG_INVALID_PARAM;
    return png_header(param->data,param->size,info);
}
static ABI int32_t png_decode(void *handle, const PngDecode *param, PngInfo *info) {
    if (!handle || ((Decoder *)handle)->magic!=HANDLE_MAGIC) return PNG_INVALID_HANDLE;
    if (!param) return PNG_INVALID_PARAM;
    if (!param->data || !param->image) return PNG_INVALID_ADDR;
    PngInfo header;
    int32_t r=png_header(param->data,param->size,&header);
    if (r) return r;
    if (info) *info=header;
    int width, height, channels;
    stbi_uc *pixels=stbi_load_from_memory(param->data,(int)param->size,&width,&height,&channels,4);
    if (!pixels) return PNG_DECODE_ERROR;
    const uint32_t row=(uint32_t)width*4, pitch=param->pitch ? param->pitch : row;
    if (pitch<row || (uint64_t)pitch*(uint64_t)(height-1)+row>param->image_size) { stbi_image_free(pixels); return PNG_INVALID_SIZE; }
    /* Images without an alpha channel or tRNS get the caller's alpha value. */
    const int fill_alpha=(header.color_space==2 || header.color_space==3 || header.color_space==4) && !(header.flags&2);
    for (int y=0;y<height;++y) {
        const stbi_uc *src=pixels+(size_t)y*row;
        uint8_t *dst=param->image+(size_t)y*pitch;
        for (int x=0;x<width;++x, src+=4, dst+=4) {
            dst[0]=param->pixel_format==1 ? src[2] : src[0];
            dst[1]=src[1];
            dst[2]=param->pixel_format==1 ? src[0] : src[2];
            dst[3]=fill_alpha ? (uint8_t)param->alpha : src[3];
        }
    }
    stbi_image_free(pixels);
    return width>32767 || height>32767 ? 0 : (int32_t)((uint32_t)width<<16 | (uint32_t)height);
}

/* ---- JPEG ---- */
#define JPEG_INVALID_ADDR ((int32_t)0x80650001)
#define JPEG_INVALID_SIZE ((int32_t)0x80650002)
#define JPEG_INVALID_PARAM ((int32_t)0x80650003)
#define JPEG_INVALID_HANDLE ((int32_t)0x80650004)
#define JPEG_EXCEED_MAX_WIDTH ((int32_t)0x80650006)
#define JPEG_INVALID_DATA ((int32_t)0x80650010)
#define JPEG_DECODE_ERROR ((int32_t)0x80650012)
typedef struct { uint32_t size, decode_mode, max_width; } JpegCreate;
typedef struct { const void *data; uint32_t size; uint16_t decode_mode, ratio; } JpegParse;
typedef struct {
    uint32_t width, height; uint16_t color_space, components;
    uint8_t component_id[4], sampling[4];
    uint32_t decode_mode, required_mode, output_width, output_height;
} JpegInfo;
typedef struct {
    const void *data; void *image, *aux; uint32_t size, image_size, aux_size;
    uint16_t decode_mode, ratio, pixel_format, alpha; uint32_t pitch;
} JpegDecode;
_Static_assert(sizeof(JpegInfo)==0x24,"JpegInfo layout");
_Static_assert(sizeof(JpegDecode)==0x30,"JpegDecode layout");

static ABI int32_t jpeg_query(const JpegCreate *param) {
    if (!param) return JPEG_INVALID_ADDR;
    if (param->size!=sizeof(JpegCreate)) return JPEG_INVALID_SIZE;
    if (param->decode_mode<1 || param->decode_mode>2 || !param->max_width || param->max_width>0xffff) return JPEG_INVALID_PARAM;
    uint32_t w=((param->max_width+0x1f)&~0x1fu)|0x10;
    uint32_t size=param->decode_mode==2 ? (w<<5)*5 : (w<<4)*3;
    return (int32_t)(size+w*6+0x7360);
}
static ABI int32_t jpeg_create(const JpegCreate *param, void *memory, uint32_t size, void **handle) {
    if (!memory || !handle) return JPEG_INVALID_ADDR;
    int32_t need=jpeg_query(param);
    if (need<0) return need;
    if (size<(uint32_t)need) return JPEG_INVALID_SIZE;
    *(Decoder *)memory=(Decoder){HANDLE_MAGIC,param->max_width};
    *handle=memory; return 0;
}
static ABI int32_t jpeg_delete(void *handle) {
    if (!handle || ((Decoder *)handle)->magic!=HANDLE_MAGIC) return JPEG_INVALID_HANDLE;
    ((Decoder *)handle)->magic=0; return 0;
}
static uint32_t ratio_of(uint16_t ratio) { return ratio==2 || ratio==4 || ratio==8 ? ratio : 1; }
static void jpeg_fill_info(JpegInfo *info, int w, int h, int channels, uint16_t mode, uint32_t ratio) {
    memset(info,0,sizeof(*info));
    info->width=(uint32_t)w; info->height=(uint32_t)h;
    info->color_space=channels==1 ? 2 : 1; info->components=(uint16_t)channels;
    info->component_id[0]=1; info->component_id[1]=2; info->component_id[2]=3;
    info->sampling[0]=0x22; info->sampling[1]=0x11; info->sampling[2]=0x11;
    info->decode_mode=mode; info->required_mode=1;
    info->output_width=((uint32_t)w+ratio-1)/ratio; info->output_height=((uint32_t)h+ratio-1)/ratio;
}
static ABI int32_t jpeg_parse(const JpegParse *param, JpegInfo *info) {
    if (!param || !info) return JPEG_INVALID_ADDR;
    if (!param->data) return JPEG_INVALID_ADDR;
    int w, h, channels;
    if (!stbi_info_from_memory(param->data,(int)param->size,&w,&h,&channels)) return JPEG_INVALID_DATA;
    jpeg_fill_info(info,w,h,channels,param->decode_mode,ratio_of(param->ratio));
    return 0;
}
static ABI int32_t jpeg_decode(void *handle, const JpegDecode *param, JpegInfo *info) {
    Decoder *d=handle;
    if (!d || d->magic!=HANDLE_MAGIC) return JPEG_INVALID_HANDLE;
    if (!param || !param->data || !param->image) return JPEG_INVALID_ADDR;
    if (param->pixel_format!=0 && param->pixel_format!=1 && param->pixel_format!=11) return JPEG_INVALID_PARAM;
    int w, h, channels;
    stbi_uc *pixels=stbi_load_from_memory(param->data,(int)param->size,&w,&h,&channels,4);
    if (!pixels) return JPEG_DECODE_ERROR;
    if ((uint32_t)w>d->max_width) { stbi_image_free(pixels); return JPEG_EXCEED_MAX_WIDTH; }
    const uint32_t ratio=ratio_of(param->ratio);
    const uint32_t out_w=((uint32_t)w+ratio-1)/ratio, out_h=((uint32_t)h+ratio-1)/ratio;
    const uint32_t bpp=param->pixel_format==11 ? 1 : 4, pitch=param->pitch ? param->pitch : out_w*bpp;
    if (pitch<out_w*bpp || (uint64_t)pitch*out_h>param->image_size) { stbi_image_free(pixels); return JPEG_INVALID_SIZE; }
    for (uint32_t y=0;y<out_h;++y) {
        uint8_t *dst=(uint8_t *)param->image+(size_t)y*pitch;
        for (uint32_t x=0;x<out_w;++x) {
            const stbi_uc *src=pixels+((size_t)(y*ratio)*(uint32_t)w+x*ratio)*4;
            if (bpp==1) { dst[x]=(uint8_t)((src[0]*77+src[1]*150+src[2]*29)>>8); continue; }
            dst[x*4+0]=param->pixel_format==1 ? src[2] : src[0];
            dst[x*4+1]=src[1];
            dst[x*4+2]=param->pixel_format==1 ? src[0] : src[2];
            dst[x*4+3]=param->alpha ? (uint8_t)param->alpha : src[3];
        }
    }
    stbi_image_free(pixels);
    if (info) jpeg_fill_info(info,w,h,channels,param->decode_mode,ratio);
    return (int32_t)(out_w<<16 | (out_h&0xffff));
}

static const RuntimeExport exports[]={
    {"scePngDecQueryMemorySize",png_query}, {"scePngDecCreate",png_create}, {"scePngDecDelete",png_delete},
    {"scePngDecParseHeader",png_parse}, {"scePngDecDecode",png_decode},
    {"sceJpegDecQueryMemorySize",jpeg_query}, {"sceJpegDecCreate",jpeg_create}, {"sceJpegDecDelete",jpeg_delete},
    {"sceJpegDecParseHeader",jpeg_parse}, {"sceJpegDecDecode",jpeg_decode},
};
uintptr_t runtime_imagedec_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
