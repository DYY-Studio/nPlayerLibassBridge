# nPlayer FFmpeg 核心全量替换（Phase A：4.4.5 → 4.4.8，同 ABI）

> 状态：Step 0（版本定界）+ Step 1（面审计）+ A0（站点表闭合）已完成，本文件为**规格与实施计划**（plan is spec）。
> 上游记录：`/Volumes/990EP/Work/Mac/nPlayer_Backup/notes/ida-investigation.md` §7。
> 前置分支：`feat/multidomain-ffmpeg-bridge`（多单元 manifest / payload / 打包机制在这里落地，Phase A 依赖它）。

---

## 1. 目标与收益

把 nPlayer 3.13.0 内嵌的 FFmpeg **4.4.5** 整体替换为官方 **4.4.8**（同 ABI、同名 soname、同公开结构布局），
用既有的"多单元 dispatch + 首次调用仲裁 + 失败回落"机制接管 app 的全部 FFmpeg 调用。

- **收益**：获得 `n4.4.5...n4.4.8` 的 **448 commits / ≥300 files / ≥7702 行**修复（avformat 79、avcodec 97），
  且集中在 app 真正使用的路径上：`avformat/http`(redirect 协议校验/off-by-1)、`avformat/hls`(seg size/offset 溢出、
  `#EXTINF` 溢出)、`avformat/mpegts`、`avformat/dashdec`、`avformat/rtmpproto`、`avcodec/dca_xll`(DTS-HD)、
  `avcodec/mjpegdec`、`avcodec/qdm2` 等。
- **结构性收益**：这是把站点/结构面完整拿下的第一步；Phase B（跨 major 兼容运行时）**复用同一张站点表**，
  只换 shim 实现，因此 A 是 B 的低风险前置。

## 2. 约束（不变量）

1. **同 ABI 才允许增量与 per-domain 回退**；本阶段**不允许**任何跨 major 假设。
2. **`{libavutil, libavcodec, libavformat}` 是一个原子单元**：定义成**单个 domain**（`ffmpeg-core`），
   单一 state、单一回落开关。不允许半迁移（结构与分配横跨三库，app 按 ABI 直接读写）。
3. **站点表即规格**：§5 的站点集合必须逐条进入 manifest；`preflight` 在改写前逐条断言原始 BL 字面值。
4. 不引入新的 manifest/payload 概念（不新增 "group" 字段）；不新增 v1 兼容层。
5. Patch 阶段不依赖 Xcode/xcrun/otool；不发布 IPA、不提供解密。
6. 每个步骤一个 Commit，消息尾部附 `Co-authored-by: Codex <codex@openai.com>`。
7. 不修改 IDA 数据库；不覆盖既有分析记录。

## 3. 已确认事实

- 内嵌版本 **4.4.5**（字符串集双向包含定界，Confirmed）；app main 构建于 2025-10-11。
- **app 不直接调用 FFmpeg 内部函数**（`ff_*`/`avpriv_*`/`avcodec_default_*`）：26 个曾未命名目标 26/26 落在公共 API。
- **解复用 100% 走 FFmpeg**（`media::FFmpegDemuxer`）；视频解码 **VideoToolbox 优先**（FFmpeg 仅软件回退）；
  音频 AudioToolbox / 专用 `DTSAudioDecoder` 优先，FFmpeg 覆盖 FLAC/QDM2/Opus/Vorbis 等子集。
- **mux/录制/转码路径真实存在**：`avformat_alloc_output_context2`/`new_stream`/`init_output`/`write_header`/
  `av_write_frame`/`write_trailer`/`avio_*`/`av_bsf_*`/`avcodec_find_encoder`/`send_frame`/`receive_packet`/`av_guess_format`/`av_opt_set`。
- **不存在** `av_interleaved_write_frame` / `avformat_query_codec` / `av_packet_alloc`；app 不用
  `avformat_close_input` / `avio_context_free`（自己用 `avformat_free_context` + `avio_close`）。
- 回调面有界：自建 `AVIOContext` 仅 2 处特例（ASS 字幕 / `[Reference]` 流）；无 log 回调；未写 codec 回调字段。
- VideoToolbox hwaccel：app 不通过 FFmpeg hwaccel 使用 VT（Confirmed）。
- 本地源码缓存见 `notes/ida-investigation.md` §7.8（`build/deps/downloads/ffmpeg-4.4.*`、`build/ffmpeg-src/ffmpeg-4.4.5|4.4.8`）。

