#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

const uintptr_t npa_rtld_default_bits = (uintptr_t)RTLD_DEFAULT;
const uint32_t npa_dl_info_size = sizeof(Dl_info);
const uint32_t npa_dl_info_fname_offset = offsetof(Dl_info, dli_fname);
const uint32_t npa_dl_info_fbase_offset = offsetof(Dl_info, dli_fbase);
const uint32_t npa_dl_info_sname_offset = offsetof(Dl_info, dli_sname);
const uint32_t npa_dl_info_saddr_offset = offsetof(Dl_info, dli_saddr);
