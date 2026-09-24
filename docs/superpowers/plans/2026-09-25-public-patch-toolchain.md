# Public Patch Toolchain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已验证的 libass 0.17.5 切换工具链收窄成面向公众的单产物 patch 工具：一条命令把用户自己的 nPlayer 3.13.0 IPA 换成带 `LibASSBridge.dylib` 的 IPA，且 patch 阶段不依赖 Xcode。

**Architecture:** 输入是用户 IPA + Release 预编译 dylib，输出是单个打过补丁的 IPA。`npabridge/patch.py` 按顺序串起：解包取 main → 拒绝加密输入 → 按 `main_sha256` 匹配 `manifests/*.json` → `preflight` 结构校验 → 两处 NOP → Phase A 布局冻结 → Phase B 等长改写 + 载荷 → 组装并 `ldid -S` → 成品具名 check 全过才原子发布。`manifests/*.json` 是版本锚点，同时冻结 iOS ABI 常量（取代 patch 期的 `xcrun` 探针）；dylib 元数据读改走 LIEF（取代 `otool`）。研发期材料（smoke 应用、ABI 探针、phase 级调试入口、设备验收记录、研发计划、dev-only 测试）移入 `dev/`。

**Tech Stack:** Python ≥3.11 + uv（运行期依赖只有 `lief==1.0.0`）、`ldid`、系统 `zip`/`unzip`、LIEF 1.0 读写 Mach-O；构建期另有 clang/iOS SDK/meson/cmake（仅 `dev/` 源码构建路径）。

**Spec:** 设计已与该用户逐条确认（对话中的 8 个决策与 3 个收尾判断），本计划即规格；下面 Global Constraints 复录全部约束。

## Global Constraints

- 包管理与运行统一用 uv（`pyproject.toml` + `uv.lock`），`requires-python = ">=3.11,<3.15"`，运行期依赖只有 `lief==1.0.0`；dev group 保留 `meson`/`ninja`/`pytest`。
- **patch 阶段不得依赖 Xcode**：不得调用 `xcrun`/`otool`/iOS SDK。`ldid` 保留不变（跨平台可用，不换 `codesign`）。
- 严禁静默回落：版本不符、加密输入、bridge 契约不符、缺 `ldid`/`zip`/`unzip` 一律直接失败，错误信息必须说明原因并给出修复提示。
- 公开产物只有 `bridge` 一个变体；`baseline`/`weak-load-only`/`fallback` 及其验证 mode 全部移除。
- 输出默认名 `<输入名去扩展>-libass<libass 版本>.ipa`（当前 `nPlayer_3.13.0-libass0.17.5.ipa`）。
- 不改动已冻结的 Mach-O 改写机制、载荷字节布局、四态状态机与两处 NOP 站点（`0x100A0392C`、`0x100ACBC14`）。
- 不新增测试，除非它钉住用户可见的失败行为或既有的安全网；禁止文本匹配/正则探测式测试。
- 每个 Task 一个 Commit，消息尾部附 `Co-authored-by: Codex <codex@openai.com>`。
- README 用英文；本项目许可 MIT；`THIRD-PARTY.md` 逐依赖列许可/版本/来源（FriBidi 为 LGPL-2.1+，其余为 MIT/ISC/FTL）；不做 CI。
- 永远不发布 IPA、不提供解密；用户必须自己提供已解密的包。

## Review Focus

以下五类输入/失败模式是这份规格隐含但容易被写坏的地方，最可能先咬到真实用户（按可能性排序）。每条都在 Task 4 的测试里有对应断言。

1. **App Store 原包（FairPlay 加密）**：`crypt_id != 0`。合理期望是"明确告诉我需要已解密的包"，而不是抛一句 sha256 不符；绝不能产出能安装但一启动就崩的 IPA。
2. **不受支持的版本 / 已打过补丁的 IPA**：按 `main_sha256` 匹配失败时，必须列出已支持版本与输入 hash；已打过补丁的包会在 `preflight` 里因载荷 segment/桥路径/NOP 站点不符被拒绝。
3. **`--bridge` 指向错误或不匹配的 dylib**（旧版、非 arm64、缺导出、含第三方动态依赖）：必须拒绝并指名具体失败的 check，不能产出装不上或一加载就崩的包。
4. **输出路径异常**（同名存在、父目录不可写、写盘中断）：不得留下半个 IPA，临时文件必须清理，发布必须是原子替换。
5. **IPA 结构异常**（无 `Payload/nPlayer.app/nPlayer`、`Frameworks` 位置是文件而非目录）：明确失败，不猜路径、不静默跳过。

---

## File Structure

| 路径 | 职责 | 动作 |
|---|---|---|
| `npabridge/patch.py` | 公开流程唯一入口：串起匹配→校验→改写→组装→成品校验 | 新建 |
| `npabridge/manifest.py` | manifest 数据模型；`app_version`/`libass_version`/`target_abi`；按 sha256 选择 manifest | 修改 |
| `npabridge/target_abi.py` | `TargetABI` 数据类 + manifest 反序列化 + 不变式校验（不再含探针编译） | 修改 |
| `npabridge/macho.py` | 两阶段改写与 preflight；dylib 元数据改走 LIEF；ABI 来自 manifest | 修改 |
| `npabridge/verify.py` | 单产物具名 check（bridge 契约 + patched main + payload） | 修改 |
| `npabridge/package.py` | 单变体组装、伪签名、原子发布；源 IPA 由参数传入 | 修改 |
| `npabridge/payload.py` | 载荷生成（内部逻辑不变） | 不动 |
| `npabridge/build_bridge.py`、`npabridge/toolchain.py` | 从源码构建 dylib 与 host Keystone（dev 路径） | 不动 |
| `tools/patch.py` | 薄入口，转发到 `npabridge.patch:main` | 新建 |
| `npa-patch` | 仓库根入口脚本（`uv run --no-dev python tools/patch.py`） | 新建 |
| `tools/doctor.py` | 只检查 patch 真正需要的外部工具 | 修改 |
| `tools/verify.py` | 校验构建产物 + 合并 `dev/acceptance.json`（dev 用） | 修改 |
| `manifests/nplayer-3.13.0.json` | 版本锚点 + 冻结的 `target_abi` + `app_version`/`libass_version` | 修改 |
| `pyproject.toml` | 加 `[project.scripts] npa-patch` | 修改 |
| `Makefile` | 收敛为 dev 目标：bootstrap/deps/bridge/verify/test/smoke | 修改 |
| `tests/` | 公开路径测试（缺输入 IPA 时跳过） | 修改 |
| `dev/` | smoke 应用、ABI 探针、phase 入口、验收记录、研发计划、dev-only 测试 | 新建 |
| `README.md`、`LICENSE`、`THIRD-PARTY.md` | 对外文档与许可 | 新建 |