## 4. 设计裁定

| 议题 | 裁定 |
|---|---|
| 单元粒度 | `libavutil+libavcodec+libavformat` = 单个 domain `ffmpeg-core`（原子 + 单一回退）；`swscale`/`swresample` 保持既有独立 domain |
| dylib 形态 | **新建 `LibFFmpegCoreBridge.dylib`**，与已验收的 `LibFFmpegBridge.dylib`（sws/swr）并存，风险隔离 |
| enable-set | 必须镜像 app 的 4.4.5 构建（含 muxer/encoder/**BSF**/protocol/demuxer/decoder）；用"字符串集差分"驱动并作为验收项 |
| `--enable-videotoolbox` | 非功能必需，但保留（成本低、减小平行为差） |
| BSF | 必须包含 app 用到的（至少 h264/hevc 的 annexb 与 aac 的 adtstoasc 类，最终以字符串集差分为准） |
| Phase B | 后置；A 完成后用同一站点表把 shim 从"透传"改为"legacy↔modern 翻译" |

## 5. 冻结表（A0 产出，实施输入）

**口径**：app 侧 = 调用点所在函数 ∈ `[0x100960000, 0x100C00000)`（nPlayer media C++/ObjC 区；已用 FFmpeg 断言串验证该区无 FFmpeg 代码）。
站点地址 = **BL 指令地址**（小写十六进制，去掉前导 `0x`）。已剔除 libass `[0x100C0A500,0x100C37800)`、
curl `[0x100C65794,0x100CA2570)`、libssh2 `[0x100F10538,0x100F41CA8)`、mbedTLS `0x10100–0x10109`、dav1d `0x10006Dxxx`、boost `0x100D9xxxx`。

**规模**：约 **120 个唯一目标 / 约 485 个站点**；命名确定 ~103（86%）；未定名 9（见 §5.5）。

### 5.1 libavformat

| symbol | old_target | 全部 app 侧 BL 站点 | conf | 备注 |
|---|---|---|---|---|
| avformat_alloc_context | 1007DCCF8 | 100a3e390, 100ab4df4, 100aed780, 100aef684, 100b95010 | Confirmed | |
| avformat_open_input | 10081FADC | 100a3e900, 100ab451c, 100ab4e1c, 100aed878, 100aef778, 100b950f8 | Confirmed | |
| avformat_find_stream_info | 100824C8C | 100a3e910, 100ab4e2c, 100aed8e8, 100aef7a8, 100b95130 | Confirmed | |
| av_read_frame | 100821298 | 100a46964, 100ab4934, 100ab4fe0, 100aee510, 100aeec60, 100aefe28, 100b954fc, 100b9929c | Confirmed | |
| av_seek_frame | 10082357C | 100a46944, 100aefc4c, 100aefcb4, 100aefccc, 100b98c54, 100b98ca4, 100b98cbc | Confirmed | |
| av_find_input_format | 100755304 | 100ab4d74 | Confirmed | 字幕探测 |
| avformat_alloc_output_context2 | 1007BF294 | 100b30174, 100b98ee4 | Confirmed | mux |
| avformat_init_output | 1007BF3D8 | 100b99128 | Confirmed | **勘误：原记为 write_header** |
| avformat_write_header | 1007BFC54 | 100b30248, 100b99150 | Confirmed | mux |
| av_write_frame | 1007C06F8 | 100b304b0, 100b99bf8, 100b99eac, 100b9a65c, 100b9a700, 100b9a7d8 | Confirmed | 非交织 |
| av_write_trailer | 1007C0A74 | 100b303dc, 100b98e84 | Confirmed | mux |
| avformat_new_stream | 100827BE4 | 100b30180, 100b98f6c | Confirmed | mux |
| avformat_free_context | 10082020C | 100a415d4, 100b30404, 100b96ce4, 100b98e8c | Confirmed | 单参、载入值 |
| av_guess_format | 100754FCC | 100b98ec8 | Confirmed | mux |
| av_probe_input_buffer | 1007558B0 | 100ab4dec | Confirmed | deprecated 包装 |
| av_guess_codec(?) | 100823F10 | 100aee2bc, 100b99078, 100b9e5e4 | Probable | output codec id 查表 |
| avio_alloc_context | 10073AD30 | 100a3e388, 100ab4dcc, 100aed778, 100b30234, 100b95008 | Confirmed | 4 处传自定义 opaque |
| avio_open2 | 10073DD84 | 100b9924c, 100b9a648, 100b9a768 | Confirmed | |
| avio_read | 10073C2F0 | 100ab4b00 | Confirmed | |
| avio_write | 10073B03C | 100b9a68c, 100b9a808 | Confirmed | |
| avio_flush | 10073B1C4 | 100b304f8, 100b99138, 100b9a668, 100b9a7e4 | Confirmed | |
| avio_seek | 10073B228 | 100ab4b14 | Confirmed | |
| avio_size | 10073B834 | 100a46874, 100aefc18, 100b98c28 | Confirmed | |
| avio_close | 10073DE84 | 100b9a69c, 100b9a818 | Confirmed | |
| avio_closep | 10073D8CC | 100b96cdc, 100b9a70c | Probable | 传 `&field` |
| avio_open_dyn_buf | 10073E17C | 100aec678, 100b990dc, 100b991b4, 100b9a6a8, 100b9a824 | Confirmed | |
| avio_close_dyn_buf | 10073E3B0 | 100aec69c, 100b9a67c, 100b9a7f8 | Probable | |
| avio_wb16 / avio_wb32(?) | 10073B920 / 10073B9E0 | 100b9a780, 100b9a7ac, 100b9a790, 100b9a7a0, 100b9a7bc, 100b9a7cc | Hypothesis | 命名待确认，影响 mux 头写入 |

