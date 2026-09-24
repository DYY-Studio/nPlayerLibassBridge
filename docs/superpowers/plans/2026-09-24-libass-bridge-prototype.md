# LibASS Bridge Minimal Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从干净 nPlayer 3.13.0 IPA 构建伪签名 `LibASSBridge.dylib`，将 16 个 libass 调用点作为单一 15-API ABI 域原子切换到 libass 0.17.5，并在 bridge 缺失或解析失败时安全使用旧静态实现。

**Architecture:** 主程序通过 `LC_LOAD_WEAK_DYLIB` 加载 bridge，并由 `__NPATCH_TEXT` 中的 Keystone payload 解析 15 个 wrapper。只有全部 `dlsym` 和 `dladdr` image identity 检查成功才发布 NEW；否则永久发布 OLD。LIEF 分两阶段重建与填充，Keystone 只消费 LIEF 已冻结的最终 VA。

**Tech Stack:** C、Clang arm64/iOS 13、Theos、Meson、Ninja、uv（`pyproject.toml` + `uv.lock`）、LIEF、Keystone（源码构建）、libass 0.17.5、ldid。

**Spec:** `../../../../notes/ida-investigation-2.md` 与本计划中已确认的冻结设计。

## Global Constraints

- 只处理 libass；FFmpeg 代码、ABI 和调用点全部不动。
- 唯一输入基线是 IPA 内主程序 SHA-256 `28e4a62ca87642338deeedbaf144bb8e4b3a801963abcdb59434aae88369b2b8`。
- 最终同时保留 `0x100A0392C`、`0x100ACBC14` 两处 NOP。
- Bridge 固定导出 15 个 Mach-O 符号 `_npa_ass_*`；`dlsym` 名称不带下划线。
- 不允许逐 callsite 或逐 API fallback；不得产生部分 NEW/部分 OLD 的 app。
- `INITIALIZING` 等待者不得因超时自行发布 OLD。
- Bridge 只能通过 `dlsym` 失败进入 OLD；dyld 装载失败是独立启动故障。
- `__NPATCH_DATA` 初始内容必须只有 `state=0` 和 15 个 NULL slot。
- 新 segment 不增加 bind/rebase relocation，不含静态 main/dylib 64-bit VA。
- 所有 branch 必须逐项断言在 arm64 `±128 MiB` 范围内。
- Bridge 与所有静态依赖统一以 `arm64-apple-ios13.0` 构建。
- Bridge 不得有 `__mod_init_func`、`__mod_term_func`、C++ static initializer 或其他隐式初始化入口。
- Fontconfig 固定 Expat backend；禁止宿主自动探测。
- 最终验证器必须区分 Mach-O symbol spelling 与 `dlsym` spelling。
- `dladdr` 同时验证 15 项 `dli_fbase` 相同，且 `dli_fname` basename 为 `LibASSBridge.dylib`。
- Python 环境用 uv 管理：`pyproject.toml` 声明依赖与 `requires-python` 区间，`uv.lock` 为唯一锁。只声明支持的版本区间，不实现版本探测、候选回落或环境来源校验：解释器不在区间内时 uv 直接失败。
- Keystone 从源码构建。PyPI 仅 0.9.2 且无 macOS arm64 wheel，故不引入 PyPI 包，也不使用其 Python 绑定。
- 不安装或链接 Homebrew 的 macOS 产物。
- 若执行 Git commit，必须先获得用户明确授权，并包含 `Co-authored-by: Codex <codex@openai.com>` trailer。

---

## File Structure

```text
nPlayerLibassBridge/
├── .gitignore
├── Makefile
├── pyproject.toml
├── uv.lock
├── bridge/
│   ├── npa_ass_bridge.c
│   └── bridge.exports
├── deps/
│   ├── sources.lock.json
│   ├── ios-arm64.cross
│   └── build_deps.py
├── manifests/
│   └── nplayer-3.13.0.json
├── npabridge/
│   ├── __init__.py
│   ├── manifest.py
│   ├── toolchain.py
│   ├── target_abi.py
│   ├── payload.py
│   ├── build_bridge.py
│   ├── macho.py
│   ├── verify.py
│   └── package.py
├── tools/
│   ├── doctor.py
│   ├── keystone.py
│   ├── target_abi_probe.c
│   ├── phase_a.py
│   ├── phase_b.py
│   ├── verify.py
│   └── package.py
├── smoke/
│   └── BridgeSmoke/
└── tests/
    ├── test_manifest.py
    ├── test_toolchain.py
    ├── test_dependencies.py
    ├── test_payload.py
    ├── test_bridge.py
    ├── test_macho.py
    ├── test_verify.py
    └── test_package.py
```

---

## 参考实现与取舍

`../nPlayerLibassBridgeOld/` 是本计划的前一轮实现，已跑到 bridge/payload 阶段。它是**参考**，不是必须全盘移植的基线：其中已完成一轮去过度工程化，但仍有遗留。以下取舍对全计划生效。

可直接借鉴（已实现且与本计划约束一致）：

