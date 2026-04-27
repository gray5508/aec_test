# WebRTC AEC 小白教学

这份文档用这个项目里的代码解释一件事：机器人一边外放 TTS，一边听人说话时，怎么尽量不把自己的 TTS 识别成用户说话。

如果你已经读完本文，并对 `260ms`、峰值归一化、AEC/NS 原理、10ms 分帧、系统 loopback 等问题有疑问，可以继续看 `docs\webrtc_aec_qa.md`。

## 1. 先理解问题

电脑播放 TTS 时，声音会从扬声器出来，再被麦克风录进去。麦克风里实际包含三类声音：

```text
麦克风信号 = 用户说话 + 扬声器漏回来的 TTS + 环境噪声
```

ASR 如果直接听麦克风，就可能把机器人的 TTS 也识别出来。我们想要的是：

```text
去回声后的麦克风 = 用户说话 + 少量环境噪声
```

这个过程叫 AEC，Acoustic Echo Cancellation，声学回声消除。

## 2. WebRTC AEC 需要哪两路音频

WebRTC AEC 不是只看麦克风。它需要两路信号：

- `farend_ref`：远端参考信号，也就是我们准备播放给扬声器的 TTS 数字音频。
- `mic`：麦克风真实录到的音频。

项目里的对应文件是：

- `outputs\farend_ref.wav`
- `outputs\mic_recording.wav`

直觉上可以这样理解：

```text
farend_ref: 我知道自己正在播放什么
mic:        我听到房间里有什么
WebRTC AEC: 从 mic 里找出 farend_ref 经过扬声器/空气/房间/麦克风后形成的回声，并压掉它
```

注意，它不是简单地把 `farend_ref` 反相相加。因为声音从扬声器到麦克风会发生变化：有延时、有混响、有频响变化、有硬件缓冲、有非线性失真。

## 3. `--delay-ms 260` 是什么

`260` 是传给 WebRTC AEC 的系统延时，单位是毫秒。代码里对应这一行：

```python
processor.set_stream_delay(args.delay_ms)
```

位置：

```text
src\barge_in_aec_asr_test.py
src\webrtc_aec_offline.py
```

它的含义是：

```text
从程序把 TTS 音频送到输出设备开始，到这段声音真的被麦克风录到，中间大概过了多少毫秒
```

这个延时包括：

- Python/sounddevice 音频缓冲
- Windows 音频系统缓冲
- 声卡/驱动缓冲
- 扬声器发声到麦克风采集的物理传播时间
- 麦克风输入缓冲

所以它不是“降噪强度”，也不是“识别等待时间”。它是告诉 WebRTC：参考信号和麦克风信号大概应该怎么对齐。

为什么你这台机器推荐先用 `260`？

因为用 `data\test.wav` 实测扫参时，`260 ms` 的 WebRTC 输出残留能量最低：

```text
Mic RMS:     -23.4 dBFS
Cleaned RMS: -40.5 dBFS
Reduction:   17.1 dB
```

这个结果只代表当前电脑、当前默认麦克风、当前默认扬声器、当前音频驱动设置。换蓝牙耳机、USB 声卡、HDMI 输出、系统默认设备，都建议重新扫。

## 4. 离线测试流程

离线测试分两步。

第一步，播放 `data\test.wav` 并录麦克风：

```powershell
python src\play_record.py
```

关键代码在 `src\play_record.py`：

```python
tts, sr = read_mono(args.wav, target_sr=args.samplerate)
tts = peak_normalize(tts) * args.volume
playback = np.concatenate([tts, tail]).astype(np.float32)
```

这几行做了三件事：

- 读取 `data\test.wav`
- 转成项目使用的采样率，默认 `16000 Hz`
- 做峰值归一化并乘以播放音量

真正播放和录音发生在 callback 里：

```python
block[:n] = playback[cursor:end]
mic[cursor:end] = indata[:n, 0]
farend_ref[cursor:end] = block[:n]
outdata[:, 0] = block
```

可以把它读成：

- `outdata`：送给扬声器播放
- `indata`：从麦克风录进来
- `farend_ref`：把送给扬声器的同一份音频保存下来
- `mic`：保存麦克风真实录音

第二步，用 WebRTC AEC 离线处理：

```powershell
python src\webrtc_aec_offline.py --delay-ms 260 --enable-ns
```

核心代码在 `src\webrtc_aec_offline.py`：