### 5.2 libavcodec

| symbol | old_target | 全部 app 侧 BL 站点 | conf | 备注 |
|---|---|---|---|---|
| avcodec_find_decoder | 100143A28 | 100a03d8c, 100a467b0, 100a80f8c, 100a898c4, 100b96078 | Confirmed | |
| avcodec_find_encoder | 100143988 | 100a46ac0, 100b9a2b0 | Confirmed | 录制/转码 |
| avcodec_alloc_context3 | 1004FEBAC | 100a03be8, 100a03cf8, 100a03d68, 100a467b8, 100a46acc, 100a80f9c, 100a898d4, 100a8a514, 100ab4740, 100b63ff4, 100b95d4c, 100b96088, 100b9a2bc | Confirmed | |
| avcodec_free_context | 1004FEC04 | 100a03b28, 100a03e38, 100a468ec, 100a46b70, 100a80ed8, 100a80f4c, 100a81078, 100a81104, 100a89770, 100a89880, 100a899d0, 100a8a684, 100ab4884, 100b300f0, 100b63e5c, 100b63f88, 100b95dc4, 100b96d18, 100b96d30, 100b98ce0, 100b9a36c | Confirmed | 传 `&field` |
| avcodec_open2 | 10016C0F8 | 100a03dc8, 100a467dc, 100a46b04, 100a81020, 100a89914, 100b960a4, 100b9a358 | Confirmed | |
| avcodec_close | 1010F3F88 | 100a468e0, 100b965a0, 100b96d10, 100b96d28, 100b98cd8 | Probable | deprecated public |
| avcodec_send_packet | 1001FF544 | 100a468a8, 100a46998, 100a81280, 100a812ac, 100a89bd8, 100a89c04, 100b99334 | Confirmed | |
| avcodec_receive_frame | 100200060 | 100a468b4, 100a469ac, 100a812f8, 100a89c8c, 100b9934c, 100b995f4 | Confirmed | |
| avcodec_flush_buffers | 10016CB40 | 100a815ac, 100a89ff4, 100b98cc8 | Confirmed | |
| avcodec_send_frame | 100240C84 | 100a46b24, 100b99578 | Confirmed | 编码 |
| avcodec_receive_packet | 10024125C | 100a46b30, 100b995a8 | Confirmed | 编码 |
| avcodec_fill_audio_frame | 1005E92C0 | 100b99568 | Confirmed | |
| avcodec_decode_subtitle2 | 100200368 | 100a04d14 | Probable | 字幕解码 |
| avcodec_get_name | 1005E95C4 | 100a3e9d0, 100aee290, 100b9e5c4 | Confirmed | |
| avcodec_descriptor_get | 1001D8B68 | 100a3e9bc, 100ab46b8, 100aee25c, 100b9e5ac | Confirmed | |
| avcodec_parameters_alloc | 1001D8C00 | 100a03d98, 100a80fa8, 100a898e0, 100a8a528, 100b63ffc | Confirmed | |
| avcodec_parameters_free | 1001D8C70 | 100a03db8, 100a80fcc, 100a89904, 100a8a54c, 100b64020 | Confirmed | |
| avcodec_parameters_copy | 1001D8CE8 | 100a8a538, 100aef15c, 100b9594c, 100b98fcc | Confirmed | |
| avcodec_parameters_from_context | 1001D8DE8 | 100a03da4, 100a80fb8, 100a898f0, 100b6400c, 100b98fb0 | Confirmed | |
| avcodec_parameters_to_context | 1001D8F78 | 100a03db0, 100a467c8, 100a80fc4, 100a898fc, 100a8a544, 100ab474c, 100b64018, 100b95d58, 100b96094 | Confirmed | |
| av_bsf_alloc | 10017C170 | 100aef144, 100b9593c | Confirmed | |
| av_bsf_init | 10017C248 | 100aef164, 100b95954 | Confirmed | |
| av_bsf_flush | 10017C3B8 | 100aefcf0 | Confirmed | |
| av_bsf_free | 10017C0D0 | 100aef9e4, 100b96cb8 | Confirmed | 传 `&field` |
| av_bsf_send_packet | 10017C3FC | 100aeff00, 100b999cc | Confirmed | |
| av_bsf_receive_packet | 10017C4BC | 100aeff14, 100b999e0 | Confirmed | |
| av_init_packet | 10016D6D8 | 100a04aec, 100a40bb4, 100a40fac, 100a46958, 100a46b10, 100a811cc, 100a89b50, 100ab4928, 100ab4fd4, 100aee4f4, 100aeec48, 100aeff08, 100af045c, 100b30458, 100b954ec, 100b99290, 100b99590, 100b99968, 100b99b48, 100b99d68 | Confirmed | 栈上 AVPacket |
| av_packet_unref | 10016D708 **和** 10016D7F0 | 100ab4908, 100ab4fcc, 100af043c + 100a40c5c, 100a41054, 100a4110c, 100a469c0, 100a46b50, 100ab4aa8, 100ab5148, 100aee8d8, 100aeef7c, 100aeff20, 100af04b8, 100af0524, 100b958c0, 100b99358, 100b999d4, 100b99b80, 100b99c00, 100b99c08, 100b99da0, 100b99eb4, 100b9a3e8 | Confirmed | 两副本，合并 23 站 |
| av_packet_free | 10016D744 | 100ab4ac0, 100ab5150, 100af052c | Confirmed | 传 `&field` |
| av_new_packet | 10016D87C | 100b99b5c, 100b99d7c | Confirmed | |
| av_packet_copy_props | 10016E010 | 100b99b68, 100b99d88 | Confirmed | |
| av_packet_ref | 10016E124 | 100a46b48, 100b99974 | Confirmed | |
| av_packet_move_ref | 10016E278 | 100aeff2c, 100b99b8c, 100b99dac | Confirmed | |
| av_packet_rescale_ts | 10016E430 | 100b99bc0, 100b99ea0 | Confirmed | 4 字段含 `convergence_duration` |
| avsubtitle_free(?) | 10016CC7C | 100a05788 | Hypothesis | 纯 free 例程 |

