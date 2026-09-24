#include <stdarg.h>
#include "ass/ass.h"

#define NPA_EXPORT __attribute__((visibility("default")))

NPA_EXPORT ASS_Library *npa_ass_library_init(void)
{
    return ass_library_init();
}

NPA_EXPORT void npa_ass_library_done(ASS_Library *library)
{
    ass_library_done(library);
}

NPA_EXPORT void npa_ass_set_fonts_dir(ASS_Library *library, const char *fonts_dir)
{
    ass_set_fonts_dir(library, fonts_dir);
}

NPA_EXPORT void npa_ass_set_extract_fonts(ASS_Library *library, int extract)
{
    ass_set_extract_fonts(library, extract);
}

NPA_EXPORT void npa_ass_set_message_cb(
    ASS_Library *library,
    void (*message_cb)(int, const char *, va_list, void *),
    void *data
)
{
    ass_set_message_cb(library, message_cb, data);
}

NPA_EXPORT ASS_Renderer *npa_ass_renderer_init(ASS_Library *library)
{
    return ass_renderer_init(library);
}

NPA_EXPORT void npa_ass_renderer_done(ASS_Renderer *renderer)
{
    ass_renderer_done(renderer);
}

NPA_EXPORT void npa_ass_set_frame_size(ASS_Renderer *renderer, int width, int height)
{
    ass_set_frame_size(renderer, width, height);
}

NPA_EXPORT void npa_ass_set_fonts(
    ASS_Renderer *renderer,
    const char *default_font,
    const char *default_family,
    int provider,
    const char *config,
    int update
)
{
    (void)provider;
    ass_set_fonts(
        renderer,
        default_font,
        default_family,
        ASS_FONTPROVIDER_FONTCONFIG,
        config,
        update
    );
}

NPA_EXPORT ASS_Image *npa_ass_render_frame(
    ASS_Renderer *renderer,
    ASS_Track *track,
    long long now,
    int *detect_change
)
{
    return ass_render_frame(renderer, track, now, detect_change);
}

NPA_EXPORT ASS_Track *npa_ass_new_track(ASS_Library *library)
{
    return ass_new_track(library);
}

NPA_EXPORT void npa_ass_process_codec_private(ASS_Track *track, const char *data, int size)
{
    ass_process_codec_private(track, data, size);
}

NPA_EXPORT void npa_ass_process_data(ASS_Track *track, const char *data, int size)
{
    ass_process_data(track, data, size);
}

NPA_EXPORT void npa_ass_free_track(ASS_Track *track)
{
    ass_free_track(track);
}

NPA_EXPORT void npa_ass_flush_events(ASS_Track *track)
{
    ass_flush_events(track);
}