```python
processor = AudioProcessor(
    enable_aec=True,
    enable_ns=enable_ns,
    ns_level=2,
    enable_agc=enable_agc,
    agc_mode=1,
    enable_vad=False,
)
processor.set_stream_format(sr, 1, sr, 1)
processor.set_reverse_stream_format(sr, 1)
processor.set_stream_delay(delay_ms)
```

这里创建了 WebRTC 音频处理器：

- `enable_aec=True`：打开回声消除
- `enable_ns=True`：打开噪声抑制，来自命令行 `--enable-ns`
- `enable_agc=False`：默认不自动增益，避免音量被自动拉来拉去
- `set_stream_delay(delay_ms)`：设置刚才说的延时参数

WebRTC AEC 按 10ms 一帧工作：

```python
processor.process_reverse_stream(ref_frame)
processed = processor.process_stream(mic_frame)
```

这两句很关键：

- `process_reverse_stream(ref_frame)`：先喂“我正在播放什么”
- `process_stream(mic_frame)`：再喂“麦克风听到了什么”
- 返回的 `processed`：就是 WebRTC 清理后的麦克风

输出文件：

```text
outputs\webrtc_aec\cleaned_webrtc_delay_260ms.wav
```

## 5. 实时插话流程

实时脚本是：

```powershell
python src\barge_in_aec_asr_test.py --delay-ms 260 --enable-ns --raw-asr
```

它做的事情比离线版多一些：

```text
随机选 TTS wav
    ↓
按 10ms 一帧播放
    ↓
同一帧 TTS 喂给 WebRTC reverse stream
    ↓
麦克风帧喂给 WebRTC stream
    ↓
得到 clean_frame
    ↓
clean_frame 送入 sherpa VAD/ASR
```

### 5.1 生成播放队列

代码在 `load_tts_sequence()`：

```python
files = sorted(tts_dir.glob("*.wav"))
chosen = [rng.choice(files) for _ in range(rounds)]
```

它会从 `TTS_module\voice\samples` 里随机选几条 TTS。

然后拼接静音间隔：

```python
gap = np.zeros(int(gap_seconds * sr), dtype=np.float32)
tail = np.zeros(int(tail_seconds * sr), dtype=np.float32)
```

静音的作用是让你有机会听清每轮结果，也让末尾的回声尾巴被录下来。

### 5.2 创建 WebRTC AEC

代码在 `main()`：

```python
processor = AudioProcessor(
    enable_aec=True,
    enable_ns=args.enable_ns,
    ns_level=2,
    enable_agc=args.enable_agc,
    agc_mode=1,
    enable_vad=False,
)
processor.set_stream_format(sr, 1, sr, 1)
processor.set_reverse_stream_format(sr, 1)
processor.set_stream_delay(args.delay_ms)
```

这和离线测试是同一套 WebRTC 逻辑。实时测试里默认采样率是 `16000 Hz`，每帧长度：

```python
frame_size = sr // 100
```

`16000 / 100 = 160`，也就是每帧 `160` 个采样点，正好 `10 ms`。

### 5.3 音频 callback

实时音频最重要的代码在 `callback()`：

```python
ref_frame[:n] = playback[start:end]
mic_frame = pad_or_trim(indata[:, 0], frames)
```

这里：

- `ref_frame` 是当前要播放的 TTS 帧
- `mic_frame` 是当前麦克风帧

然后喂给 WebRTC：

```python
processor.process_reverse_stream(float_to_i16_bytes(ref_frame))
clean_frame = i16_bytes_to_float(
    processor.process_stream(float_to_i16_bytes(mic_frame)),
    frames,
)
```

顺序很重要：先喂参考信号，再处理麦克风信号。

然后播放：

```python
outdata[:, 0] = ref_frame
```

同时保存三路音频：

```python
mic_raw[start:end] = mic_frame[:n]
mic_clean[start:end] = clean_frame[:n]
farend_ref[start:end] = ref_frame[:n]
```

最后把清理后的音频送给 ASR：

```python
clean_asr.put(clean_frame[:n])
```

如果你加了 `--raw-asr`，原始麦克风也会送一路 ASR：

```python
raw_asr.put(mic_frame[:n])
```

所以控制台里会看到两种结果：

- `CLEAN_ASR`：去回声后的识别
- `RAW_ASR`：原始麦克风识别

理想状态是：`RAW_ASR` 容易识别到机器人自己的 TTS，`CLEAN_ASR` 尽量只识别你的插话。

## 6. ASR 部分在干什么

ASR 逻辑在 `SherpaMiniWorker` 类里。

初始化 VAD：

