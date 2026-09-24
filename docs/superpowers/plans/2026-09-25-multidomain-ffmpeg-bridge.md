# Multi-Dylib Bridge + FFmpeg swscale/swresample Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已验证的「单一 libass 桥」泛化为「多个可独立回落、可按产物选择的桥接单元」，并在 `LibFFmpegBridge.dylib` 中落地第一个 FFmpeg 单元（`libswscale` + `libswresample`，共 19 个调用点），先在干净 IPA 上验证该单元。

**Architecture:** 载荷与 Mach-O 改写机制不变，仍是一段 `__NPATCH_TEXT`/`__NPATCH_DATA` + 四态状态机 + `dlsym`/`dladdr` 身份校验 + 原子发布 OLD/NEW。变化只有两点：（1）载荷从「一个 15-API 单元」改为「N 个单元，每单元一个 state 与槽表，各自独立解析、独立发布、互不影响」；（2）主程序从「弱加载一个 dylib」改为「弱加载每个出现过的 dylib」。单元（unit）是原子切换与回落的最小粒度，一个 dylib 可承载多个单元，因此同一份现代 libav\* 可以在一个 dylib 内分阶段接入（先 swscale/swresample，之后是解码、解复用），各单元仍能独立回落。FFmpeg 侧只用 `--disable-everything --enable-swscale --enable-swresample` 的极小闭包（libavutil + libswscale + libswresample），bridge 导出 10 个 legacy 名字的薄 shim。

**Tech Stack:** Python ≥3.11 + uv（运行期依赖只有 `lief==1.0.0`）、`ldid`、系统 `zip`/`unzip`、LIEF 1.0、Keystone（payload 汇编）；构建期 clang / iOS SDK / meson / ninja + FFmpeg 9.0.2 自带的 `configure`。

**Spec:** 设计已与该用户逐条确认（工程落点＝扩展为多单元、dylib 粒度＝按库族、libass 单元本次一起重构、目标＝FFmpeg 9.0.2（当前稳定版）、验证顺序＝先干净 IPA）。本计划即规格，Global Constraints 复录全部约束。

## Global Constraints

- 包管理与运行统一用 uv（`pyproject.toml` + `uv.lock`），`requires-python = ">=3.11,<3.15"`，运行期依赖只有 `lief==1.0.0`；dev group 保留 `meson`/`ninja`/`pytest`。
- **patch 阶段不得依赖 Xcode**：不得调用 `xcrun`/`otool`/iOS SDK。`ldid` 保留。
- 严禁静默回落；不得把「没找到 dylib」当成「该单元 OLD」。单元回落只允许由该单元自身的 `dlsym`/`dladdr` 校验失败触发；产物缺 dylib 是构建期错误，必须直接失败。
- **一个调用点只能属于一个单元**；同一符号在同一单元内可有多个调用点（共用 veneer）。
- 载荷数据区只允许「每单元 state(u32)+对齐 + 该单元槽表」，不得出现静态 64-bit VA 字面量、绑定/重定位项。
- 不改动已冻结的四态状态机语义（UNINITIALIZED/INITIALIZING/NEW/OLD）、等待者不得因超时自行发布、`finish` 必须恢复 X0–X7/X30/SP。
- 不新增兼容层：不做 v1 manifest 兼容、不保留旧 CLI 别名、不保留旧 `LibASSBridge` 专用字段。
- 密钥与隐私：不发布 IPA、不提供解密；新增依赖必须在 `THIRD-PARTY.md` 记录。
- 测试只钉住「用户可见的失败行为」或既有安全网；禁止文本匹配/正则探测式测试，禁止 restating schema 的测试。
- 每个 Task 一个 Commit，消息尾部附 `Co-authored-by: Codex <codex@openai.com>`。
- 一切设备验收结果写入 `dev/acceptance.json`。

## 冻结输入（不可重新推导）

主程序 SHA-256 `28e4a62ca87642338deeedbaf144bb8e4b3a801963abcdb59434aae88369b2b8`；`imagebase 0x100000000`；`dlsym_stub 0x1011362CC`；`dladdr_stub 0x10113629C`；既有两处 NOP `0x100A0392C`/`0x100ACBC14`。