### 5.3 libavutil

| symbol | old_target | 全部 app 侧 BL 站点 | conf | 备注 |
|---|---|---|---|---|
| av_malloc | 10084E10C | 100a23458, 100a8a5b8, 100a8a650, 100aace60, 100ab4da8, 100aef038, 100aef0a0, 100aef0dc, 100b04214, 100b301c4, 100b6412c, 100b64b0c, 100b64b84, 100b80ce8, 100b958d4 | Confirmed | |
| av_mallocz | 10084E448 | 100aacb50 | Confirmed | |
| av_realloc | 10084E188 | 100b80e1c | Confirmed | |
| av_freep | 10084E2A4 | 100a415cc, 100a46b80, 100aacc48, 100ab4490, 100aee358, 100aee36c, 100aeeff8, 100aefa20, 100b303fc, 100b96c90, 100b96d08 | Confirmed | |
| av_dict_get | 100842BE0 | 100a3e9fc, 100ab4670, 100ab469c, 100aed1dc, 100aeddd0, 100aeddf0, 100aee130, 100aee164, 100aee1fc, 100aee234, 100aee3e4, 100aefbbc, 100af032c, 100b95f94, 100b95fe0, 100b9e600, 100b9e634 | Confirmed | |
| av_dict_set | 100842DA4 | 100a3e770, 100a3e78c, 100aed83c, 100aed858, 100aef728, 100aef744, 100b950a4, 100b950c0, 100b99100, 100b9911c | Confirmed | 4 参含 flags |
| av_dict_free | 1008431CC | 100a3f91c, 100aed884, 100aed910, 100aef790, 100aef7d4, 100b95114, 100b99140 | Confirmed | 传 `&field` |
| av_dict_copy | 100843238 | 100b98f2c, 100b99094 | Confirmed | 传 `&field` |
| av_frame_alloc | 1008479D8 | 100a40b38, 100a40e90, 100a467f8, 100a46a3c, 100a812e8, 100a817f8, 100a899c0, 100b960b8, 100b960e4 | Confirmed | |
| av_frame_free | 100847A7C | 100a40c8c, 100a412dc, 100a468d8, 100a46b88, 100a81328, 100a813a8, 100a818f8, 100a89768, 100b96d38, 100b96d40 | Confirmed | 传 `&field` |
| av_frame_unref | 100847AB4 | 100a40c74, 100a41124, 100a469e8, 100a818f0 | Confirmed | |
| av_frame_ref | 10084807C | 100a468cc, 100a469e0, 100a81804 | Confirmed | |
| av_image_alloc | 10084AA60 | 100a46a6c | Confirmed | |
| av_image_fill_arrays | 10084B408 | 100a05fd0, 100a064cc, 100a2347c, 100a237f4, 100ac5ccc | Confirmed | |
| av_image_get_buffer_size | 10084B5F0 | 100a05f38, 100a06488, 100a23450 | Confirmed | |
| av_image_copy | 10084B044 | 100a05fec, 100a8a41c | Confirmed | 7 参 |
| av_reduce | 100853718 | 100a81064, 100a81374, 100aee3b4 | Confirmed | 传 `&field,&field` |
| av_rescale_q | 10084D144 | 100b99408, 100b9affc | Confirmed | |
| av_log2 | 10084BC4C | 100a556ac | Confirmed | |
| av_get_bytes_per_sample | 100853E10 | 100a3541c, 100a3554c, 100a35618, 100a362c0, 100a363ec, 100a3650c, 100a36538, 100a365b8, 100a36ab4, 100a6301c, 100a63454, 100a635a4, 100a63878, 100a89df8, 100aee09c, 100aee0c0, 100b6448c, 100b644f4, 100b64d7c, 100b65124, 100b65184, 100b6544c | Confirmed | |
| av_sample_fmt_is_planar | 100853E3C | 100a36300, 100a36314, 100a363a0, 100a36a54, 100a6343c, 100a89d14, 100b0424c, 100b04350 | Confirmed | |
| av_samples_get_buffer_size | 100853E64 | 100a63414, 100a89cf0, 100b99428, 100b99548 | Confirmed | **勘误：原记为 av_samples_alloc** |
| av_get_channel_layout | 1001795C8 | 100aef138, 100b95930 | Confirmed | 37 项名表 strcmp |
| av_get_channel_layout_nb_channels | 1008416D0 | 100a353c0, 100a35b40, 100a62e28, 100a89cb0, 100b64a28, 100b9a330 | Confirmed | |
| av_get_default_channel_layout(?) | 100841718 | 100a63504, 100a636c4, 100abfb98, 100abfba8, 100abfbd0, 100abfc10, 100b64d40 | Probable | |
| av_opt_set | 10084F230 | 100b99f94, 100b99fcc, 100b99fe8 | Confirmed | varargs |
| av_gettime | 10085A718 | 100aa531c, 100aa53a8, 100aa53f8, 100aa5488 | Confirmed | |

