# 实时 Loopback AEC + ASR 实验

这个脚本用于测试：

```text
网易云音乐 + 随机项目 TTS + 你说话
    ↓
WASAPI loopback 抓系统播放声音
麦克风抓房间声音
    ↓
WebRTC AEC 实时压掉系统声音
    ↓
sherpa-onnx 实时识别清理后的声音
```

脚本：

```text
experiments\wasapi_loopback\realtime_loopback_barge_in_asr.py
```

## 使用步骤

先进入环境：

```powershell
cd D:\HST_WORK\py_project\aec_test
conda activate aec_webrtc_test_311
```

先打开网易云音乐并开始外放一首歌。

然后运行：

```powershell
python experiments\wasapi_loopback\realtime_loopback_barge_in_asr.py --seconds 30 --play-random-tts --enable-ns --raw-asr
```

脚本运行后你直接说话，看控制台里的：

```text
CLEAN_ASR
```

理想情况是 `CLEAN_ASR` 主要识别你的话，不识别网易云和项目 TTS。

如果开了 `--raw-asr`，还会看到：

```text
RAW_ASR
```

它是原始麦克风识别，用来对比没有 AEC 时会识别到多少系统声音。

## 关键参数

```powershell
--loopback-lag-ms 230
```

实时预对齐参数。离线测试发现 loopback 采集流常常比 mic 晚两百多毫秒，所以脚本默认把 mic 缓冲约 230ms，再和后到的 loopback 配对。

```powershell
--delay-ms 120
```

WebRTC AEC 的残余 delay 参数。离线自动对齐后，当前测试里 `120ms` 效果较好。

```powershell
--play-random-tts
```

随机播放 `TTS_module\voice\samples` 里的缓存 TTS。网易云音乐要你手动提前播放。

## 输出文件

默认输出到：

```text
outputs\wasapi_loopback_realtime_asr
```

包括：

```text
loopback_ref_realtime.wav
mic_aligned_realtime.wav
mic_clean_realtime.wav
mic_raw_input_realtime.wav
report.json
```

重点听：

```text
mic_clean_realtime.wav
```

如果效果不好，可以试：

```powershell
python experiments\wasapi_loopback\realtime_loopback_barge_in_asr.py --seconds 30 --play-random-tts --enable-ns --raw-asr --loopback-lag-ms 180 --delay-ms 120
python experiments\wasapi_loopback\realtime_loopback_barge_in_asr.py --seconds 30 --play-random-tts --enable-ns --raw-asr --loopback-lag-ms 230 --delay-ms 120
python experiments\wasapi_loopback\realtime_loopback_barge_in_asr.py --seconds 30 --play-random-tts --enable-ns --raw-asr --loopback-lag-ms 280 --delay-ms 120
```

这个实验脚本还不是产品级实时音频管线。它的重点是验证“系统播放混音作为 farend_ref 接进实时 ASR”这条链路。

