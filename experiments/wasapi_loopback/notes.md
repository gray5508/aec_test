# WASAPI Loopback 实验记录

## 这次实验做了什么

目标是验证这个方案：

```text
Windows 系统正在播放的所有声音 -> WASAPI loopback -> farend_ref
麦克风真实录音                 -> mic
farend_ref + mic              -> WebRTC AEC
```

这和主流程的区别是：

```text
主流程：farend_ref 只包含本程序自己播放的 TTS。
loopback 实验：farend_ref 包含系统送往默认扬声器的混音。
```

所以 loopback 方案理论上可以覆盖：

- 浏览器播放的视频声音
- 音乐播放器
- 其他程序播放的语音
- 系统提示音
- 本项目自己播放的 TTS

但它仍然不能覆盖电脑之外的声音，比如手机外放、旁边人说话、空调声，因为这些声音没有进入 Windows 播放混音。

## 当前实现文件

- `list_soundcard_devices.py`：列出 `soundcard` 看到的扬声器、麦克风和 loopback 设备。
- `record_loopback.py`：只录系统回放。
- `record_loopback_and_mic.py`：同时录系统回放和房间麦克风。
- `loopback_webrtc_aec.py`：用 loopback 作为 `farend_ref` 跑 WebRTC AEC。

## 为什么不用 sounddevice 直接做 loopback

当前环境里的 `sounddevice` 是 `0.5.5`，它有 `WasapiSettings`，但没有暴露 `loopback=True` 这种参数。源码里也没有 `loopback` 相关实现。

所以实验里用了 `soundcard`：

```python
import soundcard as sc

speaker = sc.default_speaker()
loopback_mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
```

`soundcard` 会把 Windows WASAPI loopback 暴露成一个“特殊麦克风”，也就是：

```text
录这个 loopback microphone = 录默认扬声器正在播放的系统混音
```

## 当前电脑上的设备

实测 `soundcard` 识别到：

```text
Default speaker:
  扬声器 (Realtek(R) Audio)

Default microphone:
  麦克风 (Realtek(R) Audio)

Loopback:
  KX32 (NVIDIA High Definition Audio)
  扬声器 (Realtek(R) Audio)
```

## 实测结果

先用项目自带 `data\test.wav` 自动播放，同时录 loopback：

```powershell
python experiments\wasapi_loopback\record_loopback.py --play-test-wav --seconds 10 --out outputs\wasapi_loopback\loopback_test.wav
```

录到的 loopback 文件有明显能量：

```text
sr: 48000
duration: 10.0s
peak: 0.4076
rms: 0.0613
```

然后同时录 loopback 和麦克风：

```powershell
python experiments\wasapi_loopback\record_loopback_and_mic.py --play-test-wav --seconds 10 --out-dir outputs\wasapi_loopback_pair_test
```

输出：

```text
outputs\wasapi_loopback_pair_test\farend_loopback.wav
outputs\wasapi_loopback_pair_test\mic_recording.wav
```

再跑 WebRTC AEC：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --mic outputs\wasapi_loopback_pair_test\mic_recording.wav --ref outputs\wasapi_loopback_pair_test\farend_loopback.wav --out-dir outputs\wasapi_loopback_aec_sweep --delay-ms 480 --enable-ns
```

早期未做自动对齐时，某次录音里 `480ms` 比 `260ms` 更好：

```text
Delay 260ms: RMS reduction about 7.0 dB
Delay 480ms: RMS reduction about 8.0 dB
```

这个结果说明 loopback 方案是可行的，但同步比主流程更难。主流程里 `farend_ref` 和播放 callback 在同一个音频流里生成；loopback 实验里，loopback recorder 和 mic recorder 是两个采集流，启动时间、系统缓冲、采集块抖动都会影响对齐。

后续脚本加入了自动预对齐：先用互相关估计 `loopback` 和 `mic` 的相对偏移，再裁剪到同一时间轴。修正后，同一组测试录音估计到：

```text
estimated lag: -3719 samples = -232.4 ms
correlation score: -0.464
```

这里负号表示 loopback 相对 mic 更晚，所以脚本会裁掉 loopback 前面的对应部分。自动对齐后再扫 WebRTC 残余 `delay-ms`，当前这组录音里 `120ms` 最好：

```text
Delay 0ms:   RMS reduction about 9.4 dB
Delay 20ms:  RMS reduction about 9.4 dB
Delay 40ms:  RMS reduction about 9.2 dB
Delay 80ms:  RMS reduction about 8.4 dB
Delay 120ms: RMS reduction about 10.4 dB
Delay 240ms: RMS reduction about 8.8 dB
```

因此当前实验脚本默认推荐：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --delay-ms 120 --enable-ns
```

## 看到 discontinuity warning 是什么

实验时 `soundcard` 偶尔输出：

```text
SoundcardRuntimeWarning: data discontinuity in recording
```

这表示底层音频采集流出现了小的时间不连续，可能是缓冲、调度或设备时钟导致的。概念验证可以接受，但如果要做稳定实时产品，需要：

- 用更稳定的音频后端
- 明确控制 block size
- 给 loopback 和 mic 做时间戳对齐
- 做动态延时估计
- 必要时丢帧/补帧以维持同步

## Linux 上可行吗

可行，但取决于 Linux 使用的音频系统。

### PulseAudio

PulseAudio 通常会为每个输出设备提供一个 monitor source，比如：

```text
alsa_output.xxx.monitor
```

录这个 monitor source，就相当于录系统回放混音。

概念上对应：

```text
PulseAudio monitor source -> farend_ref
microphone                 -> mic
```

### PipeWire

现在很多新 Linux 桌面默认是 PipeWire。PipeWire 也能做 monitor/capture，而且路由能力更强。用 `pw-loopback`、`pw-record`、`pactl` 或 Python 绑定都可以探索。

### ALSA

纯 ALSA 也能做，但通常更麻烦。可能需要 `snd-aloop` 虚拟声卡，把系统播放路由到虚拟设备，再从虚拟设备采集。

## Windows 和 Linux 哪个更好做

如果是你当前这台 Windows 电脑，最快验证是 Windows 更好做：

```text
soundcard + WASAPI loopback
```

优点：

- 不需要改系统音频路由
- Python 里能直接拿默认扬声器 loopback
- 很适合快速验证想法

缺点：

- loopback 和 mic 是两个采集流，同步要额外处理
- Windows 音频设备/驱动差异比较大
- 做产品级实时稳定性需要更多缓冲和时间戳设计

Linux 在工程控制上更灵活，尤其是 PipeWire：

优点：

- 音频路由更透明
- 更容易构建虚拟音频管线
- 服务端/嵌入式场景更适合长期运行

缺点：

- 发行版差异大
- PulseAudio/PipeWire/ALSA 路径不同
- 初次配置比 Windows 验证更折腾

简单判断：

```text
快速原型：Windows + soundcard 更省事。
长期可控音频管线：Linux + PipeWire 更有潜力。
```

## 下一步建议

1. 先用 `record_loopback.py` 播放浏览器或音乐，确认 loopback 能录到其他播放器。
2. 用 `record_loopback_and_mic.py` 同时录 loopback 和 mic。
3. 对 `delay-ms` 做更细的 sweep，比如 `360, 420, 480, 540, 600`。
4. 如果效果稳定，再考虑做实时版：loopback stream + mic stream + WebRTC AEC + ASR。
5. 实时版最好加入动态延时估计，否则换播放器/设备后参数会漂。