```python
vad_config = sherpa_onnx.VadModelConfig()
vad_config.silero_vad.model = str(model_dir / "silero_vad.onnx")
vad_config.silero_vad.min_silence_duration = vad_min_silence
vad_config.silero_vad.threshold = vad_threshold
```

VAD 的作用是判断“什么时候有人声”。有了 VAD，程序不是每 10ms 都识别一次，而是等检测到一小段完整语音后再识别。

初始化识别模型：

```python
self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=str(model_dir / "model.int8.onnx"),
    tokens=str(model_dir / "tokens.txt"),
    use_itn=False,
    num_threads=2,
)
```

识别一段语音：

```python
stream = self.recognizer.create_stream()
stream.accept_waveform(self.sr, segment)
self.recognizer.decode_stream(stream)
text = stream.result.text.strip()
```

这就是把 VAD 切出来的一段音频送给 SenseVoice 模型，然后拿到文字。

## 7. 重要参数怎么理解

`--delay-ms`

WebRTC AEC 的延时估计，单位毫秒。你当前默认设备建议先用 `260`。

`--enable-ns`

打开 WebRTC 噪声抑制。通常建议开，因为 AEC 后可能残留一些噪声和轻微伪影。

`--enable-agc`

打开自动增益。测试阶段不建议先开，因为它可能把残留回声或底噪又拉大。

`--raw-asr`

同时跑原始麦克风识别，用来和 AEC 后结果对比。调试时建议开。

`--asr-gain`

只在送入 ASR 前放大音频，不改变保存的 wav。人声太小时可以调大一点，比如 `3.0`。

`--vad-threshold`

VAD 触发阈值。越高越不容易触发，越低越敏感。

`--volume`

TTS 播放音量。音量越大，回声越强，AEC 压力越大。

## 8. 推荐调试顺序

第一步，确认设备：

```powershell
python src\list_devices.py
```

第二步，录一段固定测试音：

```powershell
python src\play_record.py
```

第三步，扫几个延时：

```powershell
python src\webrtc_aec_offline.py --delay-ms 220 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 240 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 260 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 280 --enable-ns
python src\webrtc_aec_offline.py --delay-ms 300 --enable-ns
```

第四步，试听这些文件：

```text
outputs\webrtc_aec\cleaned_webrtc_delay_xxxms.wav
```

第五步，把最好的延时用于实时脚本：

```powershell
python src\barge_in_aec_asr_test.py --delay-ms 260 --enable-ns --raw-asr
```

## 9. 判断效果好不好

可以从三个角度判断。

第一，听音频：

- `mic_raw.wav`：原始麦克风，应该能听到明显 TTS。
- `mic_clean_webrtc.wav`：AEC 后，TTS 应该明显变小。

第二，看 RMS：

脚本最后会打印：

```text
mic_raw RMS
mic_clean_webrtc RMS
```

如果没有人说话，只播放 TTS，`mic_clean_webrtc RMS` 通常应该比 `mic_raw RMS` 低很多。

第三，看 ASR：

- `RAW_ASR` 识别到机器人 TTS 是正常的。
- `CLEAN_ASR` 如果还频繁识别机器人 TTS，说明 AEC 还不够好，优先重新调 `--delay-ms`。

## 10. 常见问题

### 为什么物理估计延时和最佳 `delay-ms` 不一样

因为 WebRTC 内部还有自己的缓冲和自适应逻辑。`delay-ms` 是给 WebRTC 的系统延时提示，不一定等于互相关算出来的声学峰值。最终以 WebRTC 输出效果为准。

### 为什么换电脑要重新调

音频链路变了。不同电脑、不同声卡、不同驱动、不同输出设备，缓冲都可能变。蓝牙设备尤其容易引入更大延时。

### 为什么默认采样率是 16000

语音识别和 WebRTC AEC 都很常用 16k 单声道。对人声足够，也能减少计算量。

### 为什么要保存 `farend_ref.wav`

它是 AEC 的参考答案来源。没有它，WebRTC 不知道“哪些声音是机器人自己播放的”。

### 为什么 `CLEAN_ASR` 有时还会识别到机器人

常见原因：

- `--delay-ms` 不合适
- 扬声器太响，麦克风输入过载
- 房间混响太强
- 输出设备或输入设备不是你以为的那个
- 系统自带增强、降噪、回声消除和 WebRTC AEC 互相影响

## 11. 当前项目的一句话流程图

```text
TTS wav -> ref_frame -> 扬声器播放
              |
              v
        WebRTC reverse stream

麦克风 -> mic_frame -> WebRTC stream -> clean_frame -> VAD -> ASR -> 文本
```