- `npabridge/manifest.py` 与 `manifests/nplayer-3.13.0.json`：15 API / 16 callsite 数据、`encode_bl`。
- `npabridge/toolchain.py`：Keystone 的 ctypes 封装，仅 `ks_version` / `ks_open` / `ks_asm` / `ks_close` / `ks_free`。
- `npabridge/target_abi.py`：从 iOS SDK 编译探测对象读取 `Dl_info` 偏移与 `RTLD_DEFAULT`，不硬编码。
- `tools/build_keystone.py`：Keystone 源码构建与 `CMP0051` 兼容补丁。
- `npabridge/payload.py`：状态机、resolve block、veneer、`_check_branch` / `_check_adrp`、`_validate_encoded_branches`、`_check_external_branches`。
- `npabridge/build_bridge.py`：编译、导出、链接与隐式初始化检查。
- `deps/ios-arm64.cross` 与各依赖的 meson 选项集合。

不得沿用（旧实现仍存在的过度工程）：

- 依赖侧的 include/manifest/digest 校验子系统：`write_include_manifest`、`validate_include_manifest`、`_json_digest`、`_validate_record_paths`、`validate_system_dependency_records`、`_validate_project_dependency_records`、`verify_dependency_resolution`、`verify_meson_configuration`。
- `build_bridge.py::DependencyContract` 中的 `include_manifest` / `closure` / `system_link_args` 字段及其校验路径。
- `payload.py::_validate_source_invariants` 中对生成文本的匹配（`source.count("stlr w16, [x_state]")` 等），改为对最终字节的断言。
- gperf 及其构建链，以及 `_safe_member_name` / `_extract_archive` 的手写逐成员校验；归档解包统一用标准库 `tarfile.extractall(..., filter="data")`。
- `tools/doctor.py` 的全量 `TOOLS` 清单与 Keystone 路径探测数组。
- `tests/test_dependencies.py` 中围绕 include manifest / record / meson 配置的用例。

---

### Task 1: Freeze Baseline, Correct Report, and Create Manifest

**Files:**
- Create: `nPlayerLibassBridge/pyproject.toml`
- Create: `nPlayerLibassBridge/uv.lock`
- Create: `nPlayerLibassBridge/manifests/nplayer-3.13.0.json`
- Create: `nPlayerLibassBridge/npabridge/manifest.py`
- Create: `nPlayerLibassBridge/tests/test_manifest.py`
- Modify: `../../../../notes/ida-investigation-2.md`

**Interfaces:**
- Produces: `load_manifest(path: Path) -> Manifest`
- Produces: immutable API/callsite/old-target data used by every later task.

- [ ] **Step 1: Declare the environment before any test runs**

Task 1 needs a test runner, so the uv environment is declared here; Task 2 Step 2 only re-runs the sync.

`pyproject.toml`:

```toml
[project]
name = "nplayer-libass-bridge"
version = "0.1.0"
requires-python = ">=3.11,<3.15"
dependencies = ["lief==1.0.0"]

[dependency-groups]
dev = ["meson", "ninja", "pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

```bash
uv lock
uv sync --frozen --all-groups
```

`uv.lock` is the only lock file. The declared version range is the only Python policy: uv selects or fails; no candidate probing, venv provenance checks, wheel RECORD reconstruction, console-script verification or source-mode fallback.

- [ ] **Step 2: Correct the two confirmed report errors**

Change the `nPlayer.bak` description to “only `0xA0392C` is patched” and replace dyld values with the values below. Also record the newly confirmed omitted boundary call `ass_free_track` at `0x100A03B40 -> 0x100C0A2F4`, and change the app boundary summary to 15 unique APIs / 16 direct call sites.

```text
rebase_off      0x1834000  size 0xC318
bind_off        0x1840318  size 0x6F30
weak_bind_off   0x1847248  size 0x8618
lazy_bind_off   0x184F860  size 0x6CA8
export_off      0x1856508  size 0x5BBB8
```

- [ ] **Step 3: Write manifest validation tests**

Tests cover only machine-checkable facts in the manifest (counts, addresses, symbol spelling).

```python
def test_domain_has_fifteen_unique_apis(self):
    self.assertEqual(len(self.manifest.apis), 15)
    self.assertEqual(len({api.symbol for api in self.manifest.apis}), 15)

def test_domain_has_sixteen_callsites(self):
    sites = [site for api in self.manifest.apis for site in api.call_sites]
    self.assertEqual(len(sites), 16)

def test_process_data_has_two_callsites(self):
    api = self.manifest.api("npa_ass_process_data")
    self.assertEqual(api.call_sites, (0x100A0482C, 0x100A0529C))

def test_free_track_has_expected_callsite(self):
    api = self.manifest.api("npa_ass_free_track")
    self.assertEqual(api.call_sites, (0x100A03B40,))
    self.assertEqual(api.old_target, 0x100C0A2F4)

def test_symbol_spellings_are_distinct(self):
    api = self.manifest.api("npa_ass_library_init")
    self.assertEqual(api.dlsym_name, "npa_ass_library_init")
    self.assertEqual(api.macho_name, "_npa_ass_library_init")
```

- [ ] **Step 4: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_manifest.py -v
```

Expected: import or manifest-file failure.

- [ ] **Step 5: Create the manifest**

Include all 15 mappings:

```text
npa_ass_library_init          100A03F50 -> 100C296AC
npa_ass_set_extract_fonts     100A03FA0 -> 100C29A9C
npa_ass_set_message_cb        100A03FB4 -> 100C29D48
npa_ass_renderer_init         100A03FBC -> 100C15D24
npa_ass_set_frame_size        100A03FC8 -> 100C2FEF8
npa_ass_set_fonts_dir         100A04680 -> 100C297EC
npa_ass_new_track             100A047E8 -> 100C0BD68
npa_ass_process_codec_private 100A04800 -> 100C0B314
npa_ass_process_data          100A0482C,100A0529C -> 100C0B060
npa_ass_free_track            100A03B40 -> 100C0A2F4
npa_ass_flush_events          100A062F8 -> 100C0B9F0
npa_ass_render_frame          100A06408 -> 100C169F0
npa_ass_renderer_done         100A035EC -> 100C15F88
npa_ass_library_done          100A035F4 -> 100C2978C
npa_ass_set_fonts             100A0394C -> 100C30174
```

Also record:

```json
{
  "imagebase": "0x100000000",
  "main_sha256": "28e4a62ca87642338deeedbaf144bb8e4b3a801963abcdb59434aae88369b2b8",
  "dlsym_stub": "0x1011362CC",
  "dladdr_stub": "0x10113629C",
  "bridge_path": "@executable_path/Frameworks/LibASSBridge.dylib",
  "expected_bridge_basename": "LibASSBridge.dylib",
  "callback": {
    "app_callback": "0x100A033C4",
    "prototype": "void(int, const char *, va_list, void *)",
    "va_list_size": 8,
    "ignored_argument_register": "x3"
  }
}
```

- [ ] **Step 6: Implement manifest dataclasses and BL calculation**

```python
@dataclass(frozen=True)
class APIBinding:
    symbol: str
    call_sites: tuple[int, ...]
    old_target: int

    @property
    def dlsym_name(self) -> str:
        return self.symbol

    @property
    def macho_name(self) -> str:
        return "_" + self.symbol
```

- [ ] **Step 7: Run manifest and baseline tests**

Expected: all tests pass; IPA member hash and every computed original BL word match.

- [ ] **Step 8: Commit checkpoint if authorized**

Suggested commit:

```text
fix: freeze libass bridge binary baseline
```

---

### Task 2: Build the Patch Toolchain and Probe iOS ABI

**Files:**
- Create: `nPlayerLibassBridge/npabridge/toolchain.py`
- Create: `nPlayerLibassBridge/npabridge/target_abi.py`
- Create: `nPlayerLibassBridge/tools/doctor.py`
- Create: `nPlayerLibassBridge/tools/keystone.py`
- Create: `nPlayerLibassBridge/tools/target_abi_probe.c`
- Create: `nPlayerLibassBridge/tests/test_toolchain.py`

**Interfaces:**
- Produces: `Toolchain.assemble(source: str, address: int) -> bytes`
- Produces: `load_target_abi(sdk: Path) -> TargetABI`

- [ ] **Step 1: Write failing tests**

```python
def test_keystone_assembles_arm64_branch(self):
    code = self.toolchain.assemble("b #0x101000000", 0x100FFF000)
    self.assertIsInstance(code, bytes)
    self.assertEqual(code[:4], b"\x00\x04\x00\x14")

def test_target_abi_comes_from_ios_sdk(self):
    abi = load_target_abi(self.ios_sdk)
    self.assertEqual(abi.dl_info_size, 32)
    self.assertEqual(abi.dl_info_fname_offset, 0)
    self.assertEqual(abi.dl_info_fbase_offset, 8)
    self.assertEqual(abi.rtld_default_masked, self.expected_rtld_default)
```

- [ ] **Step 2: Sync the environment declared in Task 1**

```bash
uv sync --frozen --all-groups
```

`uv.lock` is the only lock file; the declared `requires-python` range is the only Python policy. No candidate probing, venv provenance checks, wheel RECORD reconstruction, console-script verification or source-mode fallback.

- [ ] **Step 3: Build Keystone 0.9.2 from commit `dc7932ef2b2c4a793836caec6ecab485005139d6`**

Build a host `libkeystone.dylib`; load it through a ctypes wrapper implementing only `ks_version`, `ks_open`, `ks_asm`, `ks_close`, `ks_free`. Extract the pinned source archive with the standard library (`tarfile.extractall(destination, filter="data")`); the `cmake_policy(SET CMP0051 OLD → NEW)` patch is still required.

- [ ] **Step 4: Compile the target ABI probe**

```c
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

const uintptr_t npa_rtld_default_bits = (uintptr_t)RTLD_DEFAULT;
const uint32_t npa_dl_info_size = sizeof(Dl_info);
const uint32_t npa_dl_info_fname_offset = offsetof(Dl_info, dli_fname);
const uint32_t npa_dl_info_fbase_offset = offsetof(Dl_info, dli_fbase);
const uint32_t npa_dl_info_sname_offset = offsetof(Dl_info, dli_sname);
const uint32_t npa_dl_info_saddr_offset = offsetof(Dl_info, dli_saddr);
```

Compile with:

```bash
xcrun --sdk iphoneos clang \
  -target arm64-apple-ios13.0 \
  -c tools/target_abi_probe.c \
  -o build/target_abi_probe.o
```

Read constants from the Mach-O object; do not hardcode `RTLD_DEFAULT`.

