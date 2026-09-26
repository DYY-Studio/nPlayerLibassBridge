/*
 * Forwarding stubs for the ffmpeg-core unit.
 *
 * The unit carries libavformat, libavcodec, libavutil and libswresample 4.4.8
 * and replaces the 4.4.5 the app links statically. Each entry point the app
 * calls is forwarded by a tail branch to the same symbol inside this dylib, so
 * the call reaches 4.4.8 with no argument translation and no hand-written
 * prototype that could disagree with the headers. Phase B replaces these
 * branches with real shims, one symbol at a time.
 *
 * The include below is the reason this translation unit exists even though it
 * defines no C function: it fails the build if the closure's ABI drifts away
 * from the 4.4.5 the app was built against.
 *
 * Generated from the closed call-site table; see
 * docs/superpowers/plans/2026-09-25-ffmpeg-core-fullswap.md.
 */

#include "ffmpeg-core-abi.h"

#define NPA_FORWARD(symbol) \
    __asm__(".globl _npa_" #symbol "\n_npa_" #symbol ":\n\tb _" #symbol "\n")

NPA_FORWARD(avformat_alloc_context);
NPA_FORWARD(avformat_open_input);
NPA_FORWARD(avformat_find_stream_info);
NPA_FORWARD(av_read_frame);
NPA_FORWARD(av_seek_frame);
NPA_FORWARD(av_find_input_format);
NPA_FORWARD(avformat_alloc_output_context2);
NPA_FORWARD(avformat_init_output);
NPA_FORWARD(avformat_write_header);
NPA_FORWARD(av_write_frame);
NPA_FORWARD(av_write_trailer);
NPA_FORWARD(avformat_new_stream);
NPA_FORWARD(avformat_free_context);
NPA_FORWARD(av_guess_format);
NPA_FORWARD(av_probe_input_buffer);
NPA_FORWARD(av_codec_get_tag);
NPA_FORWARD(avio_alloc_context);
NPA_FORWARD(avio_open2);
NPA_FORWARD(avio_read);
NPA_FORWARD(avio_write);
NPA_FORWARD(avio_flush);
NPA_FORWARD(avio_seek);
NPA_FORWARD(avio_size);
NPA_FORWARD(avio_close);
NPA_FORWARD(avio_closep);
NPA_FORWARD(avio_open_dyn_buf);
NPA_FORWARD(avio_close_dyn_buf);
NPA_FORWARD(avio_wl32);
NPA_FORWARD(avio_wb32);
NPA_FORWARD(av_find_default_stream_index);
NPA_FORWARD(av_index_search_timestamp);
NPA_FORWARD(avformat_close_input);
NPA_FORWARD(avformat_network_init);
NPA_FORWARD(avcodec_find_decoder);
NPA_FORWARD(avcodec_find_encoder);
NPA_FORWARD(avcodec_alloc_context3);
NPA_FORWARD(avcodec_free_context);
NPA_FORWARD(avcodec_open2);
NPA_FORWARD(avcodec_close);
NPA_FORWARD(avcodec_send_packet);
NPA_FORWARD(avcodec_receive_frame);
NPA_FORWARD(avcodec_flush_buffers);
NPA_FORWARD(avcodec_send_frame);
NPA_FORWARD(avcodec_receive_packet);
NPA_FORWARD(avcodec_fill_audio_frame);
NPA_FORWARD(avcodec_decode_subtitle2);
NPA_FORWARD(avcodec_get_name);
NPA_FORWARD(avcodec_descriptor_get);
NPA_FORWARD(avcodec_parameters_alloc);
NPA_FORWARD(avcodec_parameters_free);
NPA_FORWARD(avcodec_parameters_copy);
NPA_FORWARD(avcodec_parameters_from_context);
NPA_FORWARD(avcodec_parameters_to_context);
NPA_FORWARD(av_bsf_init);
NPA_FORWARD(av_bsf_flush);
NPA_FORWARD(av_bsf_free);
NPA_FORWARD(av_bsf_send_packet);
NPA_FORWARD(av_bsf_receive_packet);
NPA_FORWARD(av_bsf_alloc);
NPA_FORWARD(av_bsf_get_by_name);
NPA_FORWARD(av_init_packet);
NPA_FORWARD(av_packet_alloc);
NPA_FORWARD(av_packet_unref);
NPA_FORWARD(av_packet_free);
NPA_FORWARD(av_new_packet);
NPA_FORWARD(av_packet_copy_props);
NPA_FORWARD(av_packet_ref);
NPA_FORWARD(av_packet_move_ref);
NPA_FORWARD(av_packet_rescale_ts);
NPA_FORWARD(avsubtitle_free);
NPA_FORWARD(av_malloc);
NPA_FORWARD(av_mallocz);
NPA_FORWARD(av_realloc);
NPA_FORWARD(av_freep);
NPA_FORWARD(av_dict_get);
NPA_FORWARD(av_dict_set);
NPA_FORWARD(av_dict_free);
NPA_FORWARD(av_dict_copy);
NPA_FORWARD(av_frame_alloc);
NPA_FORWARD(av_frame_free);
NPA_FORWARD(av_frame_unref);
NPA_FORWARD(av_frame_ref);
NPA_FORWARD(av_image_alloc);
NPA_FORWARD(av_image_fill_arrays);
NPA_FORWARD(av_image_get_buffer_size);
NPA_FORWARD(av_image_copy);
NPA_FORWARD(av_reduce);
NPA_FORWARD(av_rescale_q);
NPA_FORWARD(av_log2);
NPA_FORWARD(av_get_bytes_per_sample);
NPA_FORWARD(av_sample_fmt_is_planar);
NPA_FORWARD(av_samples_get_buffer_size);
NPA_FORWARD(av_get_default_channel_layout);
NPA_FORWARD(av_get_channel_layout_channel_index);
NPA_FORWARD(av_opt_set);
NPA_FORWARD(av_gettime);
NPA_FORWARD(av_log_set_level);
NPA_FORWARD(ff_isom_write_hvcc);
NPA_FORWARD(avpriv_mpegaudio_decode_header);