libass 单元 15 API / 16 调用点：见现有 `manifests/nplayer-3.13.0.json`，原样迁移。

FFmpeg 单元 10 API / 19 调用点：

| 单元 | symbol | call sites | old_target |
|---|---|---|---|
| swscale | `npa_sws_getCachedContext` | `0x100A8A450` | `0x1008DE290` |
| swscale | `npa_sws_getContext` | `0x100A23428`, `0x100A46A90`, `0x100A8A4A4` | `0x1008DDD8C` |
| swscale | `npa_sws_scale` | `0x100A234AC`, `0x100A46AB8`, `0x100A8A470`, `0x100A8A4C4` | `0x1008C6540` |
| swscale | `npa_sws_freeContext` | `0x100A2354C`, `0x100A31DF4`, `0x100A46B78`, `0x100A8A4D0` | `0x1008DE0CC` |
| swresample | `npa_swr_alloc` | `0x100ABF9DC` | `0x10112C980` |
| swresample | `npa_swr_free` | `0x100ABFA38`, `0x100ABFB34` | `0x10112DBE0` |
| swresample | `npa_swr_alloc_set_opts` | `0x100ABFB58` | `0x1008744BC` |
| swresample | `npa_swr_set_matrix` | `0x100ABFC34` | `0x10086E348` |
| swresample | `npa_swr_init` | `0x100ABFC48` | `0x10112DC28` |
| swresample | `npa_swr_convert` | `0x100ABFC78` | `0x100874AA4` |

（旧目标地址由 notes 记录；实施前用 `preflight` 的 BL 断言逐条复核，不靠记忆。）

**冻结表勘误（2026-09-25）**：`0x10112DBE0` 是 FFmpeg 4.4 的 `swr_free`，不是 `swr_close`——两个调用点传的是
上下文字段地址（`SwrContext**`），函数体以 `av_freep` 释放该指针。表中该行已改为 `npa_swr_free`；真正的
`swr_close` 是 `0x10112DC24`（仅 Opus 路径调用，不在本表内）。其余九行经逐行反编译复核无误。
证据与判定表见 `notes/ida-investigation.md`。

FFmpeg 源：`https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz`，`archive_sha256 = 8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`（本地下载实测得到，非引用）。

现代 ABI 事实（9.0 头文件实查）：`sws_getContext`/`sws_getCachedContext`/`sws_scale`/`sws_freeContext`、`swr_alloc`/`swr_init`/`swr_close`/`swr_convert`/`swr_set_matrix` 签名与 n4.4 一致；**`swr_alloc_set_opts`（int64 layout 版）自 6.0 起已删除**，该 shim 必须自行实现 `av_channel_layout_from_mask` + `swr_alloc_set_opts2`。

---

## 减法审查结论（已并入本计划）

写入计划后做了一轮减法，以下内容被删除或收敛，理由随附：

1. **不引入 `dylibs[].domains[]` 之外的第三层结构**，也不引入「按 dylib 分组 dlopen」——载荷本来就不做 `dlopen`（dylib 由 `LC_LOAD_WEAK_DYLIB` 交给 dyld），每个单元只需自己的 basename 做 `dladdr` 校验。
2. **`PayloadLayout` 的 `state_rva`/`slots_rva` 删除**：多单元后它们恒为派生值，保留字段只会制造「布局可配置」的假象。
3. **不新增 FFmpeg 专用 smoke 应用**：dylib 装载 + 符号解析 + 身份校验与 libass 单元完全同机制，设备验收用打过补丁的 IPA 完成（截图/缩略图、播放缩放、音频重采样）。
4. **不新增独立依赖校验子系统**：FFmpeg 归档复用 `deps/build_deps.py::_validate_archive`（arm64 / iOS 13 / 非 thin / 成员清单），只新增一个 `ffmpeg.lock.json` 与该构建脚本。
5. **不新增 schema 版本号与迁移工具**：manifest 直接改成新形状，旧形状不存在于任何已发布产物中。
6. **不做 `--bridge` 兼容别名、不做 dylib 自动探测**：`--dylib <id>` 显式选择，默认全部；缺失即失败。
7. **不把 NOP 站点做成全局常量**：它们属于 libass 单元，随单元选择一起生效/失效，避免「只装 ffmpeg 单元却带上 libass 行为改动」。
8. **不加「支持任意数量 dylib/单元」的开关或配置**：循环天然支持 N，不需要为 N=2 写任何特判。
9. **不新增 restating 类型的测试**：只更新已有测试到新形状，并保留每个具名 check 的 mutation 检测覆盖。