- [ ] **Step 5: Add the environment doctor**

`tools/doctor.py` reports availability of `clang`, `xcrun`, `git`, `cmake`, `ninja` and the iOS SDK path, and exits non-zero on a missing required tool. No package-version or provenance checks.

- [ ] **Step 6: Run toolchain tests**

Expected: Keystone output disassembles correctly; probe object identifies iOS platform/minos and constants.

- [ ] **Step 7: Commit checkpoint if authorized**

Suggested commit:

```text
build: add reproducible patch toolchain
```

---

### Task 3: Build the Complete iOS Dependency Closure

**Files:**
- Create: `nPlayerLibassBridge/deps/sources.lock.json`
- Create: `nPlayerLibassBridge/deps/ios-arm64.cross`
- Create: `nPlayerLibassBridge/deps/build_deps.py`
- Create: `nPlayerLibassBridge/tests/test_dependencies.py`

**Interfaces:**
- Produces static archives under `build/deps/lib/` and `build/deps/include/`.

- [ ] **Step 1: Pin release tarballs**

Use official release archives (not git snapshots) with the published SHA-256 recorded in the lock file. Do not invent hashes: take them from the official release announcement or release directory.

```text
libass      0.17.5    release tarball
FreeType    2.14.3    release tarball
FriBidi     1.0.16    release tarball
HarfBuzz    14.2.1    release tarball
Fontconfig  2.17.1    release tarball
Expat       2.8.5     release tarball
```

Every lock entry must include URL, version, archive SHA-256, build options and output archive. Release tarballs contain the generated sources, so no `gperf` is required; if some dependency cannot be built from its release tarball, document the reason in the lock entry instead of silently reintroducing a host code generator.

- [ ] **Step 2: Create a strict iOS 13 cross file**

All projects use:

```text
target: arm64-apple-ios13.0
system: darwin
cpu_family: aarch64
cpu: arm64
endian: little
needs_exe_wrapper: true
```

- [ ] **Step 3: Configure FreeType explicitly**

```text
brotli=disabled
bzip2=disabled
harfbuzz=disabled
mmap=enabled
png=disabled
tests=disabled
zlib=system
error_strings=false
```

- [ ] **Step 4: Configure Fontconfig explicitly**

```text
doc=disabled
doc-txt=disabled
doc-man=disabled
doc-pdf=disabled
doc-html=disabled
nls=disabled
tests=disabled
tools=disabled
cache-build=disabled
iconv=enabled
xml-backend=expat
fontations=disabled
default-hinting=slight
default-sub-pixel-rendering=none
bitmap-conf=no-except-emoji
default-fonts-dirs=/System/Library/Fonts,/Library/Fonts
additional-fonts-dirs=
cache-dir=/var/cache/fontconfig
template-dir=/etc/fontconfig/conf.avail
baseconfig-dir=/etc/fonts
config-dir=/etc/fonts/conf.d
xml-dir=/etc/fonts/xml
```

The app creates its runtime `font.conf` and sets `FONTCONFIG_FILE/PATH` in `sub_100A03CB0`; the bridge must not overwrite those variables.

- [ ] **Step 5: Configure libass explicitly**

```text
fontconfig=enabled
coretext=disabled
directwrite=disabled
libunibreak=disabled
test=disabled
compare=disabled
profile=disabled
fuzz=disabled
checkasm=disabled
large-tiles=false
require-system-font-provider=true
```

Resolve HarfBuzz and FriBidi to the pinned static archives.

- [ ] **Step 6: Build dependencies in order**

```text
Expat
→ FreeType
→ HarfBuzz
→ FriBidi
→ Fontconfig
→ libass
```

- [ ] **Step 7: Verify closure and path hygiene**

Fail if any output contains:

```text
/usr/local/
/opt/homebrew/
/Users/
```

Also fail on an archive/object whose deployment target differs from iOS 13.

- [ ] **Step 8: Run dependency tests**

Expected: all archives exist, have locked hashes, and expose only headers/static libraries.

- [ ] **Step 9: Commit checkpoint if authorized**

Suggested commit:

```text
build: add pinned iOS libass dependency closure
```

---

### Task 4: Build the 15-Export LibASSBridge

**Files:**
- Create: `nPlayerLibassBridge/bridge/npa_ass_bridge.c`
- Create: `nPlayerLibassBridge/bridge/bridge.exports`
- Create: `nPlayerLibassBridge/npabridge/build_bridge.py`
- Create: `nPlayerLibassBridge/tests/test_bridge.py`

**Interfaces:**
- Produces `build/LibASSBridge.dylib`.
- Exports exactly the 15 symbols in `bridge.exports`.

- [ ] **Step 1: Write export and ABI tests**

```python
def test_exports_use_macho_underscore(self):
    exports = set(nm_exports(self.bridge))
    self.assertEqual(
        exports,
        {f"_npa_ass_{name}" for name in EXPECTED_BRIDGE_SYMBOLS},
    )

def test_dlsym_manifest_has_no_underscore(self):
    for name in EXPECTED_BRIDGE_SYMBOLS:
        self.assertFalse(name.startswith("_"))
```

- [ ] **Step 2: Write the 15 wrapper prototypes**