### 5.4 swscale / swresample（已落地，此处仅并列）

| symbol | old_target | 全部 app 侧 BL 站点 |
|---|---|---|
| sws_alloc_context | 1008DDE60 | 100a31b18 |
| sws_getContext | 1008DDD8C | 100a23428, 100a46a90, 100a8a4a4 |
| sws_getCachedContext | 1008DE290 | 100a8a450 |
| sws_scale | 1008C6540 | 100a234ac, 100a46ab8, 100a8a470, 100a8a4c4 |
| sws_freeContext | 1008DE0CC | 100a2354c, 100a31df4, 100a46b78, 100a8a4d0 |
| sws_alloc_context | 1008DDE60 | 100a31b18 |
| swr_alloc | 10112C980 | 100abf9dc |
| swr_alloc_set_opts | 1008744BC | 100abfb58 |
| swr_init | 10112DC28 | 100abfc48 |
| swr_convert | 100874AA4 | 100abfc78 |
| swr_free | 10112DBE0 | 100abfa38, 100abfb34 |
| swr_set_matrix | 10086E348 | 100abfc34 |

> 注：`sws_alloc_context`(1 站) 是 A0 全量扫面发现的**真实缺口**（`media::VideoRendererImpl` 在 `this+0x98` 持有它，
> 而它会被已重定向的 `sws_getCachedContext`/`sws_scale`/`sws_freeContext` 处理 → 跨版本 `SwsContext` 混用）。
> **已在 `358d9be` 修复**：libswscale domain 现为 **5 API / 13 站点**，app 从不调用 `sws_init_context`，故闭环。
> 教训：任何"app 自己持有不透明上下文"的库，重定向集合必须包含该上下文的**全部 alloc/free/init API**。
> Phase A 开始前应对既有 domain 做一次"分配归属"复审（本项已完结：libass 与 libswresample 的 alloc/free 均已成对覆盖）。