---

## File Structure

| 路径 | 职责 | 动作 |
|---|---|---|
| `npabridge/manifest.py` | `dylibs[].domains[].apis[]` 数据模型；`Unit`（dylib×domain）扁平视图；`encode_bl` | 修改 |
| `npabridge/payload.py` | 每单元 state/slots/veneer/解析/发布；数据尺寸与旧目标/`dlsym`/`dladdr` 计数按单元合计 | 修改 |
| `npabridge/macho.py` | 每个出现过的 dylib 一条弱加载；`extra_sites` 来自单元；两阶段改写与 preflight 按所选单元 | 修改 |
| `npabridge/verify.py` | 每单元载荷 check + 每 dylib bridge 契约 check；成品校验按所选单元 | 修改 |
| `npabridge/package.py` | 组装所选 dylib 到 `Frameworks/`；`inspect_ipa` 按所选 dylib | 修改 |
| `npabridge/patch.py` | `--dylib` 选择；输出名按所选 dylib 的 `library_version` 拼接 | 修改 |
| `npabridge/build_bridge.py` | 按 manifest 逐个 dylib 构建/校验（源、导出表、闭包、include/lib 根均来自 manifest） | 修改 |
| `bridge/npa_ffmpeg_util_bridge.c` | 10 个 legacy 名字 shim | 新建 |
| `bridge/ffmpeg-util.exports` | 10 个导出符号 | 新建 |
| `deps/ffmpeg.lock.json` | FFmpeg 9.0.2 源与 `configure` 参数、3 个输出归档 | 新建 |
| `deps/build_ffmpeg.py` | 交叉构建 FFmpeg 闭包 + 归档校验 + 写 closure | 新建 |
| `manifests/nplayer-3.13.0.json` | 迁移为 `dylibs[]`；新增 ffmpeg dylib 与其两个单元 | 修改 |
| `tests/*` | 更新到新形状；保留 mutation 检测 | 修改 |
| `Makefile` | `deps` 同时构建两个闭包；`bridge` 构建全部 dylib | 修改 |
| `README.md`、`THIRD-PARTY.md` | 输出名、`--dylib`、FFmpeg 许可与版本 | 修改 |
| `dev/acceptance.json` | 追加本轮的干净 IPA 验收 | 修改 |
| `dev/smoke/`、`dev/abi_probe.py`、`dev/tools/phase_*.py` | 不改（phase 入口自动继承多单元） | 不动 |

---

### Task 1: Manifest 泛化为 dylibs/domains/apis

**Files:** `npabridge/manifest.py`, `manifests/nplayer-3.13.0.json`, `tests/test_manifest.py`, `dev/tools/phase_a.py`（仅签名对齐）

- [ ] **Step 1: 定义新数据模型**

```python
@dataclass(frozen=True)
class Unit:
    dylib_id: str
    domain_id: str
    basename: str            # 该单元所属 dylib 的 basename，用于 dladdr 身份校验
    apis: tuple[APIBinding, ...]
    @property
    def id(self) -> str: ...   # f"{dylib_id}/{domain_id}"
    @property
    def symbol_count(self) -> int: ...
    @property
    def call_site_count(self) -> int: ...

@dataclass(frozen=True)
class ExtraSite:
    site: int
    expected: int
    replacement: int

@dataclass(frozen=True)
class Dylib:
    id: str
    library_version: str
    basename: str
    path: str
    domains: tuple[Domain, ...]
    extra_sites: tuple[ExtraSite, ...] = ()
    callback: Callback | None = None
    build: BuildSpec | None = None   # 仅 dev 构建用：source/exports/closure/include_root/lib_root

Manifest.units() -> tuple[Unit, ...]        # 按 dylibs→domains 顺序摊平
Manifest.dylib(id) -> Dylib
Manifest.extra_sites(dylib_ids) -> tuple[ExtraSite, ...]
```