### 测试输入约定

公开仓库不含 IPA。测试模块统一从 `tests/support.py` 取输入：

```python
# tests/support.py
"""Shared test inputs. Tests skip when a dev-only input is absent."""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_IPA = Path(os.environ.get("NPA_SOURCE_IPA", ROOT.parent / "nPlayer_3.13.0.ipa"))
```

需要输入 IPA 的类在 `setUpClass` 开头写：

```python
if not SOURCE_IPA.is_file():
    raise unittest.SkipTest("source IPA is not present")
```

---

### Task 1: dylib 元数据改走 LIEF

**Files:**
- Modify: `npabridge/macho.py:157-193`（`_otool`、`xcrun_find`、`dependency_lines`、`install_name`）
- Test: `tests/test_macho_metadata.py`（新建）

**Interfaces:**
- Consumes: `npabridge.macho.parse(path) -> ParsedMachO`（属性 `lief` 是 LIEF binary，容器被持有）
- Produces: `macho.install_name(path: Path) -> str`、`macho.dependency_lines(path: Path) -> list[str]`（语义与 `otool -D` / `otool -L` 完全一致，self install name 排在第一位）、`macho.xcrun_find(name: str) -> str`（仅供 dev 构建使用）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_macho_metadata.py
import unittest
from pathlib import Path

from npabridge import macho

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
BRIDGE = BUILD / "LibASSBridge.dylib"


class MetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BRIDGE.is_file():
            raise unittest.SkipTest("LibASSBridge.dylib is not built")

    def test_install_name_and_dependencies_match_the_built_bridge(self):
        self.assertEqual(macho.install_name(BRIDGE), "@rpath/LibASSBridge.dylib")
        self.assertEqual(
            macho.dependency_lines(BRIDGE),
            [
                "@rpath/LibASSBridge.dylib",
                "/usr/lib/libiconv.2.dylib",
                "/usr/lib/libz.1.dylib",
                "/usr/lib/libSystem.B.dylib",
            ],
        )

    def test_dependency_lines_follow_otool_when_otool_is_available(self):
        import shutil
        import subprocess

        otool = shutil.which("otool") or "/usr/bin/xcrun"
        if not Path(otool).is_file():
            self.skipTest("otool is not available")
        command = [otool, "-L", str(BRIDGE)] if otool.endswith("otool") else ["/usr/bin/xcrun", "otool", "-L", str(BRIDGE)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.skipTest("otool is not usable")
        expected = [line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()]
        self.assertEqual(macho.dependency_lines(BRIDGE), expected)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_macho_metadata.py -v`
Expected: 失败或报错——当前 `dependency_lines` 走 `/usr/bin/xcrun otool`。

- [ ] **Step 3: 实现 LIEF 版本**

替换 `npabridge/macho.py` 中的 `_otool`/`dependency_lines`/`install_name`：

```python
def dependency_lines(path: Path) -> list[str]:
    """Dependencies and the self install name, in otool -L order."""

    binary = parse(path)
    return [str(library.name) for library in binary.libraries]


def install_name(path: Path) -> str:
    binary = parse(path)
    if not binary.has(lief.MachO.LoadCommand.TYPE.ID_DYLIB):
        raise ValueError(f"no LC_ID_DYLIB in {path}")
    for command in binary.commands:
        if command.command == lief.MachO.LoadCommand.TYPE.ID_DYLIB:
            return str(command.name)
    raise ValueError(f"LC_ID_DYLIB is unreadable in {path}")
```

`_otool` 从 `npabridge/macho.py` 删除；`xcrun_find` 保留（`build_bridge.py` 用）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_macho_metadata.py tests/test_verify.py tests/test_bridge.py -v`
Expected: PASS（`test_bridge.py` 的 7 个 bridge check 仍全绿）。

- [ ] **Step 5: 提交**

```bash
git add npabridge/macho.py tests/test_macho_metadata.py
git commit -m "refactor: read dylib metadata through LIEF

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 2: 把 iOS ABI 冻结进 manifest

**Files:**
- Modify: `npabridge/target_abi.py`（只留数据类 + 反序列化 + 校验）
- Create: `dev/abi_probe.py`（探针编译与读取；由 `npabridge/target_abi.py` 拆出）
- Move: `tools/target_abi_probe.c` → `dev/target_abi_probe.c`
- Modify: `npabridge/manifest.py`、`npabridge/macho.py:277-350,383-453`、`npabridge/verify.py`、`manifests/nplayer-3.13.0.json`
- Test: `tests/test_manifest.py`（增加断言）、`dev/tests/test_abi_probe.py`（新建）

**Interfaces:**
- Produces: `manifest.Manifest` 新增字段 `app_version: str`、`libass_version: str`、`target_abi: TargetABI`；`target_abi.from_manifest(data: dict) -> TargetABI`；`dev/abi_probe.py` 的 `load_target_abi(sdk: Path) -> TargetABI`（dev 校验用）
- Consumes: 现有 `macho.phase_a(baseline, layout, manifest, target_abi=None)` / `phase_b(layout, output, manifest, target_abi=None)` 签名不变，但缺省值改为 `manifest.target_abi`

- [ ] **Step 1: 记录当前探针输出**

Run:
```bash
uv run python -c "
from pathlib import Path
from npabridge.target_abi import load_target_abi
from npabridge import macho
abi = load_target_abi(macho.sdk_path())
print(abi)
"
```
把输出的各字段抄进 Step 3 的 manifest 片段（`platform`、`minos`、`sdk`、`dl_info_size`、四个偏移、`rtld_default_masked`）。已知实机值：`platform=IOS`、`minos=(13,0,0)`、`sdk=(26,2,0)`、`dl_info_size=32`、偏移 `0/8/16/24`、`rtld_default_masked=18446744073709551614`。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_manifest.py 追加（放在已有 ManifestTests 内）
    def test_manifest_declares_version_and_frozen_abi(self):
        manifest = load_manifest(MANIFEST)
        self.assertEqual(manifest.app_version, "3.13.0")
        self.assertEqual(manifest.libass_version, "0.17.5")
        self.assertEqual(manifest.target_abi.platform.upper(), "IOS")
        self.assertEqual(manifest.target_abi.dl_info_size, 32)
        self.assertEqual(
            (
                manifest.target_abi.dl_info_fname_offset,
                manifest.target_abi.dl_info_fbase_offset,
                manifest.target_abi.dl_info_sname_offset,
                manifest.target_abi.dl_info_saddr_offset,
            ),
            (0, 8, 16, 24),
        )
        self.assertEqual(manifest.target_abi.rtld_default_masked, (1 << 64) - 2)

    def test_manifest_libass_version_matches_the_pinned_source(self):
        import json

        lock = json.loads((ROOT / "deps" / "sources.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(
            load_manifest(MANIFEST).libass_version, lock["sources"]["libass"]["version"]
        )
```

- [ ] **Step 3: 冻结 ABI 到 manifest 并让 patch 路径不再调用 xcrun**

`manifests/nplayer-3.13.0.json` 顶层新增（值取自 Step 1）：

```json
{
  "app_version": "3.13.0",
  "libass_version": "0.17.5",
  "target_abi": {
    "platform": "IOS",
    "minos": [13, 0, 0],
    "sdk": [26, 2, 0],
    "dl_info_size": 32,
    "dl_info_fname_offset": 0,
    "dl_info_fbase_offset": 8,
    "dl_info_sname_offset": 16,
    "dl_info_saddr_offset": 24,
    "rtld_default_masked": "0xfffffffffffffffe"
  }
}
```

`npabridge/target_abi.py` 保留 `TargetABI` 与新增：

```python
def from_manifest(data: dict) -> TargetABI:
    values = {
        "platform": str(data["platform"]),
        "minos": _version_tuple(data["minos"]),
        "sdk": _version_tuple(data["sdk"]),
        "dl_info_size": int(data["dl_info_size"]),
        "dl_info_fname_offset": int(data["dl_info_fname_offset"]),
        "dl_info_fbase_offset": int(data["dl_info_fbase_offset"]),
        "dl_info_sname_offset": int(data["dl_info_sname_offset"]),
        "dl_info_saddr_offset": int(data["dl_info_saddr_offset"]),
        "rtld_default_masked": int(data["rtld_default_masked"], 0)
        if isinstance(data["rtld_default_masked"], str)
        else int(data["rtld_default_masked"]),
    }
    abi = TargetABI(**values)
    _require(abi.platform.upper() == "IOS", "target ABI platform is not iOS")
    _require(abi.minos[:2] == (13, 0), f"target ABI minos is not iOS 13: {abi.minos}")
    offsets = (
        abi.dl_info_fname_offset,
        abi.dl_info_fbase_offset,
        abi.dl_info_sname_offset,
        abi.dl_info_saddr_offset,
    )
    _require(all(0 <= offset < abi.dl_info_size for offset in offsets), "invalid Dl_info offset")
    _require(
        list(offsets) == sorted(offsets) and len(set(offsets)) == 4,
        "Dl_info offsets are not distinct and increasing",
    )
    _require(
        abi.rtld_default_masked == (1 << 64) - 2,
        f"unexpected RTLD_DEFAULT bit pattern: {abi.rtld_default_masked}",
    )
    return abi
```

`load_target_abi`、`_compile_probe`、`_probe_paths`、`_read_symbol`、`REQUIRED_SYMBOLS` 整体移入 `dev/abi_probe.py`（`ROOT` 改为 `parents[1]`，探针源路径改为 `dev/target_abi_probe.c`，`build/abi_probe.o` 作输出），并新增一个打印 manifest 片段的 `--emit`：

```python
if __name__ == "__main__":
    import json
    import sys

    from npabridge import macho

    abi = load_target_abi(macho.sdk_path())
    print(json.dumps({
        "platform": abi.platform,
        "minos": list(abi.minos),
        "sdk": list(abi.sdk),
        "dl_info_size": abi.dl_info_size,
        "dl_info_fname_offset": abi.dl_info_fname_offset,
        "dl_info_fbase_offset": abi.dl_info_fbase_offset,
        "dl_info_sname_offset": abi.dl_info_sname_offset,
        "dl_info_saddr_offset": abi.dl_info_saddr_offset,
        "rtld_default_masked": hex(abi.rtld_default_masked),
    }, indent=2, sort_keys=True))
    sys.exit(0)
```

`npabridge/manifest.py`：`Manifest` 增加 `app_version`、`libass_version`、`target_abi` 三个字段并在 `load_manifest` 里填充（`target_abi=target_abi.from_manifest(data["target_abi"])`）。

`npabridge/macho.py`、`npabridge/verify.py`：把 `load_target_abi(sdk_path())` 全部换成 `manifest.target_abi`，并在文件头删除 `from .target_abi import ... load_target_abi` 中的 `load_target_abi`。`payload._validate_abi` 不变。

- [ ] **Step 4: 运行测试与全链路**

Run: `uv run pytest -q`
Expected: PASS（`test_macho.py`、`test_verify.py` 不再需要 SDK）。

Run:
```bash
rm -rf build/macho && uv run python tools/phase_a.py && uv run python tools/phase_b.py && shasum -a 256 build/macho/main-phase-b
```
Expected: `4bb9f5670062c2a7eee5797a02ccb066abdc68a3610f5b01037e123e862a79f3`（布局与字节不变）。

- [ ] **Step 5: 提交**

```bash
git add -A npabridge dev tools manifests tests
git commit -m "feat: freeze the target ABI into the manifest

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 3: 收窄到单产物

**Files:**
- Modify: `npabridge/package.py`、`npabridge/verify.py`、`tools/verify.py`、`Makefile`、`tools/package.py`（删除）
- Test: `tests/test_package.py`、`tests/test_verify.py`

**Interfaces:**
- Produces: `package.publish(source_ipa: Path, output: Path, main: Path, bridge: Path) -> dict`；`verify.verify_artifact(baseline: Path, patched: Path, manifest: Manifest, bridge: Path) -> VerificationReport`（无 `mode` 参数）；`verify.MODES` 删除
- Consumes: Task 2 的 `Manifest.target_abi`

- [ ] **Step 1: 改测试（失败的先写）**

`tests/test_package.py`：

```python
from support import ROOT, SOURCE_IPA

BUILD = ROOT / "build" / "package"
MAIN = ROOT / "build" / "macho" / "main-phase-b"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE_IPA.is_file():
            raise unittest.SkipTest("source IPA is not present")
        if not MAIN.is_file() or not BRIDGE.is_file():
            raise unittest.SkipTest("patch artifacts are not built")
        cls.artifact = BUILD / "test" / "bridge.ipa"
        package.publish(SOURCE_IPA, cls.artifact, MAIN, BRIDGE)
```

`test_bridge_variant_carries_one_main_and_one_bridge` 重命名为 `test_artifact_carries_one_main_and_one_bridge`，断言不变（`inspect_ipa(cls.artifact)`，不再传 `expect_bridge`）。

`tests/test_verify.py`：`test_extra_export_is_rejected`（需要 clang + iOS SDK 重新链接）整体移到 `dev/tests/test_bridge_link.py`；其余三个测试保留，`setUpClass` 增加 IPA 与 bridge 存在性判断（`verify_artifact` 现在要求 bridge），`verify_artifact(...)` 调用去掉 `mode`：

```python
    @classmethod
    def setUpClass(cls):
        if not SOURCE_IPA.is_file():
            raise unittest.SkipTest("source IPA is not present")
        if not build_bridge.OUTPUT.is_file():
            raise unittest.SkipTest("LibASSBridge.dylib is not built")
        ...
        report = verify_artifact(
            self.baseline,
            self.patched,
            MANIFEST,
            build_bridge.OUTPUT,
        )
```

并在文件头用 `from support import SOURCE_IPA` 取代 `IPA = ROOT.parent / "nPlayer_3.13.0.ipa"`（`ROOT` 仍从 `Path(__file__).resolve().parents[1]` 取，供 `MANIFEST`/`BUILD` 使用）。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_package.py tests/test_verify.py -v`
Expected: 失败（`publish` 仍要求 variant、`verify_artifact` 仍带 mode）。

- [ ] **Step 3: 收窄实现**

`npabridge/package.py`：
- 删除 `VARIANTS`、`SOURCE_IPA`、`publish()` 的 `variant` 参数与 `_require(output.stem == variant, ...)`；`package_ipa` 的 `mode` 固定为 `"bridge"`；`inspect_ipa(path)` 不再需要 `expect_bridge` 参数（永远要求 1 个 bridge）。
- `WORK_ROOT` 改为 `ROOT / "build" / "patch"`；`DIST` 删除（输出由调用方给）。
- `sign()` 与打包用的 `zip`/`unzip` 改为按 PATH 解析并给出可执行的报错：

```python
def _tool(name: str, hint: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} is required to assemble the IPA ({hint})")
    return path
```

`unzip` → `_tool("unzip", "install unzip, or use macOS's built-in /usr/bin/unzip")`；`zip` → `_tool("zip", "install zip, or use macOS's built-in /usr/bin/zip")`；`ldid` 沿用同类报错（`brew install ldid`）。

`npabridge/verify.py`：
- 删除 `MODES`、`verify_main` 的 `mode` 参数与 `baseline`/`weak-load-only` 两个分支，只留原 `else`（full dispatch）分支的 checks：`main.baseline_hash`、`main.entrypoint`、`main.bindings`、`main.segments`、`main.sections`、`main.dylib_ordinals`、`main.instructions`、`main.call_sites`、`main.nops`、`payload.*`。
- `verify_artifact(baseline, patched, manifest, bridge, target_abi=None)`：`bridge` 必填；返回的 `VerificationReport.mode` 固定 `"bridge"`。
- `VerificationReport.write()` 保留（dev 报告用）。

`tools/verify.py`：`--all` 改为校验 `dist/bridge.ipa`（或 `--ipa`）单个产物；移除 `--baseline` 模式推断；保留把 `dev/acceptance.json` 合并进报告的 `load_acceptance()`（Task 6 里改路径）。

`Makefile`：删除 `baseline`/`weak-load-only`/`fallback`/`dist` 目标与 `tools/package.py`；`smoke` 指向 `dev/tools/smoke.py`（Task 6 后生效）：

```make
.PHONY: bootstrap deps bridge verify test smoke clean

bootstrap:
	$(UV) sync --frozen --all-groups
	$(UV) run python tools/keystone.py

deps:
	$(UV) run python deps/build_deps.py

bridge:
	$(UV) run python -m npabridge.build_bridge

verify:
	$(UV) run python tools/verify.py

test:
	$(UV) run pytest

smoke:
	$(UV) run python dev/tools/smoke.py

clean:
	rm -rf build dist
```

`tools/package.py` 删除（功能并入 Task 4 的 `npabridge/patch.py`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest -q`
Expected: PASS；`uv run python tools/verify.py --ipa dist/bridge.ipa` 全绿。

- [ ] **Step 5: 提交**

```bash
git add -A npabridge tools tests Makefile
git commit -m "refactor: narrow the toolchain to the single bridge artifact

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 4: 公开 patch 流程

**Files:**
- Create: `npabridge/patch.py`
- Modify: `npabridge/manifest.py`（新增 `select_manifest`）
- Test: `tests/test_patch.py`（新建）

**Interfaces:**
- Consumes: Task 1 的元数据 helper、Task 2 的 `Manifest.target_abi`、Task 3 的 `package.publish` / `verify.verify_artifact` / `verify.verify_bridge`
- Produces:
  - `manifest.select_manifest(directory: Path, main: Path) -> tuple[Manifest, Path]`（按 `main_sha256` 匹配，失败抛 `ValueError` 并列出已支持版本）
  - `patch.patch_ipa(source: Path, output: Path | None, bridge: Path, manifests: Path, work: Path | None = None) -> PatchResult`（`output=None` 时按 `<输入名>-libass<版本>.ipa` 命名）
  - `patch.main(argv: list[str] | None = None) -> int`
  - `patch.PatchResult`（`source`、`output`、`app_version`、`libass_version`、`manifest`、`bridge`、`source_main_sha256`、`patched_main_sha256`、`packaged_main_sha256`、`bridge_sha256`、`state_initial`、`checks`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_patch.py
import hashlib
import shutil
import struct
import unittest
from pathlib import Path
from zipfile import ZipFile

from npabridge import patch
from npabridge.manifest import select_manifest

from support import ROOT, SOURCE_IPA


MANIFESTS = ROOT / "manifests"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"
# 既有设备验收产物里的 main 成员哈希（nPlayer 名下的 ldid 签名结果）
PACKAGED_MAIN_SHA256 = "19d3447193bcd66e03b850876a1281c4bceac087dd50cf6db534e0527fb3a887"


class PatchFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE_IPA.is_file():
            raise unittest.SkipTest("source IPA is not present")
        if not BRIDGE.is_file():
            raise unittest.SkipTest("LibASSBridge.dylib is not built")
        cls.work = ROOT / "build" / "patch" / "test"
        cls.work.mkdir(parents=True, exist_ok=True)

    def test_patch_produces_the_known_packaged_main(self):
        output = self.work / "patched.ipa"
        result = patch.patch_ipa(SOURCE_IPA, output, BRIDGE, MANIFESTS, work=self.work / "run")
        self.assertEqual(result.app_version, "3.13.0")
        self.assertEqual(result.libass_version, "0.17.5")
        self.assertTrue(output.is_file())
        self.assertEqual(result.state_initial, 0)
        with ZipFile(output) as archive:
            main = archive.read(patch.MAIN_MEMBER)
        self.assertEqual(hashlib.sha256(main).hexdigest(), PACKAGED_MAIN_SHA256)

    def test_unsupported_version_lists_the_supported_ones(self):
        unknown = self.work / "unknown-main"
        unknown.write_bytes(b"\x00" * 16)
        with self.assertRaises(ValueError) as caught:
            select_manifest(MANIFESTS, unknown)
        message = str(caught.exception)
        self.assertIn("3.13.0", message)
        self.assertIn(hashlib.sha256(b"\x00" * 16).hexdigest(), message)

    def test_encrypted_input_is_reported_as_encrypted(self):
        encrypted = self.work / "encrypted.ipa"
        self._write_ipa_with_crypt_id(SOURCE_IPA, encrypted, 1)
        with self.assertRaises(ValueError) as caught:
            patch.patch_ipa(encrypted, self.work / "encrypted-out.ipa", BRIDGE, MANIFESTS, work=self.work / "enc")
        self.assertIn("encrypted", str(caught.exception).lower())
        self.assertFalse((self.work / "encrypted-out.ipa").exists())

    def test_invalid_bridge_is_rejected_by_name(self):
        with self.assertRaises(Exception) as caught:
            patch.patch_ipa(
                SOURCE_IPA,
                self.work / "bad-bridge.ipa",
                Path("/usr/lib/libSystem.B.dylib"),
                MANIFESTS,
                work=self.work / "bad",
            )
        self.assertIn("bridge.exports", str(caught.exception))
        self.assertFalse((self.work / "bad-bridge.ipa").exists())

    def _write_ipa_with_crypt_id(self, source: Path, output: Path, crypt_id: int) -> None:
        """Flip crypt_id in place so the input looks like an App Store package."""

        main = self.work / "encrypted-main"
        with ZipFile(source) as archive:
            main.write_bytes(archive.read(patch.MAIN_MEMBER))
        raw = bytearray(main.read_bytes())
        offset = 32  # sizeof(struct mach_header_64)
        while offset < len(raw) - 24:
            command, size = struct.unpack_from("<II", raw, offset)
            if command == 0x2C and size >= 24:  # LC_ENCRYPTION_INFO_64
                struct.pack_into("<I", raw, offset + 16, crypt_id)
                break
            self.assertGreaterEqual(size, 8, "malformed load command")
            offset += size
        else:
            self.fail("LC_ENCRYPTION_INFO_64 is missing")
        main.write_bytes(bytes(raw))
        shutil.copy2(source, output)
        with ZipFile(output, "a") as archive:
            archive.write(main, patch.MAIN_MEMBER)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_patch.py -v`
Expected: `ModuleNotFoundError: npabridge.patch`。

- [ ] **Step 3: 实现 manifest 选择与 patch 流程**

`npabridge/manifest.py` 追加：

```python
def select_manifest(directory: Path, main: Path) -> Manifest:
    """Pick the manifest whose frozen main hash matches this executable."""

    digest = hashlib.sha256(main.read_bytes()).hexdigest()
    candidates = []
    supported = []
    for path in sorted(Path(directory).glob("*.json")):
        manifest = load_manifest(path)
        supported.append(manifest.app_version)
        if manifest.main_sha256 == digest:
            return manifest
        candidates.append((path, manifest))
    raise ValueError(
        f"no manifest matches this main executable\n"
        f"  input sha256: {digest}\n"
        f"  supported nPlayer versions: {', '.join(sorted(supported)) or 'none'}\n"
        f"  (a patched IPA and an App Store-encrypted dump both fail this check)"
    )
```

`npabridge/patch.py`：

```python
"""Patch one nPlayer IPA with the prebuilt LibASSBridge dylib.

The flow is deliberately linear and fails loudly: resolve the version from
the input's SHA-256, refuse encrypted input, verify the bridge contract,
freeze the layout, rewrite the call sites, assemble and pseudo-sign, then
verify the shipped pair before publishing it atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from . import macho, package, verify
from .manifest import Manifest, select_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "manifests"
MAIN_MEMBER = package.MAIN_MEMBER
APP_DIR = package.APP_DIR


@dataclass(frozen=True)
class PatchResult:
    source: Path
    output: Path
    app_version: str
    libass_version: str
    manifest: Path
    bridge: Path
    source_main_sha256: str
    patched_main_sha256: str
    packaged_main_sha256: str
    bridge_sha256: str
    state_initial: int
    checks: tuple[dict, ...]

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "output": str(self.output),
            "app_version": self.app_version,
            "libass_version": self.libass_version,
            "manifest": str(self.manifest),
            "bridge": str(self.bridge),
            "source_main_sha256": self.source_main_sha256,
            "patched_main_sha256": self.patched_main_sha256,
            "packaged_main_sha256": self.packaged_main_sha256,
            "bridge_sha256": self.bridge_sha256,
            "state_initial": self.state_initial,
            "checks": [dict(check) for check in self.checks],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_main(source: Path, destination: Path) -> Path:
    with ZipFile(source) as archive:
        names = set(archive.namelist())
        if MAIN_MEMBER not in names:
            raise ValueError(f"{source.name} does not carry {MAIN_MEMBER}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read(MAIN_MEMBER))
    return destination


def _reject_encrypted(main: Path) -> None:
    binary = macho.parse(main)
    if not binary.has_encryption_info or int(binary.encryption_info.crypt_id) != 0:
        raise ValueError(
            "this IPA is still FairPlay-encrypted; a decrypted dump of your own "
            "purchase is required (crypt_id != 0)"
        )


def patch_ipa(source, output, bridge, manifests, work=None) -> PatchResult:
    source = Path(source).resolve()
    bridge = Path(bridge).resolve()
    manifests = Path(manifests).resolve()
    for label, path in (("source IPA", source), ("bridge dylib", bridge)):
        if not path.is_file():
            raise FileNotFoundError(f"{label} is missing: {path}")
    if not manifests.is_dir():
        raise FileNotFoundError(f"manifest directory is missing: {manifests}")
    work = Path(work) if work is not None else source.parent / ".npa-patch"
    work.mkdir(parents=True, exist_ok=True)

    source_main = _extract_main(source, work / "source-main")
    source_digest = _sha256(source_main)
    _reject_encrypted(source_main)
    manifest, manifest_path = select_manifest(manifests, source_main)
    macho.preflight(source_main, manifest)

    output = (
        Path(output).resolve()
        if output is not None
        else source.with_name(f"{source.stem}-libass{manifest.libass_version}.ipa")
    )
    if output == source:
        raise ValueError("refusing to overwrite the source IPA; pass -o")

    contract = verify.verify_bridge(bridge, manifest)
    contract.require()

    macho.nop_only_main(source_main, work / "main-nop")
    macho.phase_a(work / "main-nop", work / "main-phase-a", manifest)
    macho.phase_b(work / "main-phase-a", work / "main-phase-b", manifest)

    temporary = output.with_name(f".tmp-{output.name}")
    temporary.unlink(missing_ok=True)
    try:
        package.package_ipa(source, temporary, work / "main-phase-b", bridge)
        extracted = package.extract_for_verification(temporary, work / "shipped")
        report = verify.verify_artifact(
            source_main, extracted["main"], manifest, extracted["bridge"]
        )
        report.require()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return PatchResult(
        source=source,
        output=output,
        app_version=manifest.app_version,
        libass_version=manifest.libass_version,
        manifest=manifest_path,
        bridge=bridge,
        source_main_sha256=source_digest,
        patched_main_sha256=_sha256(work / "main-phase-b"),
        packaged_main_sha256=_sha256(extracted["main"]),
        bridge_sha256=_sha256(extracted["bridge"]),
        state_initial=report.state_initial,
        checks=tuple(check.as_dict() for check in report.checks),
    )
```

`main()`：

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="npa-patch",
        description="Patch a decrypted nPlayer IPA with the prebuilt LibASSBridge dylib.",
    )
    parser.add_argument("source", type=Path, help="your own decrypted nPlayer .ipa")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument(
        "--bridge",
        type=Path,
        default=Path.cwd() / "LibASSBridge.dylib",
        help="LibASSBridge.dylib from the release assets",
    )
    parser.add_argument("--manifests", type=Path, default=MANIFESTS)
    arguments = parser.parse_args(argv)
    try:
        result = patch_ipa(
            arguments.source,
            arguments.output,
            arguments.bridge,
            arguments.manifests,
        )
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    except Exception as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0
```

默认输出名由 `patch_ipa` 在匹配完 manifest 之后决定（`output=None` 时用 `libass_version` 命名并把最终路径放进 `PatchResult.output`）；`-o` 显式给出时直接使用。`manifest.py` 需要新增 `import hashlib`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_patch.py -v`
Expected: PASS（`test_patch_produces_the_known_packaged_main` 断言 `19d34471…` 即既有交付物的 main 成员哈希）。

- [ ] **Step 5: 提交**

```bash
git add npabridge/patch.py npabridge/manifest.py tests/test_patch.py
git commit -m "feat: add the single public patch flow

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 5: 三个入口与 doctor

**Files:**
- Create: `tools/patch.py`、`npa-patch`
- Modify: `pyproject.toml`、`tools/doctor.py`

**Interfaces:**
- Consumes: `npabridge.patch.main`
- Produces: `npa-patch` console script、`./npa-patch` 可执行脚本、`uv run python tools/patch.py`

- [ ] **Step 1: 写入口**

`tools/patch.py`（与仓库既有 tools 风格一致）：

```python
"""Thin entry point for the public patch flow."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge.patch import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
```

`npa-patch`（仓库根，`chmod +x`）：

```sh
#!/bin/sh
# Patch an nPlayer IPA with the prebuilt LibASSBridge dylib.
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec uv run --no-dev python "$here/tools/patch.py" "$@"
```

`pyproject.toml`：

```toml
[project.scripts]
npa-patch = "npabridge.patch:main"
```

`tools/doctor.py`：`TOOLS` 改为 `("ldid", "zip", "unzip")`，删除 `ios_sdk_path()` 与 `xcrun`/`clang`/`cmake`/`ninja` 检查，缺失时打印具体安装提示：

```python
HINTS = {
    "ldid": "brew install ldid",
    "zip": "macOS ships /usr/bin/zip; on Linux install zip",
    "unzip": "macOS ships /usr/bin/unzip; on Linux install unzip",
}
```

- [ ] **Step 2: 验证三个入口**

Run:
```bash
uv run python tools/patch.py --help >/dev/null && echo "tools/patch ok"
./npa-patch --help >/dev/null && echo "root entry ok"
uv run npa-patch --help >/dev/null && echo "console script ok"
uv run python tools/doctor.py
```
Expected: 前三行 ok；doctor 列出 ldid/zip/unzip 均为路径（`required=ok`）。

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml tools/patch.py tools/doctor.py npa-patch
git commit -m "feat: expose the patch flow through three entry points

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 6: 研发期材料移入 dev/

**Files:**
- Move: `smoke/` → `dev/smoke/`、`tools/smoke.py` → `dev/tools/smoke.py`、`tools/phase_a.py`/`tools/phase_b.py` → `dev/tools/`、`acceptance.json` → `dev/acceptance.json`、`docs/superpowers/plans/2026-09-24-libass-bridge-prototype.md` → `dev/plans/`
- Move: `tests/test_dependencies.py`、`tests/test_toolchain.py` → `dev/tests/`
- Move: `npabridge/package.py` 的 `publish_app_bundle` + `inspect_app_ipa` → `dev/smoke_package.py`（原样搬运，只调整 `ROOT`/`sign` 导入）
- Create: `dev/README.md`、`tests/support.py`（若 Task 3/4 尚未创建）
- Modify: `dev/tools/smoke.py`（改从 `dev/smoke_package.py` 导入）、`tools/verify.py`（`ACCEPTANCE` 路径改 `dev/acceptance.json`）

**Interfaces:**
- Produces: `dev/README.md` 说明源码重建、设备验收复现、发布产物流程
- Consumes: 无

- [ ] **Step 1: 移动文件并修正路径**

```bash
mkdir -p dev/tools dev/plans dev/tests
git mv smoke dev/smoke
git mv tools/smoke.py dev/tools/smoke.py
git mv tools/phase_a.py dev/tools/phase_a.py
git mv tools/phase_b.py dev/tools/phase_b.py
git mv acceptance.json dev/acceptance.json
git mv docs/superpowers/plans/2026-09-24-libass-bridge-prototype.md dev/plans/
git mv tests/test_dependencies.py dev/tests/test_dependencies.py
git mv tests/test_toolchain.py dev/tests/test_toolchain.py
```

每个 `dev/tools/*.py` 的 `ROOT = Path(__file__).resolve().parents[1]` 改为 `parents[2]`（`dev/tools/x.py` → 仓库根）；`dev/smoke` 相关路径同步（`tools/smoke.py` 里的 `dev/smoke/BridgeSmoke/...`）。`dev/tests/*` 加一行 `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`，因 pytest 默认 `rootdir` 的 `pythonpath` 仍是仓库根。

`tools/verify.py`：`ACCEPTANCE = ROOT / "dev" / "acceptance.json"`。

- [ ] **Step 2: 写 dev/README.md**

```markdown
# Developer material

This directory holds everything that is not needed to patch an IPA.

- `smoke/` + `tools/smoke.py` — the BridgeSmoke app that proves the 15-export
  ABI contract on device. Requires Xcode and the iOS SDK.
- `tools/phase_a.py`, `tools/phase_b.py` — run the two layout stages separately
  to isolate a failure.
- `abi_probe.py` + `target_abi_probe.c` — regenerate/verify the frozen
  `target_abi` block of `manifests/*.json` against a real iOS SDK.
- `acceptance.json` — the recorded device acceptance (iPhone SE 3rd gen,
  iOS 17.7.2, LiveContainer 3.7.2).
- `plans/` — the research plan that produced this toolchain.
- `tests/` — dev-only tests (dependency closure, Keystone/probe, SDK-linked
  bridge mutation).

## Rebuild everything from source

```sh
uv sync --frozen --all-groups
make bootstrap deps bridge      # host Keystone, iOS dependency closure, bridge dylib
make verify test
```

`deps/sources.lock.json` pins every dependency (version, archive URL, SHA-256);
the closure is built with `deps/ios-arm64.cross` and `deps/macos-arm64.native`.

## Re-run the device acceptance

Build `dev/smoke` and the release dylib, install them on a device, then follow
`plans/2026-09-24-libass-bridge-prototype.md` (Task 11) and append the result to
`acceptance.json`. The expected packaged main hash is
`19d3447193bcd66e03b850876a1281c4bceac087dd50cf6db534e0527fb3a887`.

## Release checklist

1. `make bridge` and run `make verify`.
2. Publish `build/LibASSBridge.dylib` in a GitHub release with its SHA-256.
3. Update `THIRD-PARTY.md` when any pinned dependency version changes.
4. Run `dev/abi_probe.py` if the iOS SDK changed and re-check `target_abi`.
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest -q && uv run pytest dev/tests -q`
Expected: 公开测试 PASS（缺 IPA 时跳过）；dev 测试在装了 Xcode 的机器上 PASS。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: move the research material under dev

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 7: 对外文档与许可

**Files:**
- Create: `README.md`、`LICENSE`、`THIRD-PARTY.md`

**Interfaces:**
- Consumes: Task 5 的三个入口、Task 6 的 `dev/README.md`

- [ ] **Step 1: 写 README.md（英文）**

必须包含的章节与事实（不得留 TBD）：

```markdown
# nPlayer LibASS Bridge

Replace the bundled libass 0.13 stack in your own **nPlayer 3.13.0** install
with libass 0.17.5, without jailbreaking and without inline hooks: the patched
IPA contains an added 15-export dylib and sixteen redirected call sites.

## What this does

- `npa-patch` rewrites a decrypted nPlayer IPA you own and pseudo-signs it.
- The subtitle rendering path is switched to `LibASSBridge.dylib`
  (libass 0.17.5 + FreeType + HarfBuzz + FriBidi + fontconfig + expat,
  statically linked, no third-party dynamic dependency).

## Requirements

- macOS or Linux, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), `ldid`
  (`brew install ldid`), `zip`/`unzip`.
- Your own **decrypted** nPlayer 3.13.0 IPA. App Store packages are
  FairPlay-encrypted and are rejected on purpose; this project ships no IPA
  and no decryption.
- No Xcode, no iOS SDK, no jailbreak.

## Quick start

```sh
git clone <repository> && cd <repository>
# put LibASSBridge.dylib from the release assets next to this file
uv run npa-patch "/path/to/nPlayer_3.13.0.ipa"
# -> nPlayer_3.13.0-libass0.17.5.ipa
```

`--bridge`, `-o/--output`, `--manifests` are the only options. Install the
result with your usual sideload tool or LiveContainer; that tool re-signs the
whole bundle.

## Supported versions

Exactly the versions listed under `manifests/`. The input executable is matched
by SHA-256, so an unsupported or already-patched IPA fails before anything is
written. Adding a version means adding one manifest (see `dev/README.md`).

## Verification status

Patched artifacts were verified on device (iPhone SE 3rd generation, iOS
17.7.2, LiveContainer 3.7.2): state dispatch reaches the new library, SRT and
embedded ASS render, Matroska embedded fonts and the font cache work, and
continuous playback stays correct. `dev/acceptance.json` records the detail;
`dev/` holds the standalone bridge smoke app and the reverse-engineering plan.

## Troubleshooting

| Message | Cause | Fix |
| --- | --- | --- |
| `still FairPlay-encrypted` | App Store package | provide a decrypted dump |
| `no manifest matches this main executable` | wrong nPlayer version, or already patched | use a supported clean dump |
| `bridge.exports` / `bridge.install_name` failed | wrong or stale dylib | use the dylib from the matching release |
| `ldid is required to assemble the IPA` | missing ldid | `brew install ldid` |

## Legal

MIT for this toolchain (see `LICENSE`); third-party notices in
`THIRD-PARTY.md`. Not affiliated with, or endorsed by, the nPlayer authors.
You must own a licence for nPlayer and patch only your own copy.
```

- [ ] **Step 2: 写 LICENSE（MIT）与 THIRD-PARTY.md**

`LICENSE` 用标准 MIT 全文，版权行为 `<copyright holder>`（发布前由用户填入姓名或组织；不要留占位符进仓库）。

`THIRD-PARTY.md` 表格逐行给出依赖、许可、版本、来源，版本必须与 `deps/sources.lock.json` 一致：

| Dependency | License | Version | Source |
| --- | --- | --- | --- |
| libass | ISC | 0.17.5 | https://github.com/libass/libass |
| FreeType | FTL / GPL-2.0 | 2.14.3 | https://github.com/freetype/freetype |
| HarfBuzz | MIT | 14.2.1 | https://github.com/harfbuzz/harfbuzz |
| FriBidi | LGPL-2.1-or-later | 1.0.16 | https://github.com/fribidi/fribidi |
| fontconfig | MIT-like | 2.17.1 | https://gitlab.freedesktop.org/fontconfig/fontconfig |
| expat | MIT | 2.8.5 | https://github.com/libexpat/libexpat |

并写明：这些库被静态链接进 `LibASSBridge.dylib`；全部由本仓库内 `deps/sources.lock.json` 的 pin 源码构建，任何人可用 `dev/README.md` 的步骤重建同一闭包，因此 LGPL 组件的源码可得性得到满足。

- [ ] **Step 3: 提交**

```bash
git add README.md LICENSE THIRD-PARTY.md
git commit -m "docs: add the public README, license and third-party notices

Co-authored-by: Codex <codex@openai.com>"
```

---

### Task 8: 验收与执行记录

**Files:**
- Modify: `docs/superpowers/plans/2026-09-25-public-patch-toolchain.md`（追加"执行记录"）

- [ ] **Step 1: 公开测试面**

Run: `uv run pytest -q`
Expected: 全绿；输入 IPA 缺失时相关测试明确 skip（不得静默通过）。

- [ ] **Step 2: 字节锚点（重构不得改变产物）**

Run:
```bash
uv run npa-patch ../nPlayer_3.13.0.ipa -o build/accept/bridge.ipa
python3 - <<'PY'
import hashlib, zipfile
z = zipfile.ZipFile("build/accept/bridge.ipa")
main = z.read("Payload/nPlayer.app/nPlayer")
print("packaged main:", hashlib.sha256(main).hexdigest())
PY
shasum -a 256 build/macho/main-phase-b
```
Expected: `packaged main` = `19d3447193bcd66e03b850876a1281c4bceac087dd50cf6db534e0527fb3a887`；`main-phase-b` = `4bb9f5670062c2a7eee5797a02ccb066abdc68a3610f5b01037e123e862a79f3`。

- [ ] **Step 3: 证明 patch 阶段不用 Xcode**

Run: `env DEVELOPER_DIR=/nonexistent uv run npa-patch ../nPlayer_3.13.0.ipa -o build/accept/no-xcode.ipa`
Expected: 成功；若任何环节调用 `xcrun`，该命令会失败。

- [ ] **Step 4: 成品校验与 dev 报告**

Run:
```bash
uv run python tools/verify.py --ipa build/accept/bridge.ipa
```
Expected: 全部具名 check 通过，报告写入 `dist/verification.json` 并带上 `dev/acceptance.json` 的设备结论。

- [ ] **Step 5: 写执行记录并提交**

把与计划的每处偏差、上述四条命令的实际输出（哈希、check 数）追加到本计划的"执行记录"一节，然后：

```bash
git add docs/superpowers/plans/2026-09-25-public-patch-toolchain.md
git commit -m "docs: record the public toolchain acceptance

Co-authored-by: Codex <codex@openai.com>"
```

---

## Self-Review

**Spec coverage（逐条对照 Global Constraints）**

- 单条命令的公开流程 → Task 4/5。
- patch 阶段无 Xcode → Task 1（去 otool）+ Task 2（ABI 入 manifest），Task 8 Step 3 以 `DEVELOPER_DIR=/nonexistent` 证明。
- 版本按 sha256 匹配 → Task 4 的 `select_manifest` 与其测试。
- 拒绝加密输入 → Task 4 的 `_reject_encrypted` 与其测试。
- bridge 契约校验（15 导出/install_name/依赖/初始化）→ 既有 `verify.verify_bridge` 保留，Task 4 测试钉住失败路径。
- 失败不留半个 IPA → Task 4 先写临时文件再 `replace`，两个失败测试都断言输出不存在。
- 输出名含 libass 版本 → Task 4 的默认命名 + `libass_version` 字段（Task 2）。
- 单产物、删变体 → Task 3。
- 研发材料入 `dev/` → Task 6。
- README(EN)/MIT/THIRD-PARTY/无 CI → Task 7。
- 保留安全网（四态状态机、字节不变式、preflight、对照检查）→ 仅在 Task 3 删除 mode 分支，检查项本身不动。

**Placeholder scan:** 无 TBD/TODO；每个代码步都给了可执行代码或确切命令。唯一需要用户提供的是 `LICENSE` 的版权行与 README 里的仓库地址（Task 7 Step 2 已标注）。

**Type consistency:** `select_manifest` 返回 `(Manifest, Path)`（Task 4 正文说明），`verify_artifact` 去掉 `mode`，`package.publish(source, output, main, bridge)`，`PatchResult` 字段在 Task 4/5 与 Task 8 的命令输出中一致。

**Review Focus 覆盖:** 五条分别由 Task 4 的 `test_encrypted_input_is_reported_as_encrypted`、`test_unsupported_version_lists_the_supported_ones`、`test_invalid_bridge_is_rejected_by_name`、两个"输出不存在"断言、`_extract_main` 的成员检查覆盖。
