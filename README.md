# AEC Echo Cancel Test

这个小项目用于验证一个最小可行方案：

1. 从内存播放已知 TTS wav，也就是 `data/test.wav`。
2. 同时录麦克风，得到 `outputs/mic_recording.wav`。
3. 把“实际送到声卡的 TTS 参考信号”保存成 `outputs/farend_ref.wav`。
4. 离线估计扬声器到麦克风的延迟和回声路径，用 NLMS 自适应滤波从麦克风录音里扣掉 TTS 回声。

## 环境

```powershell
cd E:\project\aec_echo_cancel_test
& C:\Users\86158\miniconda3\Scripts\conda.exe env create -f environment.yml
& C:\Users\86158\miniconda3\Scripts\conda.exe activate aec_echo_cancel_test
```

如果环境已经创建过：

```powershell
& C:\Users\86158\miniconda3\Scripts\conda.exe activate aec_echo_cancel_test
```

WebRTC AEC 测试需要 Python 3.11 环境：

```powershell
& C:\Users\86158\miniconda3\Scripts\conda.exe env create -f environment_webrtc.yml
& C:\Users\86158\miniconda3\Scripts\conda.exe activate aec_webrtc_test_311
```

## 1. 查看音频设备

```powershell
python src\list_devices.py
```

记下输入麦克风和输出扬声器的 device id。

## 2. 播放并录音

先用默认设备：

```powershell
python src\play_record.py
```

指定设备：

```powershell
python src\play_record.py --input-device 1 --output-device 3
```

录音时建议：

- 扬声器正常外放 `test.wav`。
- 你在播放期间说话。
- 麦克风和扬声器保持你真实项目里类似的位置和音量。
- 先关掉系统“降噪/回声消除/音效增强”，否则测试结果不稳定。

## 3. 离线回声消除

```powershell
python src\aec_offline.py
```

输出文件：

- `outputs/cleaned.wav`：回声消除后的麦克风信号。
- `outputs/echo_estimate.wav`：算法估计出来的 TTS 回声。
- `outputs/diagnostic.png`：能量曲线和频谱对比图，需要运行时加 `--plot`。

可调参数示例：

```powershell
python src\aec_offline.py --filter-ms 80 --mu 0.0002 --max-delay-ms 500
```

如果 `cleaned.wav` 比 `mic_recording.wav` 大很多，通常是自适应滤波发散。先试：

```powershell
python src\aec_offline.py --mu 0.00005 --filter-ms 60
```

如果 `mic_recording.wav` 声音本身非常小，先提高麦克风输入音量或播放音量。回声消除需要麦克风里确实录到可测的 TTS 回声；太小的话算法只能在底噪里估计，会很容易误判。

## 4. 纯 TTS 标定拟合

如果录音里没有你说话，只有 TTS 外放被麦克风录进去，优先跑这个更强的离线拟合：

```powershell
python src\fit_fir_offline.py
```

输出：

- `outputs/cleaned_fir.wav`：FIR 拟合后扣掉回声的结果。
- `outputs/echo_estimate_fir.wav`：FIR 估计出来的回声。
- `outputs/echo_path_fir.npy`：估计出来的扬声器到麦克风回声路径。

这个脚本利用整段纯 TTS 录音来拟合固定回声路径，适合验证“这套设备/摆位能不能从参考音频里还原麦克风回声”。但它不是实时方案；实时方案仍然需要分块自适应滤波或 WebRTC/SpeexDSP AEC。

## 5. 扫频标定后消 TTS

生成扫频标定音：

```powershell
python src\make_chirp.py
```

播放扫频并录麦克风：

```powershell
python src\play_record.py --wav data\calib_chirp.wav --out-dir outputs\calib_chirp --input-device 1 --output-device 4 --volume 1.0
```

用扫频录音拟合回声路径，然后应用到上一条 TTS 录音：

```powershell
python src\apply_calibrated_fir.py
```

输出在 `outputs/calibrated_tts`。

## 6. WebRTC AEC 离线测试

```powershell
& C:\Users\86158\miniconda3\Scripts\conda.exe activate aec_webrtc_test_311
python src\webrtc_aec_offline.py --delay-ms 240
```

## 7. Barge-in ASR 实时测试

这个测试会随机播放 `TTS_module/voice/samples` 里的 TTS wav，同时把播放帧作为 far-end reference 喂给 WebRTC AEC。麦克风经 AEC 后实时进入 sherpa-mini VAD+ASR。

```powershell
& C:\Users\86158\miniconda3\Scripts\conda.exe activate aec_webrtc_test_311
python src\barge_in_aec_asr_test.py --input-device 1 --output-device 4 --delay-ms 240 --enable-ns --raw-asr
```

测试时在机器人播放期间直接说话，观察控制台：

- `CLEAN_ASR`：AEC 后的识别，理想情况下不识别机器人自己的 TTS，只识别你的插话。
- `RAW_ASR`：原始麦克风识别，对比用，通常更容易识别到机器人自己的 TTS。

输出音频和识别日志会保存在 `outputs/barge_in_aec_asr`。

可以试不同延迟：

```powershell
python src\webrtc_aec_offline.py --delay-ms 0
python src\webrtc_aec_offline.py --delay-ms 120
python src\webrtc_aec_offline.py --delay-ms 240
```

如果扫频太刺耳，可以换成柔和宽频噪声：

```powershell
python src\make_soft_noise.py
python src\play_record.py --wav data\calib_soft_noise.wav --out-dir outputs\calib_soft_noise --input-device 1 --output-device 4 --volume 1.0
python src\apply_calibrated_fir.py --calib-mic outputs\calib_soft_noise\mic_recording.wav --calib-ref outputs\calib_soft_noise\farend_ref.wav
```

## 判断可行性的关键点

这件事总体是可行的，但不能只“把原 wav 反相相加”。扬声器、房间、麦克风会把 TTS 变成延迟、混响、频响变化、非线性失真的版本，所以需要一个回声路径模型。

本项目先用 NLMS 做离线验证。它适合回答第一个问题：已知 TTS 参考音频时，能否从麦克风录音里明显压低 TTS 成分，同时保留人声。如果这里有效，再迁移到实时项目里，可以考虑 WebRTC AEC、SpeexDSP AEC 或分块频域自适应滤波。

## 文件说明

- `data/test.wav`：你的测试 TTS 音频副本。
- `src/list_devices.py`：列出输入/输出设备。
- `src/play_record.py`：全双工播放和录音。
- `src/aec_offline.py`：离线自适应回声消除。
- `src/fit_fir_offline.py`：用纯 TTS 录音拟合固定 FIR 回声路径。
- `src/make_chirp.py`：生成扫频标定音。
- `src/make_soft_noise.py`：生成更柔和的宽频标定音。
- `src/apply_calibrated_fir.py`：用扫频标定结果消 TTS。
- `src/webrtc_aec_offline.py`：WebRTC Audio Processing AEC 离线测试。
- `src/barge_in_aec_asr_test.py`：随机 TTS 播放 + WebRTC AEC + sherpa-mini ASR 实时插话测试。
- `src/synthetic_check.py`：无需设备的合成数据自测。
- `src/audio_utils.py`：音频读写、重采样、归一化工具。

## 快速自测

不播放声音，只验证核心滤波代码：

```powershell
python src\synthetic_check.py
```