- [ ] **Step 2: 迁移 manifest（行为等价）**：libass dylib（`library_version 0.17.5`、`LibASSBridge.dylib`、1 个 domain `libass`（15 API/16 站点）、`callback`、`extra_sites` = 两处 NOP（`expected` 取冻结原字 `0x35000148`/`0x37000080`、`replacement` `0xD503201F`）、`build` = 现有源/导出表/`build/deps/libass-closure.txt`/`build/deps/include`/`build/deps/lib`）。移除顶层 `bridge_path`/`expected_bridge_basename`/`apis`。
- [ ] **Step 3: 更新 `tests/test_manifest.py`**：保留原有「15 唯一 API / 16 站点 / `process_data` 双站点 / 符号拼写」断言，改为经 `manifest.units()` 取；新增「单元 id 全局唯一」「同一站点不跨单元重复」。
- [ ] **Step 4: 跑测试**：`uv run pytest tests/test_manifest.py`；随后 `uv run pytest`（预期 payload/verify/package 测试失败，属后续 Task）。
- [ ] **Step 5: Commit** `refactor: model bridges as dylibs, domains and call sites`

---

### Task 2: Payload 生成 N 个独立单元

**Files:** `npabridge/payload.py`, `npabridge/macho.py`（`PayloadLayout` 调用点）, `npabridge/verify.py`（构造点）, `tests/test_payload.py`

- [ ] **Step 1: 收敛布局**：`PayloadLayout` 只保留 `text_vmaddr`/`data_vmaddr`；删除 `state_rva`/`slots_rva`。每单元数据块 = `u32 state + u32 pad`，其后接 `len(apis) * 8` 个槽；`state` 地址即块首，槽从 `+8` 起。
- [ ] **Step 2: 泛化生成器**：`_veneer_blocks`/`_resolve_blocks`/`_terminal_blocks` 接受 `units`；`enter_<symbol>` 在帧内额外保存**本单元 state 地址**（`adrp/add` 到数据段，不用 64-bit 字面量）；`bootstrap` 用该地址做 LL/SC `UNINITIALIZED→INITIALIZING`；`publish_new`/`publish_old` 写回同一地址；每个 api 的 `dladdr` basename 校验用其单元的 `basename`。
- [ ] **Step 3: 泛化不变式**：符号与站点在全域唯一；`old_target` 分支数 = 总 api 数；`dlsym`/`dladdr` 调用数 = 总 api 数；发布指令数 = `2 × unit 数`；数据尺寸 = `Σ(8 + 8×api 数)`；`measure_payload`/`assemble_payload` 签名不变。
- [ ] **Step 4: 更新 `tests/test_payload.py`**：保留分支范围、无 64-bit 字面量、无 literal pool、`adrp` 范围、站点→veneer 可编码等既有断言，改为对 `units` 驱动；删除对固定 `128`/`15`/`16` 的硬编码断言，改由 manifest 推导。
- [ ] **Step 5: 跑测试**：`uv run pytest tests/test_payload.py tests/test_macho.py`
- [ ] **Step 6: Commit** `refactor: generate one atomic dispatch unit per domain`

---

### Task 3: Mach-O 两阶段改写与成品校验按单元集合

**Files:** `npabridge/macho.py`, `npabridge/verify.py`, `npabridge/package.py`, `npabridge/patch.py`, `tests/test_macho.py`, `tests/test_verify.py`, `tests/test_package.py`, `tests/test_patch.py`

- [ ] **Step 1: `macho.py`**
  - `preflight`：对所选单元的每个调用点断言 BL 等于 `encode_bl(site, old_target)`（既有逻辑，改为遍历所选单元）；对所选 dylib 断言未预先加载；`extra_sites` 断言原字等于 `expected`；`STUB_THUNKS` 保持全局校验。
  - `phase_a`：删除 `NOP_SITES` 常量；为所选单元涉及的每个 basename 追加 `DylibCommand.weak_lib(path)`；payload 预留大小 = `measure_payload(provisional, manifest, abi, units)`。
  - `phase_b`：载荷填充后，按所选单元写调用点 BL，再按所选 dylib 写 `extra_sites`；`_assert_intended_writes` 的声明范围同步。
