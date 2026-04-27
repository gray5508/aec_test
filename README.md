# WebRTC AEC Barge-in Test

这个项目现在只保留 WebRTC AEC 方案：播放本地 TTS 音频，同时录麦克风，把播放信号作为 far-end reference 喂给 WebRTC Audio Processing，得到去回声后的麦克风音频，再送入 sherpa-onnx 做实时 VAD/ASR。

小白教学文档在 `docs\webrtc_aec_tutorial.md`，里面结合代码解释了 `--delay-ms 260`、离线测试和实时插话的完整流程。继续深入的问答记录在 `docs\webrtc_aec_qa.md`。

系统回放捕获实验在 `experiments\wasapi_loopback`，用于尝试 Windows WASAPI loopback，把其他播放器/浏览器/系统声音作为更完整的 `farend_ref`。

## 环境

```powershell
cd D:\HST_WORK\py_project\aec_test
conda env create -f environment_webrtc.yml
conda activate aec_webrtc_test_311
```

如果环境已经建好：

```powershell
conda activate aec_webrtc_test_311
python -m pip install -r requirements.txt
```

## 1. 查看音频设备

```powershell
python src\list_devices.py
```

如果不传设备编号，脚本会使用系统默认输入和输出设备。换电脑时建议先看一眼默认设备是否符合预期。

## 2. 用 data\test.wav 录一段默认设备测试音频

```powershell
python src\play_record.py
```

输出：

- `outputs\mic_recording.wav`：麦克风真实录音，包含扬声器回放漏进麦克风的声音。
- `outputs\farend_ref.wav`：实际送到扬声器的参考信号。
- `outputs\recording_metadata.json`：录音参数。

也可以指定设备：

```powershell
python src\play_record.py --input-device 1 --output-device 3
```

## 3. 离线扫 WebRTC AEC 延时

WebRTC AEC 需要一个 `delay-ms`，它不是自动动态计算出来的。换电脑、声卡、蓝牙/USB 设备、驱动缓冲区后都建议重新试。

```powershell
python src\webrtc_aec_offline.py --delay-ms 240 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 260 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 300 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 320 --enable-ns
```

输出在 `outputs\webrtc_aec`。优先听 `cleaned_webrtc_delay_xxxms.wav`，选择 TTS 残留最小、人声损伤可接受的值。

## 4. 实时插话测试

```powershell
python src\barge_in_aec_asr_test.py --delay-ms 260 --enable-ns --raw-asr
```

指定设备时：

```powershell
python src\barge_in_aec_asr_test.py --input-device 1 --output-device 3 --delay-ms 260 --enable-ns --raw-asr
```

观察控制台：

- `CLEAN_ASR`：WebRTC AEC 后的识别结果，理想情况下只识别人的插话，不识别机器人的 TTS。
- `RAW_ASR`：原始麦克风识别结果，用来对比没有 AEC 时会识别到多少 TTS。

输出在 `outputs\barge_in_aec_asr`：

- `farend_ref.wav`
- `mic_raw.wav`
- `mic_clean_webrtc.wav`
- `report.json`

## 文件说明

- `src\list_devices.py`：列出系统音频输入/输出设备。
- `src\play_record.py`：播放 `data\test.wav` 并同步录麦克风。
- `src\webrtc_aec_offline.py`：用录好的 `mic_recording.wav` 和 `farend_ref.wav` 离线测试 WebRTC AEC。
- `src\barge_in_aec_asr_test.py`：随机播放 TTS 样本，实时 WebRTC AEC，再接 sherpa-onnx ASR。
- `src\audio_utils.py`：音频读写、重采样、归一化、RMS 统计工具。