```c
ASS_Library *npa_ass_library_init(void);
void npa_ass_library_done(ASS_Library *);
void npa_ass_set_fonts_dir(ASS_Library *, const char *);
void npa_ass_set_extract_fonts(ASS_Library *, int);
void npa_ass_set_message_cb(
    ASS_Library *,
    void (*)(int, const char *, va_list, void *),
    void *
);

ASS_Renderer *npa_ass_renderer_init(ASS_Library *);
void npa_ass_renderer_done(ASS_Renderer *);
void npa_ass_set_frame_size(ASS_Renderer *, int, int);
void npa_ass_set_fonts(
    ASS_Renderer *,
    const char *,
    const char *,
    int,
    const char *,
    int
);
ASS_Image *npa_ass_render_frame(
    ASS_Renderer *,
    ASS_Track *,
    long long,
    int *
);

ASS_Track *npa_ass_new_track(ASS_Library *);
void npa_ass_process_codec_private(ASS_Track *, const char *, int);
void npa_ass_process_data(ASS_Track *, const char *, int);
void npa_ass_free_track(ASS_Track *);
void npa_ass_flush_events(ASS_Track *);
```

- [ ] **Step 3: Implement direct pass-through wrappers**

All wrappers call the corresponding libass function with the same argument and return types.

`npa_ass_set_fonts` must explicitly call:

```c
ass_set_fonts(
    renderer,
    default_font,
    default_family,
    ASS_FONTPROVIDER_FONTCONFIG,
    config,
    update
);
```

`npa_ass_set_message_cb` must pass the four-argument callback and `va_list` callback pointer through unchanged. It must not call `va_start`, `va_end`, or `va_copy`.

`npa_ass_free_track` must call `ass_free_track(track)` directly so a track created by the bridge is never released by the old static libass implementation.

- [ ] **Step 4: Enforce linker exports**

`bridge.exports`:

```text
_npa_ass_library_init
_npa_ass_library_done
_npa_ass_set_fonts_dir
_npa_ass_set_extract_fonts
_npa_ass_set_message_cb
_npa_ass_renderer_init
_npa_ass_renderer_done
_npa_ass_set_frame_size
_npa_ass_set_fonts
_npa_ass_render_frame
_npa_ass_new_track
_npa_ass_process_codec_private
_npa_ass_process_data
_npa_ass_free_track
_npa_ass_flush_events
```

Compile with hidden visibility and:

```text
-Wl,-exported_symbols_list,bridge/bridge.exports
```

- [ ] **Step 5: Link static dependencies only**

Reject the build if `otool -L` names libass, FreeType, Fontconfig, Expat, HarfBuzz, FriBidi, Homebrew or non-system host paths.

- [ ] **Step 6: Verify no implicit initialization**

Require all of:

```text
__mod_init_func absent or zero-sized
__mod_term_func absent or zero-sized
no _GLOBAL__sub_I_* symbols
no unexpected __cxa_atexit import
no initializer constructor in bridge source
```

- [ ] **Step 7: Run bridge static tests**

Expected: exact 15 exports, iOS 13 build version, no host paths, no init entries.

- [ ] **Step 8: Commit checkpoint if authorized**

Suggested commit:

```text
feat: add fifteen-export libass bridge
```

---

### Task 5: Generate the Atomic Keystone Payload

**Files:**
- Create: `nPlayerLibassBridge/npabridge/payload.py`
- Create: `nPlayerLibassBridge/tests/test_payload.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class PayloadLayout:
    text_vmaddr: int
    data_vmaddr: int
    state_rva: int
    slots_rva: int

@dataclass(frozen=True)
class Payload:
    text: bytes
    data: bytes
    symbols: dict[str, int]
    stubs: dict[str, int]

def measure_payload(layout, manifest, target_abi) -> int: ...
def assemble_payload(layout, manifest, target_abi) -> Payload: ...
```

- [ ] **Step 1: Generate the four-state state machine**

```text
0 UNINITIALIZED
1 INITIALIZING
2 NEW
3 OLD
```

Use LL/SC for `UNINITIALIZED → INITIALIZING`; `LDAR` for readers; `STLR` for final publication. Waiters spin without a total timeout.

- [ ] **Step 2: Generate the bootstrap ABI save/restore**

Save/restore:

```text
X0–X7
X30/LR
SP with 16-byte alignment
```

Avoid X19–X28 entirely; if used, save/restore them. Preserve X16/X17 as caller-saved scratch.

- [ ] **Step 3: Generate 15 resolve blocks**

Each block:

```text
dlsym(RTLD_DEFAULT, "npa_ass_*")
→ require non-NULL
→ dladdr(address, &info)
→ require same dli_fbase
→ require basename == LibASSBridge.dylib
→ store final slot
```

Any failure branches to one shared `publish_old` block. No partially resolved pointer is visible because state remains INITIALIZING.

- [ ] **Step 4: Generate 16 callsite veneers**

Two callsites use the same `process_data` veneer. Each veneer:

```text
UNINITIALIZED → bootstrap
INITIALIZING → wait
NEW → load slot and BR
OLD → B old target
```

- [ ] **Step 5: Enforce data and text invariants on the assembled bytes**

`data` must contain only the initialized dispatch state and null slots:

```text
offset 0x00: u32 state = 0
offset 0x04: 4 bytes alignment padding = 0
offset 0x08: 15 × u64 NULL
```

The logical data size is `4 + 4 + 15×8 = 128` bytes; any segment reservation remains separately aligned.

Reject assembled text containing 64-bit MOVZ/MOVK pointer literals, absolute pointer literals or data relocation directives. Check the produced bytes and their decoded instructions; do not match against the generated assembly text.

- [ ] **Step 6: Check every branch range**

Assert:

```text
callsite → veneer             BL
veneer → old target           B
free_track veneer → 0x100C0A2F4 B
bootstrap → _dlsym stub      BL
bootstrap → _dladdr stub     BL
```

- [ ] **Step 7: Run payload tests**

Expected: deterministic output, constant encoded size across provisional/final VAs, all state/branch/symbol rules pass.

- [ ] **Step 8: Commit checkpoint if authorized**

Suggested commit:

```text
feat: generate atomic libass dispatch payload
```

---

### Task 6: Implement LIEF Phase A Layout Freeze

**Files:**
- Create: `nPlayerLibassBridge/npabridge/macho.py`
- Create: `nPlayerLibassBridge/tools/phase_a.py`
- Create: `nPlayerLibassBridge/tests/test_macho.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SemanticSnapshot:
    segment_vas: dict[str, int]
    section_vas: dict[str, int]
    entrypoint: int
    dylib_ordinals: tuple[tuple[str, int], ...]
    bind_targets: tuple[tuple[str, str], ...]
    lazy_targets: tuple[tuple[str, str], ...]
    export_symbols: tuple[str, ...]

def phase_a(input_path: Path, output_path: Path, manifest, reserved_text: int) -> None: ...
def snapshot(binary: lief.MachO.Binary) -> SemanticSnapshot: ...
```

- [ ] **Step 1: Write semantic-preservation tests**

Compare pristine IPA member and Phase A output:

```python
self.assertEqual(before.segment_vas["__TEXT"], after.segment_vas["__TEXT"])
self.assertEqual(before.entrypoint, after.entrypoint)
self.assertEqual(before.dylib_ordinals[:-1], after.dylib_ordinals[:-1])
self.assertEqual(before.bind_targets, after.bind_targets)
self.assertEqual(before.lazy_targets, after.lazy_targets)
self.assertEqual(before.export_symbols, after.export_symbols)
```

- [ ] **Step 2: Parse and preflight the clean main**

Fail on SHA, load-command, `cryptid`, dlsym/dladdr stub, opcode, VA, branch target, or NOP mismatch.

- [ ] **Step 3: Remove the old signature and add weak dependency**

Use LIEF’s `DylibCommand.weak_dylib()` with:

```text
@executable_path/Frameworks/LibASSBridge.dylib
```

- [ ] **Step 4: Add measured placeholder segments**

Add:

```text
__NPATCH_TEXT r-x
__NPATCH_DATA rw-
```

Use the exact measured payload capacity. Keep `__LINKEDIT` as the final segment command.

- [ ] **Step 5: Write and reparse the temporary Mach-O**

Phase A ends by writing and reparsing. Phase B must never invoke LIEF’s structural builder again.

- [ ] **Step 6: Verify Phase A**

Expected: final VA layout available, no callsite changes, no new bind/rebase entries, original semantic invariants preserved.

- [ ] **Step 7: Commit checkpoint if authorized**

Suggested commit:

```text
feat: add two-phase Mach-O layout freeze
```

---

### Task 7: Implement Equal-Length Phase B Patching

**Files:**
- Modify: `nPlayerLibassBridge/npabridge/macho.py`
- Create: `nPlayerLibassBridge/tools/phase_b.py`
- Modify: `nPlayerLibassBridge/tests/test_macho.py`

**Interfaces:**

```python
def phase_b(layout_path: Path, output_path: Path, manifest) -> None: ...
def write_equal_length(section, offset: int, data: bytes) -> None: ...
def patch_bl(site: int, target: int) -> bytes: ...
```

- [ ] **Step 1: Write Phase B tests**

```python
def test_phase_b_does_not_change_segment_vas(self):
    self.assertEqual(before.segment_vas, after.segment_vas)

def test_phase_b_patches_exactly_sixteen_callsites(self):
    self.assertEqual(count_changed_instructions(before, after), 16)

def test_phase_b_preserves_original_sections(self):
    self.assertEqual(original_section_bytes(before), original_section_bytes(after))
```

- [ ] **Step 2: Reparse Phase A output and read final VAs**

Pass the parsed `__NPATCH_TEXT/__NPATCH_DATA` addresses to `assemble_payload`.

- [ ] **Step 3: Require equal payload size**

If final assembled text differs in size from the Phase A reservation, fail without writing output.

- [ ] **Step 4: Fill placeholders with equal-length writes**

Only modify the placeholder segment contents. Do not call any LIEF operation that shifts raw data or rebuilds metadata.

- [ ] **Step 5: Patch 16 BL instructions by VA**

Use section-relative lookup, not stale file offsets. Decode each original BL and assert its target equals the manifest old target before replacement.

- [ ] **Step 6: Reapply the two NOP patches by VA**

```text
0x100A0392C: 48010035 -> 1F2003D5
0x100ACBC14: 80000037 -> 1F2003D5
```

