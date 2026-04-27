# Windows WASAPI Loopback Experiment

这个实验目录用于尝试捕获“系统正在播放的所有声音”，把它作为更完整的 `farend_ref`，再送入 WebRTC AEC。

主项目里的 `farend_ref` 来自程序自己播放的 TTS；本实验里的 `farend_ref` 来自 Windows 系统回放混音，也就是其他播放器、浏览器、系统声音等最终送到同一个输出设备的声音。

如果你要做“网易云音乐 + 项目 TTS + 自己说话”的实际测试，直接看 `USAGE.md`。

如果你要测试实时 ASR 链路，看 `REALTIME_ASR.md`。

## 依赖

```powershell
conda activate aec_webrtc_test_311
python -m pip install -r requirements.txt
```

这里额外用到 `soundcard`，因为当前 `sounddevice` 版本没有暴露 WASAPI loopback 参数。

## 1. 查看 soundcard 识别到的设备

```powershell
python experiments\wasapi_loopback\list_soundcard_devices.py
```

重点看默认扬声器和默认麦克风。

## 2. 只录系统回放

先让浏览器、音乐播放器或任意程序播放声音，然后运行：

```powershell
python experiments\wasapi_loopback\record_loopback.py --seconds 10
```

输出：

```text
outputs\wasapi_loopback\loopback.wav
```

这个文件应该只包含“电脑正在播放出来的声音”，不应该包含你在房间里直接说话的声音。

也可以用项目自带 `data\test.wav` 自动播放并录 loopback：

```powershell
python experiments\wasapi_loopback\record_loopback.py --play-test-wav
```

## 3. 同时录 loopback 和麦克风

先让其他播放器播放声音，或者用 `--play-test-wav` 播放项目测试音：

```powershell
python experiments\wasapi_loopback\record_loopback_and_mic.py --seconds 10
```

输出：

```text
outputs\wasapi_loopback_pair\farend_loopback.wav
outputs\wasapi_loopback_pair\mic_recording.wav
```

再用自动对齐 + WebRTC AEC 测试：

```powershell
python experiments\wasapi_loopback\loopback_webrtc_aec.py --delay-ms 120 --enable-ns
```

输出：

```text
outputs\wasapi_loopback_aec\mic_aligned.wav
outputs\wasapi_loopback_aec\ref_aligned.wav
outputs\wasapi_loopback_aec\cleaned_webrtc_delay_120ms.wav
outputs\wasapi_loopback_aec\alignment_report.json
```

默认会先用互相关估计 loopback 和 mic 的启动偏移，再裁剪到同一时间轴。如果想关闭这一步，可以加 `--no-auto-align`。

## 重要提醒

Loopback 捕获的是“系统送往某个扬声器设备的所有声音”。它适合处理其他播放器、浏览器、系统通知这类电脑内部播放源。

它不能捕获现实房间里独立存在的声音，比如旁边手机外放、别人说话、空调声。这类声音没有数字参考信号，只能靠 NS、神经网络降噪、麦克风阵列等方案处理。