- [ ] **Step 2: `verify.py`**
  - `verify_bridge(path, dylib)`：导出符号 == 该 dylib 全部单元的 `macho_name` 集合；install name == `@rpath/<basename>`；依赖只允许 `/usr/lib/*` 且不含 `libass`/`libavutil`/`libswscale`/`libswresample` 等静态泄漏；无隐式初始化；无 host 路径；`callback` 仅在 dylib 声明时校验（读该 dylib 的 `build.source`）。
  - `verify_main(baseline, patched, manifest, abi, units)`：`_changed_sites` 期望集 = 所选单元调用点 ∪ 所选 dylib 的 `extra_sites`；`_dylib_ordinals` 允许尾部新增 `len(所选 dylib)` 条且顺序等于 manifest 顺序；载荷 check 按单元逐条（每单元 state=0、槽表全 NULL、文本前缀一致）。
  - `verify_artifact(...)`：先 `verify_main`，再对每个所选 dylib 跑 `verify_bridge`。
- [ ] **Step 3: `package.py`**：`package_ipa(source, output, main, bridges: Mapping[str, Path])`，把每个 dylib 复制/签名到 `Frameworks/<basename>`；`inspect_ipa` 断言所选 dylib 各恰好一份；`extract_for_verification` 返回 `{"main": ..., <basename>: ...}`。
- [ ] **Step 4: `patch.py`**：`patch_ipa(..., dylibs: Sequence[str] | None = None)`；默认 = manifest 全部 dylib；显式选择时只要求/只安装这些 dylib。CLI：`--dylib <id>`（可重复）。输出名 = `<stem>-<dylib_id><library_version>` 按 manifest 顺序用 `-` 连接。`select_manifest` 不变。
- [ ] **Step 5: 测试**：`tests/test_macho.py` 保留「阶段 B 只改声明范围」「段不可移动」「BL 目标断言」；`tests/test_verify.py` 保留三类故意 mutation（多一个导出 / 越界分支 / 非零 state）必须失败；`tests/test_package.py` 改为多 dylib 内容断言；`tests/test_patch.py` 覆盖「只选 ffmpeg dylib」时输出名与 patch 集合。
- [ ] **Step 6: 跑测试**：`uv run pytest`
- [ ] **Step 7: Commit** `refactor: patch and verify a chosen set of bridge dylibs`

---

### Task 4: 重构后重验 libass 单一产物（无回归）

**Files:** `dev/acceptance.json`

- [ ] **Step 1:** `make bootstrap deps bridge verify test` 全绿；确认 `build/LibASSBridge.dylib` 的导出/依赖/初始化 check 全部通过。
- [ ] **Step 2:** `npa-patch --dylib libass <你的 IPA>`，`verify_artifact` 全过；记录产物 sha256。
- [ ] **Step 3:** 设备验收（iPhone SE 3 / iOS 17.7.2 / LiveContainer）：SRT、内嵌 ASS、Matroska 内嵌字体、字体缓存、连续播放各一遍。
- [ ] **Step 4:** 把设备信息与结论追加到 `dev/acceptance.json`。
- [ ] **Step 5: Commit** `test: re-verify the libass artifact after the multi-unit refactor`

---

### Task 5: FFmpeg 9.0.2 swscale/swresample 闭包

**Files:** `deps/ffmpeg.lock.json`, `deps/build_ffmpeg.py`, `Makefile`, `THIRD-PARTY.md`