### 5.5 未定名目标（9 项，Phase A 前必须定名或证明可忽略）

`0x10016CC7C`（疑 `avsubtitle_free`）、`0x1004B8FF8`、`0x10075C1F0`、`0x1007E45A4`、`0x1007E45B0`、
`0x100822094`（流选择/评分）、`0x10082295C`、`0x100827B08`、`0x100828BE8`、`0x10084C790`。
目前**无一**呈 `ff_*`/`avpriv_*` 行为，倾向公共 API，但需逐个落实（用本地 `build/ffmpeg-src/ffmpeg-4.4.5/`）。

## 6. 实施步骤（每步一个 Commit）

### A0（已完成）
站点表闭合 + 内部调用定性 → 本文件 §5。

### A1 `build: pin and cross-compile the FFmpeg 4.4.8 core closure`
- 新增 `deps/ffmpeg-core.lock.json`（`ffmpeg-4.4.8.tar.xz`，sha256 `c73848c4ae283d9eaee7be3b276affbc3543380483555500d0dd2c9b7e1c39c3`）
  与 `deps/build_ffmpeg_core.py`（复用 `deps/build_deps.py` 的 helper；产出 `libavformat/libavcodec/libavutil` 三个静态库）。
- **enable-set 镜像**：先用 `--disable-everything` + 显式 `--enable-*` 起步，再用"字符串集差分"
  （app main 的 FFmpeg 字符串集 vs 我们构建产物的字符串集）逐轮补齐，直到差集只含可解释项。
  至少覆盖：demuxer（matroska/mov/mpegts/avi/flv/ogg/rm/asf/nut/img2/pcm…）、protocol（http/https/hls/rtmp/rtsp/tcp/file…）、
  decoder（含 libdav1d 并保持 **decoder 选择顺序**与 app 一致）、parser、**muxer**、**encoder**、**BSF**（h264/hevc annexb、aac adtstoasc 等）。
- 验收：`make deps` 幂等；闭包文本列出预期的 3 个归档；`--enable-videotoolbox` 保留。
- 可能拆成 A1a（lock+脚本）与 A1b（enable-set 对齐 + 差分报告）。

### A2 `tooling: generate the legacy 4.4 ABI offset table from the official headers`
- 由 `build/ffmpeg-src/ffmpeg-4.4.5/` 头文件生成 `offsetof` 表（AVCodecContext/AVFrame/AVFormatContext/AVStream/
  AVCodecParameters/AVPacket），产出编译期断言头。
- 本步对 Phase A 只是"顺带"（同 ABI 不需要翻译），但它是 **Phase B** 的地基，且可用来复核 A0 的结构偏移结论。