- [ ] **Step 7: Run Phase B tests**

Expected: exact 16 redirects, two NOPs, no other original instruction changes.

- [ ] **Step 8: Commit checkpoint if authorized**

Suggested commit:

```text
feat: redirect complete libass ABI domain
```

---

### Task 8: Build the Semantic and Artifact Verifier

**Files:**
- Create: `nPlayerLibassBridge/npabridge/verify.py`
- Create: `nPlayerLibassBridge/tools/verify.py`
- Create: `nPlayerLibassBridge/tests/test_verify.py`

**Interfaces:**

```python
def verify_bridge(path: Path, manifest) -> VerificationReport: ...
def verify_main(before: Path, after: Path, manifest) -> VerificationReport: ...
def verify_payload(binary: lief.MachO.Binary, manifest) -> VerificationReport: ...
```

- [ ] **Step 1: Write mutation-detection tests**

On temporary copies, introduce exactly three deliberate defects:

```text
one extra export
one out-of-range branch
one non-zero payload state / one non-NULL slot
```

Each mutation must make verification fail with a specific error code. The remaining properties (absolute pointer literal, host path, non-empty mod_init section) are covered by the positive checks in Steps 2–4 and need no separate malformed sample.

- [ ] **Step 2: Verify bridge closure**

Require:

```text
arm64 iOS, minos 13.0
exact 15 underscore-prefixed exports
no dynamic third-party dependency
no host prefix
no initializer/terminator sections
no unexpected static-init symbols
```

- [ ] **Step 3: Verify callback contract**

Confirm bridge source and manifest retain the four-argument callback and do not call `va_start` on the incoming `va_list`.

- [ ] **Step 4: Verify payload and data**

Require zero state, 15 NULL slots, no data relocation, no static VA literal, correct symbol strings and all branch ranges.

- [ ] **Step 5: Verify main semantic diff**

Allow only:

```text
new weak dylib command
new two segments
signature command/data
necessary __LINKEDIT offsets
16 four-byte BL changes
two four-byte NOP changes
placeholder payload/data
```

Original segment/section VAs, entrypoint, existing dylib ordinals, bind targets, lazy targets and export semantics must remain unchanged.

- [ ] **Step 6: Emit machine-readable report**

```json
{
  "artifact": "bridge.ipa",
  "mode": "bridge",
  "main_sha256": "...",
  "bridge_sha256": "...",
  "state_initial": 0,
  "checks": []
}
```

- [ ] **Step 7: Run verifier tests**

Expected: all valid artifacts pass; every deliberate mutation fails.

- [ ] **Step 8: Commit checkpoint if authorized**

Suggested commit:

```text
test: enforce bridge and Mach-O invariants
```

---

### Task 9: Package Signed IPA Variants

**Files:**
- Create: `nPlayerLibassBridge/npabridge/package.py`
- Create: `nPlayerLibassBridge/tools/package.py`
- Create: `nPlayerLibassBridge/tests/test_package.py`
- Modify: `nPlayerLibassBridge/Makefile`

**Interfaces:**

```python
def package_ipa(app: Path, output: Path, bridge: Path | None) -> None: ...
```

- [ ] **Step 1: Write package-content tests**

Verify each IPA contains exactly one `Payload/nPlayer.app/nPlayer` and, where requested, one `Frameworks/LibASSBridge.dylib`.

- [ ] **Step 2: Implement artifact variants**

```text
baseline.ipa          clean IPA + two NOPs
weak-load-only.ipa    weak command, no bridge, zero redirected calls
fallback.ipa          full dispatch, no bridge
bridge.ipa            full dispatch + valid bridge
```

No `bridge-load-only` variant: `bridge.ipa` already proves that the dylib loads, matches architecture/minos/dependencies and passes signature checks.

- [ ] **Step 3: Sign with ldid**

Run `ldid -S` separately on the main executable and bridge before repacking.

- [ ] **Step 4: Publish atomically**

Write to `dist/.tmp-*`; run the full verifier; rename to final filename only after success. On any failure, remove incomplete output.

- [ ] **Step 5: Add Make targets**

```text
make bootstrap      uv sync --frozen --all-groups && uv run python tools/keystone.py
make deps           uv run python deps/build_deps.py
make bridge         uv run python npabridge/build_bridge.py
make weak-load-only uv run python tools/package.py weak-load-only
make fallback       uv run python tools/package.py fallback
make bridge-ipa     uv run python tools/package.py bridge
make smoke          xcrun --sdk iphoneos clang ... smoke/BridgeSmoke
make verify         uv run python tools/verify.py
make clean          rm -rf build dist
```

- [ ] **Step 6: Run package tests**

Expected: all four variants have correct contents and valid artifacts pass.

- [ ] **Step 7: Commit checkpoint if authorized**

Suggested commit:

```text
feat: package reproducible libass bridge IPAs
```

---

### Task 10: Build the Standalone Bridge Smoke App

**Files:**
- Create: `nPlayerLibassBridge/smoke/BridgeSmoke/BridgeSmokeApp.m`

- [ ] **Step 1: Build the standalone iOS smoke app**

It must:

```text
dlopen bridge
resolve 15 names without underscores
verify same dli_fbase and LibASSBridge.dylib basename
create/destroy library
register four-argument message callback
set Fontconfig fonts
create/destroy renderer
create/process/flush track
render at least one ASS_Image
verify public image fields
exercise app-compatible teardown ordering, including renderer/library release before npa_ass_free_track
```

Only the app-compatible teardown order is exercised. The smoke app contains no production bridge behavior.

- [ ] **Step 2: Build and check the target**

Expected: builds arm64 / iOS 13; `otool -l` shows the expected platform and minos.

- [ ] **Step 3: Commit checkpoint if authorized**

Suggested commit:

```text
test: add libass bridge device smoke app
```

---

### Task 11: End-to-End Static Verification and Device Acceptance

**Files:**
- Create at execution time: `nPlayerLibassBridge/dist/verification.json`
- Modify: `nPlayerLibassBridge/Makefile`

- [ ] **Step 1: Run the full automated suite**

```bash
make clean
make bootstrap
make deps
make bridge
make verify
uv run pytest
```

Expected: zero failures.

- [ ] **Step 2: Build all artifacts from the clean IPA**

Reject use of `nPlayer`, `nPlayer.bak` or the already-patched `Payload/nPlayer.app` as patch input. Build `baseline`, `weak-load-only`, `fallback` and `bridge`.

- [ ] **Step 3: Test weak load**

Install `weak-load-only.ipa` without the bridge. Expected: app starts and behaves like the baseline.

- [ ] **Step 4: Test fallback dispatch**

Install `fallback.ipa`. Expected:

```text
state OLD
all 15 old calls
both existing NOP behaviors remain
normal teardown
```

- [ ] **Step 5: Test bridge dispatch and subtitle matrix**

Install `bridge.ipa`. Expected:

```text
state NEW
15 slots
one dli_fbase
dli_fname basename LibASSBridge.dylib
```

While it runs, exercise:

```text
ordinary SRT→ASS
embedded ASS
Matroska embedded fonts
external font directory
seek and flush
repeated open/close cycles
```

- [ ] **Step 6: Publish the verification report**

Record artifact hashes, checks, device model/iOS, state result, callback result, font cases and teardown result in `dist/verification.json`.

- [ ] **Step 7: Commit checkpoint if authorized**

Suggested commit:

```text
test: complete libass bridge prototype verification
```

---

## 执行记录（Task 1–7 已落地）

执行过程中确认的偏差，后续任务以此为准：

- Task 1 Step 2（修正报告）在上一轮已完成，`notes/ida-investigation-2.md` 无需改动。
- 环境：`pyproject.toml` + `uv.lock` 已经建立，由 Task 1 Step 1 落地；`requires-python = ">=3.11,<3.15"`，uv 选了 3.12。
- 依赖：全部改用官方 release tarball；gperf 已删除（FriBidi release 自带生成源，Fontconfig 使用系统 `/usr/bin/gperf`）。FriBidi 的 `gen.tab/meson.build` 需要 build-machine 编译器，因此新增 `deps/macos-arm64.native`，并把 host 编译参数移进 `deps/ios-arm64.cross` 的 `[built-in options]`，避免 `SDKROOT`/`CFLAGS` 污染 native 编译器。
- Payload：删除对生成文本的匹配，改为字节级断言（无 64 位 MOVZ/MOVK、无 LDR literal、恰好两次 STLR）；移除未使用的 `Payload.source`、`generate_source` 与 stub 别名。
- LIEF：
  - 必须持有 parse container，否则访问 header/section 会崩溃；`macho.parse()` 返回的 `ParsedMachO` 封装了这一点。
  - 新增 segment 的 VA/file offset 由 LIEF 决定（紧接 `__DATA` 之后），`__LINKEDIT` 被移到新 segment 之后。其余 segment 与全部 section 的 VA 不变；dylib 序号、bind/lazy、export trie、symtab、function starts 与既有 section 内容逐项相同。验证器因此把「只有 `__LINKEDIT` 可以移动」作为唯一例外。
  - LIEF 会把 segment 的 `file_size` 向上取整到 16 KiB。reservation 仍是 payload 的精确大小；Phase A 证明大小与 VA 无关，Phase B 只要求 payload 不越界且不溢出到 data segment。
  - `phase_a` 返回 Phase A 报告，并在省略 `reserved_text` 时自行测量。
- 产物布局：`build/input/nPlayer`（clean main）、`build/macho/main-phase-a|b`、`build/LibASSBridge.dylib`、`build/deps/*`。

---

## Self-Review

- All frozen architecture requirements map to explicit tasks.
- No FFmpeg work appears.
- No partial-domain app artifact is permitted.
- Symbol spelling, `dladdr` filename, Fontconfig paths, implicit initialization, two-phase LIEF, branch ranges, callback ABI and bounded waiting all have verification steps.
- Host tooling uses uv with `pyproject.toml` + `uv.lock`; no custom environment, provenance or package-management layer exists.
- Dependencies come from official release tarballs; no `gperf` is required.
- No test asserts on generated source text; payload checks run on assembled bytes and decoded instructions.
- Artifact variants, smoke targets and acceptance steps are limited to what each one uniquely proves.
- Function and type names are consistent across tasks.
- All implementation steps are explicit and independently reviewable.