- [ ] **Step 1: 写 `deps/ffmpeg.lock.json`**：源（URL/SHA-256/archive 名）、`output_archives = [build/deps/ffmpeg/lib/libavutil.a, .../libswscale.a, .../libswresample.a]`、`include_root = build/deps/ffmpeg/include`、`closure = build/deps/ffmpeg-closure.txt`、`configure_args`（含 `{prefix}`/`{cc}`/`{sdk}`/`{cflags}`/`{ldflags}` 占位）。
- [ ] **Step 2: 写 `deps/build_ffmpeg.py`**：`import build_deps` 复用 `isolated_environment`/`sdk_path`/`_xcrun`/`sha256_file`/`fetch_source`/`extract_source`/`_validate_archive`；解压 → `./configure` → `make -j` → `make install` → 校验 3 个归档（arm64 / iOS 13 / 非 thin / 成员清单 / 无 host 路径）→ 写 closure 文件。
- [ ] **Step 3: `configure` 关键约束**（写进 lock 的 `configure_args`）：

```text
--prefix={prefix} --target-os=darwin --arch=arm64 --enable-cross-compile
--cc={cc} --as={cc} --sysroot={sdk}
--extra-cflags="-target arm64-apple-ios13.0 -miphoneos-version-min=13.0 -g0
                -ffile-prefix-map={root}=. -fdebug-prefix-map={root}=."
--extra-ldflags="-target arm64-apple-ios13.0"
--disable-autodetect --disable-network --disable-doc --disable-programs
--disable-everything --enable-swscale --enable-swresample
--disable-avdevice --disable-avfilter --disable-avformat --disable-avcodec --disable-postproc
--disable-shared --enable-static
```

  `--disable-autodetect` + `--disable-everything` 必须让闭包只含 libavutil/libswscale/libswresample 三个归档；若出现第 4 个归档即为配置泄漏，直接失败。
- [ ] **Step 4: `Makefile`**：`deps` 目标依次跑 `deps/build_deps.py` 与 `deps/build_ffmpeg.py`。
- [ ] **Step 5: `THIRD-PARTY.md`**：新增 FFmpeg 9.0.2（LGPL-2.1+，本次构建未启用 GPL 组件）与闭包说明。
- [ ] **Step 6: 验证**：`make deps` 后 `python -c "import json;print(json.load(open('build/deps/verification.json')))"`（libass 闭包不受影响）+ 检查 `build/deps/ffmpeg/lib` 恰好 3 个归档。
- [ ] **Step 7: Commit** `build: pin and cross-compile the FFmpeg 9.0.2 util closure`

---

### Task 6: LibFFmpegBridge dylib 与其两个单元

**Files:** `bridge/npa_ffmpeg_util_bridge.c`, `bridge/ffmpeg-util.exports`, `manifests/nplayer-3.13.0.json`, `npabridge/build_bridge.py`, `npabridge/verify.py`, `tests/test_bridge.py`

- [ ] **Step 1: 写 shim 源**（`-fvisibility=hidden`，只导出 `npa_*`）：

```c
ASSERT npa_sws_getContext / npa_sws_getCachedContext / npa_sws_scale / npa_sws_freeContext
       —— 直接转发到同名现代函数（签名一致）。

npa_swr_alloc / npa_swr_init / npa_swr_close / npa_swr_convert / npa_swr_set_matrix
       —— 直接转发。

npa_swr_alloc_set_opts(SwrContext *s, int64_t ocl, int osf, int osr,
                       int64_t icl, int isf, int isr, int log_offset, void *log_ctx)
       —— 在现代库中已不存在：用 av_channel_layout_from_mask 构造两侧
          AVChannelLayout，再调 swr_alloc_set_opts2；s 非 NULL 时沿用该上下文。
          （本 shim 不调用 swr_init；调用方 AudioResampler 本就会自行 swr_init。）
```

