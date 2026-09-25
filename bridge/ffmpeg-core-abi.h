/*
 * Compile-time ABI guard for the ffmpeg-core unit.
 *
 * The unit replaces the FFmpeg 4.4.5 the app links statically with 4.4.8, which
 * is only sound while the two share one ABI. That holds here, and it is checked
 * from two independent directions:
 *
 *   - the public headers of the two trees differ in doxygen comments only, and
 *     every LIBAV*_VERSION_INT and FF_API_* guard is identical (avcodec
 *     58.134.100, avformat 58.76.100, avutil 56.70.100, swresample 3.9.100),
 *     so no field of any public struct can have moved;
 *   - the offsets asserted below were read out of the app's machine code
 *     separately and agree with the values recorded here. See
 *     notes/ida-investigation.md sections 7.3, 7.5 and 8.
 *
 * The app reads and writes these structures directly, with no FFmpeg call in
 * between, so a closure rebuilt from another branch must fail the build here
 * rather than let the app interpret the wrong fields.
 */

#ifndef NPA_FFMPEG_CORE_ABI_H
#define NPA_FFMPEG_CORE_ABI_H

#include <stddef.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/frame.h>
#include <libswresample/swresample.h>

#define NPA_ABI_ASSERT(condition, message) _Static_assert(condition, message)

/* A point release must not move any of these. */
NPA_ABI_ASSERT(LIBAVCODEC_VERSION_INT == 0x3a8664, "libavcodec is not 58.134.100");
NPA_ABI_ASSERT(LIBAVFORMAT_VERSION_INT == 0x3a4c64, "libavformat is not 58.76.100");
NPA_ABI_ASSERT(LIBAVUTIL_VERSION_INT == 0x384664, "libavutil is not 56.70.100");
NPA_ABI_ASSERT(LIBSWRESAMPLE_VERSION_INT == 0x030964, "libswresample is not 3.9.100");

/* Structures the app allocates through FFmpeg and then reads by offset. */
NPA_ABI_ASSERT(sizeof(AVFormatContext) == 0x5e0, "AVFormatContext layout moved");
NPA_ABI_ASSERT(sizeof(AVStream) == 0x1f0, "AVStream layout moved");
NPA_ABI_ASSERT(sizeof(AVCodecContext) == 0x438, "AVCodecContext layout moved");
NPA_ABI_ASSERT(sizeof(AVCodecParameters) == 0x90, "AVCodecParameters layout moved");
NPA_ABI_ASSERT(sizeof(AVFrame) == 0x218, "AVFrame layout moved");
NPA_ABI_ASSERT(sizeof(AVPacket) == 0x58, "AVPacket layout moved");

/* Fields the app touches directly, confirmed against the app's machine code. */
NPA_ABI_ASSERT(offsetof(AVFormatContext, ctx_flags) == 0x028, "AVFormatContext.ctx_flags moved");
NPA_ABI_ASSERT(offsetof(AVFormatContext, pb) == 0x020, "AVFormatContext.pb moved");
NPA_ABI_ASSERT(offsetof(AVFormatContext, nb_streams) == 0x02c, "AVFormatContext.nb_streams moved");
NPA_ABI_ASSERT(offsetof(AVFormatContext, streams) == 0x030, "AVFormatContext.streams moved");
NPA_ABI_ASSERT(offsetof(AVFormatContext, error_recognition) == 0x4c4, "AVFormatContext.error_recognition moved");
NPA_ABI_ASSERT(offsetof(AVFormatContext, interrupt_callback.callback) == 0x4c8, "interrupt_callback.callback moved");
NPA_ABI_ASSERT(offsetof(AVFormatContext, interrupt_callback.opaque) == 0x4d0, "interrupt_callback.opaque moved");

NPA_ABI_ASSERT(offsetof(AVStream, index) == 0x000, "AVStream.index moved");
NPA_ABI_ASSERT(offsetof(AVStream, codec) == 0x008, "AVStream.codec moved");
NPA_ABI_ASSERT(offsetof(AVStream, codecpar) == 0x0d0, "AVStream.codecpar moved");

NPA_ABI_ASSERT(offsetof(AVCodecParameters, channel_layout) == 0x068, "AVCodecParameters.channel_layout moved");
NPA_ABI_ASSERT(offsetof(AVCodecParameters, channels) == 0x070, "AVCodecParameters.channels moved");
NPA_ABI_ASSERT(offsetof(AVCodecParameters, sample_rate) == 0x074, "AVCodecParameters.sample_rate moved");

NPA_ABI_ASSERT(offsetof(AVCodecContext, width) == 0x074, "AVCodecContext.width moved");
NPA_ABI_ASSERT(offsetof(AVCodecContext, height) == 0x078, "AVCodecContext.height moved");
NPA_ABI_ASSERT(offsetof(AVCodecContext, thread_count) == 0x310, "AVCodecContext.thread_count moved");

NPA_ABI_ASSERT(offsetof(AVFrame, data) == 0x000, "AVFrame.data moved");
NPA_ABI_ASSERT(offsetof(AVFrame, linesize) == 0x040, "AVFrame.linesize moved");
NPA_ABI_ASSERT(offsetof(AVFrame, width) == 0x068, "AVFrame.width moved");
NPA_ABI_ASSERT(offsetof(AVFrame, height) == 0x06c, "AVFrame.height moved");
NPA_ABI_ASSERT(offsetof(AVFrame, format) == 0x074, "AVFrame.format moved");

NPA_ABI_ASSERT(offsetof(AVPacket, pts) == 0x008, "AVPacket.pts moved");
NPA_ABI_ASSERT(offsetof(AVPacket, dts) == 0x010, "AVPacket.dts moved");
NPA_ABI_ASSERT(offsetof(AVPacket, duration) == 0x040, "AVPacket.duration moved");
NPA_ABI_ASSERT(offsetof(AVPacket, convergence_duration) == 0x050, "AVPacket.convergence_duration moved");

#endif /* NPA_FFMPEG_CORE_ABI_H */