### A3 `feat: add the ffmpeg-core unit (avutil+avcodec+avformat 4.4.8)`
- 新增 `bridge/npa_ffmpeg_core_bridge.c`（约 120 个 `npa_*` 透传 shim，导出表 `bridge/ffmpeg-core.exports`）。
- manifest 新增 dylib `ffmpeg-core`（basename `LibFFmpegCoreBridge.dylib`、`library_version 4.4.8`）与**单个 domain**
  `ffmpeg-core`，`apis[]` 即 §5.1–5.3 的行（symbol/call_sites/old_target）。
- 同时补齐 §5.4 注中的 `sws_alloc_context` 缺口。
- 验收：`make bridge verify` 三个 dylib 全过（导出集合/install name/依赖/无初始化器/无 host 路径）。
- 可能拆成 A3a（shim 源 + 导出表）与 A3b（manifest domain + 站点表）。

### A4 `test: verify the ffmpeg-core unit on a clean IPA`
- `verify_main.main.instructions` 必须断言**改动指令集恰好等于** `ffmpeg-core` domain 的全部站点（≈485）
  加所选 dylib 的 extra sites；`preflight` 逐条断言原始 BL 字面值。
- 记录 `packaged_main_sha256`、各 dylib sha；产出 `nPlayer_3.13.0-ffmpeg-core4.4.8.ipa`。

### A5 `test: record the device acceptance of the ffmpeg-core unit`
设备验收重点（iPhone SE 3 / iOS 17.7.2 / LiveContainer）：
1. 常见容器逐个打开：mkv/webm、mp4/mov、mpegts、avi、flv、ogg、rm、asf、nut；
2. **网络路径**：http(s) 直链、**HLS**（含 `#EXTINF` 与分段边界）、rtmp/rtsp（若可构造）；
3. **录制/mux 路径**：HLS 会话的 `init_output`/`write_header`/`av_write_frame`/`write_trailer`/BSF；
4. 音频：FLAC / QDM2 / Opus / Vorbis / WavPack（走 `FFmpegAudioDecoder`）；
5. 视频软件解码回退（强制"软件"设置 / Hi10P）与 DTS(DTS-HD)；
6. 连续播放、seek、以及任意失败时**整体回落到 4.4.5**（不得半迁移）。

### A6 `docs: document the ffmpeg-core unit and the 4.4 LTS tracking`
- README（输出名规则、`--dylib ffmpeg-core`、release asset 增加 `LibFFmpegCoreBridge.dylib`）、
  THIRD-PARTY（FFmpeg 4.4.8 许可）、`dev/README.md`（4.4 LTS 跟进流程 = 更新 lock → 重建 → 字符串集差分 → 对表）。

### Phase B（后置，不在本轮）
用 §5 同一张站点表，把 shim 从"透传"改为"legacy↔modern 翻译"；需要 §7 的未决项全部闭合。

## 7. 验收标准（Phase A 完成定义）

1. `make deps bridge verify test` 全绿；三个 dylib 的产物检查全过。
2. 干净 IPA 上改动指令集**精确**等于 §5 的站点集合（无多改、无漏改、无 NOP 误动）。
3. **enable-set 差分报告**：app 的 FFmpeg 字符串集与产物的差集为空或已逐条解释（尤其 decoder 选择顺序、
   muxer/encoder/BSF 齐备）。
4. 设备验收 A5 全部通过，且能证明失败时整体回落。
5. `dev/acceptance.json` 记录产物 sha、`packaged_main_sha256` 与设备结论。

## 8. 风险

| 风险 | 影响 | 处置 |
|---|---|---|
| 站点表漏项 → 新旧结构混用 | 崩溃/数据损坏 | A0 已全量扫；A4 断言"精确等于"；§5.5 未定名项先定名 |
| enable-set 缺 muxer/encoder/BSF | 录制/HLS 路径断 | A1b 字符串集差分 + A5 专项 |
| decoder 选择顺序变化（native av1 vs libdav1d） | 行为变化 | 差分对齐 + 逐 codec 校验 |
| 代码体积/内存增大（avcodec+avformat 远大于 sws/swr） | 启动/内存 | 作为已知代价记录；必要时后续按需裁剪 |
| 许可变化 | 合规 | THIRD-PARTY 更新；以差分确认实际启用项，不臆测 |
