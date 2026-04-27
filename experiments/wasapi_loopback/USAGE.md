# WASAPI Loopback 实验使用流程

这份文档只讲怎么实际操作。目标是测试：

```text
网易云音乐 + 项目 TTS + 你说话
```

然后看 WebRTC AEC 能不能把“电脑正在播放的声音”压下去，同时保留你对麦克风说的话。

## 先理解这次实验在录什么

录音时会同时保存两路声音：

```text
farend_loopback.wav = Windows 系统正在播放的声音
mic_recording.wav   = 麦克风听到的房间声音
```

如果你打开网易云音乐，并且脚本播放项目里的 `data\test.wav`，那么：

```text
farend_loopback.wav 应该包含：网易云音乐 + 项目 TTS
mic_recording.wav   应该包含：网易云音乐外放回声 + 项目 TTS 外放回声 + 你说话 + 房间噪声
```

WebRTC AEC 的目标是：

```text
用 farend_loopback.wav 作为参考，把 mic_recording.wav 里的网易云音乐和项目 TTS 尽量压掉。
```

最后你重点听：

```text
cleaned_webrtc_delay_120ms.wav
```

理想效果是：网易云和 TTS 变小，你说话还在。

## 第 0 步：进入项目和环境

打开 PowerShell：

```powershell
cd D:\HST_WORK\py_project\aec_test
conda activate aec_webrtc_test_311
```

## 第 1 步：确认设备

先跑：

```powershell
python experiments\wasapi_loopback\list_soundcard_devices.py
```

你会看到类似：

```text
Default speaker:
  扬声器 (Realtek(R) Audio)

Default microphone:
  麦克风 (Realtek(R) Audio)

All microphones:
  <Loopback 扬声器 (Realtek(R) Audio)>
  <Microphone 麦克风 (Realtek(R) Audio)>
```

确认两件事：

1. 默认 speaker 是你现在真正外放网易云的设备。
2. 默认 microphone 是你要对着说话的麦克风。

如果这里不对，先在 Windows 声音设置里切默认输入/输出设备。

## 第 2 步：打开网易云音乐

现在打开网易云音乐，开始播放一首歌。

建议：

- 用外放，不要戴耳机。
- 音量用正常测试音量，不要太大。
- 先让音乐持续播放，不要暂停。

这一步很重要，因为 loopback 捕获的是“系统正在播放到扬声器的声音”。如果你没有播放网易云，`farend_loopback.wav` 里就不会有网易云。

## 第 3 步：开始录音

网易云已经在播放后，回到 PowerShell，运行：

```powershell
python experiments\wasapi_loopback\record_loopback_and_mic.py --play-test-wav --seconds 15 --out-dir outputs\wasapi_loopback_music_tts_test
```

这个脚本会做三件事：

1. 录 Windows 系统回放，也就是网易云音乐 + 项目 TTS。
2. 同时录麦克风，也就是房间里真实听到的声音。
3. 因为加了 `--play-test-wav`，脚本还会自动播放项目里的 `data\test.wav`。

所以这 15 秒里，系统里会同时有：

```text
网易云音乐：你手动提前播放
项目 TTS：脚本自动播放 data\test.wav
你的说话：你自己对麦克风说
```

## 第 4 步：什么时候说话

运行上面的录音命令后，按这个节奏说话：

```text
0-2 秒：不要说话，让网易云和 TTS 单独录进去。
3-8 秒：开始正常说话，比如“你好，我现在正在测试插话识别。”
9-13 秒：可以再说一句，比如“这句话应该被保留下来。”
14-15 秒：停一下，等录音结束。
```

为什么前 2 秒不要说话？

因为这样后面更容易判断：

- 原始麦克风里有没有音乐/TTS 回声
- AEC 后音乐/TTS 有没有被压下去
- 你说话有没有被保留

## 第 5 步：录音结束后会生成什么

录完后会生成：

```text
outputs\wasapi_loopback_music_tts_test\farend_loopback.wav
outputs\wasapi_loopback_music_tts_test\mic_recording.wav
```

你可以先试听：

```text
mic_recording.wav
```

它应该包含网易云、项目 TTS、你说话。

再试听：

```text
farend_loopback.wav
```

它应该包含网易云和项目 TTS，但不应该包含你直接对麦克风说的话。

如果 `farend_loopback.wav` 里没有网易云，说明网易云没有走当前默认扬声器，或者默认输出设备不对。

## 第 6 步：自动对齐 + WebRTC AEC

