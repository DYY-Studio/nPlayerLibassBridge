/*
 * Thin legacy-name shims over the modern FFmpeg utility libraries.
 *
 * nPlayer 3.13.0 links the FFmpeg 4.4 scaler and resampler. Every function
 * below keeps the 4.4 prototype and forwards to the modern implementation so
 * the app's call sites stay untouched.
 *
 * One function has no modern counterpart: swr_alloc_set_opts() (the int64
 * channel-layout form) was removed in FFmpeg 6.0. It is rebuilt here from
 * av_channel_layout_from_mask() and swr_alloc_set_opts2(). Note that the 4.4
 * prototype returns the SwrContext pointer, not an error code, and that the
 * caller keeps the swr_init() responsibility.
 */

#include <stdint.h>

#include <libavutil/channel_layout.h>
#include <libavutil/samplefmt.h>
#include <libswresample/swresample.h>
#include <libswscale/swscale.h>

#define NPA_EXPORT __attribute__((visibility("default")))

NPA_EXPORT SwsContext *npa_sws_alloc_context(void)
{
    return sws_alloc_context();
}

NPA_EXPORT SwsContext *npa_sws_getContext(
    int srcW,
    int srcH,
    enum AVPixelFormat srcFormat,
    int dstW,
    int dstH,
    enum AVPixelFormat dstFormat,
    int flags,
    SwsFilter *srcFilter,
    SwsFilter *dstFilter,
    const double *param
)
{
    return sws_getContext(
        srcW, srcH, srcFormat, dstW, dstH, dstFormat, flags, srcFilter, dstFilter, param
    );
}

NPA_EXPORT SwsContext *npa_sws_getCachedContext(
    SwsContext *context,
    int srcW,
    int srcH,
    enum AVPixelFormat srcFormat,
    int dstW,
    int dstH,
    enum AVPixelFormat dstFormat,
    int flags,
    SwsFilter *srcFilter,
    SwsFilter *dstFilter,
    const double *param
)
{
    return sws_getCachedContext(
        context,
        srcW,
        srcH,
        srcFormat,
        dstW,
        dstH,
        dstFormat,
        flags,
        srcFilter,
        dstFilter,
        param
    );
}

NPA_EXPORT int npa_sws_scale(
    SwsContext *context,
    const uint8_t *const srcSlice[],
    const int srcStride[],
    int srcSliceY,
    int srcSliceH,
    uint8_t *const dst[],
    const int dstStride[]
)
{
    return sws_scale(context, srcSlice, srcStride, srcSliceY, srcSliceH, dst, dstStride);
}

NPA_EXPORT void npa_sws_freeContext(SwsContext *context)
{
    sws_freeContext(context);
}

NPA_EXPORT struct SwrContext *npa_swr_alloc(void)
{
    return swr_alloc();
}

NPA_EXPORT int npa_swr_init(struct SwrContext *context)
{
    return swr_init(context);
}

NPA_EXPORT void npa_swr_free(struct SwrContext **context)
{
    /* FFmpeg 4.4 swr_free(): both redirected sites pass &ctx, not ctx. */
    swr_free(context);
}

NPA_EXPORT int npa_swr_convert(
    struct SwrContext *context,
    uint8_t *const *out,
    int outCount,
    const uint8_t *const *in,
    int inCount
)
{
    return swr_convert(context, out, outCount, in, inCount);
}

NPA_EXPORT int npa_swr_set_matrix(
    struct SwrContext *context,
    const double *matrix,
    int stride
)
{
    return swr_set_matrix(context, matrix, stride);
}

/*
 * Rebuild one channel layout from a legacy int64 mask. FFmpeg 4.4 accepted any
 * mask here and only failed later, inside swr_init(); the modern helper rejects
 * a zero mask outright, so a zero mask must leave the layout unset and let
 * swr_init() return EINVAL exactly as it used to.
 */
static void npa_layout_from_legacy_mask(AVChannelLayout *layout, int64_t mask)
{
    *layout = (AVChannelLayout){0};
    if (mask != 0 && av_channel_layout_from_mask(layout, (uint64_t)mask) < 0) {
        *layout = (AVChannelLayout){0};
    }
}

NPA_EXPORT struct SwrContext *npa_swr_alloc_set_opts(
    struct SwrContext *context,
    int64_t outChannelLayout,
    int outSampleFormat,
    int outSampleRate,
    int64_t inChannelLayout,
    int inSampleFormat,
    int inSampleRate,
    int logOffset,
    void *logContext
)
{
    AVChannelLayout out = {0};
    AVChannelLayout in = {0};

    npa_layout_from_legacy_mask(&out, outChannelLayout);
    npa_layout_from_legacy_mask(&in, inChannelLayout);
    if (swr_alloc_set_opts2(
            &context,
            &out,
            outSampleFormat,
            outSampleRate,
            &in,
            inSampleFormat,
            inSampleRate,
            logOffset,
            logContext
        ) < 0) {
        context = NULL;
    }
    av_channel_layout_uninit(&out);
    av_channel_layout_uninit(&in);
    return context;
}