- [ ] **Step 2: 导出表** `bridge/ffmpeg-util.exports` 列出上述 10 个 `_npa_*` 符号。
- [ ] **Step 3: manifest 新增 ffmpeg dylib**：`library_version 9.0.2`、basename `LibFFmpegBridge.dylib`、path `@executable_path/Frameworks/LibFFmpegBridge.dylib`、domain `libswscale`（4 API/12 站点）与 `libswresample`（6 API/7 站点），站点/旧目标按上文冻结表；`build` 指向新源、导出表、`build/deps/ffmpeg-closure.txt`、`build/deps/ffmpeg/include`、`build/deps/ffmpeg/lib`。
- [ ] **Step 4: `build_bridge.py` 泛化**：`build_bridge(dylib_id)` 从 manifest 的 `build` 段取源/导出表/闭包/根目录；`main()` 构建 manifest 中全部 dylib（或 `--dylib` 指定），逐个 `verify_bridge`。
- [ ] **Step 5: `tests/test_bridge.py`**：断言 `LibFFmpegBridge.dylib` 恰好导出 10 个 `_npa_*`、install name 为 `@rpath/LibFFmpegBridge.dylib`、动态依赖只有 `/usr/lib/*`、无 `__mod_init_func`、无 host 路径；断言 manifest 中每个 dylib 的 `build` 段都有对应导出表且符号集合匹配。
- [ ] **Step 6: 构建验证**：`make bridge verify` 产出两个 dylib 且 check 全过。
- [ ] **Step 7: Commit** `feat: add the ten-symbol LibFFmpegBridge dylib`

---

### Task 7: 干净 IPA 上验证 FFmpeg 单元

**Files:** `dev/acceptance.json`

- [ ] **Step 1:** `npa-patch --dylib ffmpeg <干净 IPA>`；确认输出名为 `nPlayer_3.13.0-ffmpeg9.0.2.ipa`，`verify_artifact` 全过（改动指令集 = ffmpeg 单元 19 个调用点，无 NOP，无 libass 相关改动）。
- [ ] **Step 2:** 装到设备，验证：`nPlayerView snapshotImage`（截图/缩略图）正常出图；播放缩放（解码帧→CVPixelBuffer）画面正常；音频播放（重采样→AudioUnit）音高/时长正常、无爆音；连续播放与多次打开关闭不崩。
- [ ] **Step 3:** 域隔离专项：另取一次构建，只带 `LibFFmpegBridge.dylib` 但改名为不匹配（或在无该 dylib 的设置里）验证该单元发布 OLD、行为与 baseline 一致——若该步无法在设备上稳定构造，改用 `dev/tools/phase_b.py` 生成 fallback 变体做静态验证，并在 `acceptance.json` 注明手段。
- [ ] **Step 4:** 记录设备/系统/安装方式与结论到 `dev/acceptance.json`。
- [ ] **Step 5: Commit** `test: accept the ffmpeg util unit on a clean input`

---

### Task 8: 组合产物与对外文档

**Files:** `README.md`, `dev/acceptance.json`

- [ ] **Step 1:** `npa-patch`（默认全部 dylib）产出 `nPlayer_3.13.0-libass0.17.5-ffmpeg9.0.2.ipa`，静态校验全过；设备上快速回归字幕 + 截图 + 音频三条路径。
- [ ] **Step 2:** 设备结论追加 `dev/acceptance.json`。
- [ ] **Step 3:** `README.md` 更新：输出名规则、`--dylib` 选择、`LibFFmpegBridge.dylib` 与 `libkeystone.dylib` 同为 release asset、验证状态段落。
- [ ] **Step 4: Commit** `docs: document the multi-dylib bridge and the ffmpeg util unit`

---

## Self-Review

- 每个 Task 都是可独立提交、可独立验证的节点；Task 4 是重构后的既有能力回归闸门，排在新增 FFmpeg 单元之前。
- 无兼容层、无 schema 版本、无新 smoke 应用、无新依赖校验子系统。
- 「一个调用点只属于一个单元」由 Task 1 的测试钉住；「单元回落只由自身 dlsym/dladdr 失败触发」由 Task 7 Step 3 的设备/静态度量钉住。
- FFmpeg 的 19 个站点全部来自只读逆向，逐个带 `old_target`，`preflight` 会在改写前逐条断言。
- 现代 ABI 的唯一缺口（`swr_alloc_set_opts`）在 Task 6 有明确实现，且不改变调用方的 `swr_init` 责任。
- 无 `%hook`/Substrate 依赖；patch 阶段不依赖 Xcode；设备验收结果全部落 `dev/acceptance.json`。
- 未决项：`LibFFmpegBridge.dylib` 的 ABI 兼容性只验证到「签名一致」，像素级/样本级输出差异属预期，不做逐像素比对。