录完后运行：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_tts_test\mic_recording.wav --ref outputs\wasapi_loopback_music_tts_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_tts_aec --delay-ms 120 --enable-ns
```

这个脚本会做五件事：

1. 读取麦克风录音 `mic_recording.wav`。
2. 读取系统回放录音 `farend_loopback.wav`。
3. 自动估计这两路录音的时间偏移，并裁剪对齐。
4. 把对齐后的 loopback 当成 WebRTC AEC 的 `farend_ref`。
5. 输出去回声后的音频。

为什么要自动对齐？

因为 loopback 和 mic 是两个独立录音流，它们不一定同一毫秒开始录。脚本会用互相关估计偏移，比如：

```text
Estimated loopback/mic lag: -3719 samples = -232.4 ms
```

然后自动裁剪，让两路音频尽量站到同一条时间线上。

## 第 7 步：处理后会生成什么

处理完会生成：

```text
outputs\wasapi_loopback_music_tts_aec\mic_aligned.wav
outputs\wasapi_loopback_music_tts_aec\ref_aligned.wav
outputs\wasapi_loopback_music_tts_aec\cleaned_webrtc_delay_120ms.wav
outputs\wasapi_loopback_music_tts_aec\alignment_report.json
```

含义：

```text
mic_aligned.wav
```

自动对齐后的麦克风音频。

```text
ref_aligned.wav
```

自动对齐后的系统回放参考音频。

```text
cleaned_webrtc_delay_120ms.wav
```

WebRTC AEC 处理后的结果，重点听这个。

```text
alignment_report.json
```

对齐和处理报告，里面有估计偏移、RMS 降幅等指标。

## 第 8 步：怎么判断效果

建议按这个顺序听：

1. `outputs\wasapi_loopback_music_tts_test\mic_recording.wav`
2. `outputs\wasapi_loopback_music_tts_test\farend_loopback.wav`
3. `outputs\wasapi_loopback_music_tts_aec\cleaned_webrtc_delay_120ms.wav`

你要听三个问题：

### 1. 网易云有没有进 farend_loopback

如果 `farend_loopback.wav` 里能听到网易云，说明 loopback 捕获成功。

### 2. cleaned 里网易云和 TTS 有没有变小

如果 `cleaned_webrtc_delay_120ms.wav` 里音乐和 TTS 明显比 `mic_recording.wav` 小，说明 AEC 有效果。

### 3. 你的说话有没有保留

理想情况是：

```text
网易云/TTS 变小，但你说话仍然清楚。
```

如果你说话也被压得很厉害，可能是因为你说话时和音乐/TTS 频段重叠太多，或者 AEC/NS 过强。

## 第 9 步：如果 120ms 效果不好怎么办

先保留同一组录音，不要重新录，直接多试几个 delay：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_tts_test\mic_recording.wav --ref outputs\wasapi_loopback_music_tts_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_tts_aec_0 --delay-ms 0 --enable-ns

python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_tts_test\mic_recording.wav --ref outputs\wasapi_loopback_music_tts_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_tts_aec_40 --delay-ms 40 --enable-ns

python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_tts_test\mic_recording.wav --ref outputs\wasapi_loopback_music_tts_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_tts_aec_120 --delay-ms 120 --enable-ns

python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_tts_test\mic_recording.wav --ref outputs\wasapi_loopback_music_tts_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_tts_aec_240 --delay-ms 240 --enable-ns
```

分别听：

```text
outputs\wasapi_loopback_music_tts_aec_0\cleaned_webrtc_delay_0ms.wav
outputs\wasapi_loopback_music_tts_aec_40\cleaned_webrtc_delay_40ms.wav
outputs\wasapi_loopback_music_tts_aec_120\cleaned_webrtc_delay_120ms.wav
outputs\wasapi_loopback_music_tts_aec_240\cleaned_webrtc_delay_240ms.wav
```

哪个音乐/TTS 残留最小，同时你说话还自然，就选哪个。

## 第 10 步：推荐的一键流程

每次测试可以按这个顺序：

```powershell
cd D:\HST_WORK\py_project\aec_test
conda activate aec_webrtc_test_311

python experiments\wasapi_loopback\list_soundcard_devices.py
```

然后手动打开网易云并开始播放。

接着录音：

```powershell
python experiments\wasapi_loopback\record_loopback_and_mic.py --play-test-wav --seconds 15 --out-dir outputs\wasapi_loopback_music_tts_test
```

录音时按节奏说话。

最后处理：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_tts_test\mic_recording.wav --ref outputs\wasapi_loopback_music_tts_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_tts_aec --delay-ms 120 --enable-ns
```

听：

```text
outputs\wasapi_loopback_music_tts_aec\cleaned_webrtc_delay_120ms.wav
```

## 常见问题

### 为什么我说话不要太早

因为自动对齐主要依赖系统播放声音和麦克风里回声的相似性。如果开头全是你说话，相关性会变差。

### 为什么 farend_loopback 里没有我的说话

这是正常的。loopback 录的是电脑内部播放到扬声器的声音，不录房间里直接进入麦克风的声音。

### 如果 farend_loopback 里有我的说话呢

可能是你开了某个监听/返听功能，或者麦克风声音被某个软件播放回系统输出了。这会让 AEC 把你的声音也当成要消掉的参考，测试会变复杂。

### 为什么要加 `--play-test-wav`

这是为了同时测试项目 TTS。网易云是你手动播放的系统声音，`data\test.wav` 是脚本自动播放的项目 TTS。两者都会进入 loopback。

### 能不能只测网易云，不播放项目 TTS

可以。录音时去掉 `--play-test-wav`：

```powershell
python experiments\wasapi_loopback\record_loopback_and_mic.py --seconds 15 --out-dir outputs\wasapi_loopback_music_only_test
```

然后处理：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_music_only_test\mic_recording.wav --ref outputs\wasapi_loopback_music_only_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_music_only_aec --delay-ms 120 --enable-ns
```

