# WebRTC AEC 问答教学记录

这份文档记录阅读 `docs\webrtc_aec_tutorial.md` 后提出的问题，并结合当前项目代码做进一步解释。

## 问题 1：`260ms` 是如何计算得出的

先说结论：当前项目里的 `260ms` 不是某个公式直接算出来的，而是用 `data\test.wav` 在当前电脑、当前默认麦克风、当前默认扬声器上实测扫参得到的推荐值。

它对应命令里的这个参数：

```powershell
python src\barge_in_aec_asr_test.py --delay-ms 260 --enable-ns --raw-asr
```

代码里真正使用它的位置是：

```python
processor.set_stream_delay(args.delay_ms)
```

在离线脚本里是：

```python
processor.set_stream_delay(delay_ms)
```

这个参数告诉 WebRTC AEC：

```text
我播放出去的参考音频 farend_ref，大概过了多少毫秒后，会以回声形式出现在麦克风 mic 里。
```

### 实测过程

第一步，先用默认设备播放并录音：

```powershell
python src\play_record.py --out-dir outputs\default_delay_test --volume 0.85
```

这个命令会生成：

```text
outputs\default_delay_test\farend_ref.wav
outputs\default_delay_test\mic_recording.wav
```

第二步，用不同 `delay-ms` 跑 WebRTC AEC：

```powershell
python src\webrtc_aec_offline.py --mic outputs\default_delay_test\mic_recording.wav --ref outputs\default_delay_test\farend_ref.wav --out-dir outputs\default_delay_test\webrtc_sweep --delay-ms 240 --enable-ns
python src\webrtc_aec_offline.py --mic outputs\default_delay_test\mic_recording.wav --ref outputs\default_delay_test\farend_ref.wav --out-dir outputs\default_delay_test\webrtc_sweep --delay-ms 260 --enable-ns
python src\webrtc_aec_offline.py --mic outputs\default_delay_test\mic_recording.wav --ref outputs\default_delay_test\farend_ref.wav --out-dir outputs\default_delay_test\webrtc_sweep --delay-ms 280 --enable-ns
python src\webrtc_aec_offline.py --mic outputs\default_delay_test\mic_recording.wav --ref outputs\default_delay_test\farend_ref.wav --out-dir outputs\default_delay_test\webrtc_sweep --delay-ms 320 --enable-ns
```

第三步，对比输出的 `Cleaned RMS` 和实际听感。当前机器测试结果里，`260ms` 最好：

```text
Delay: 260 ms
Mic RMS: -23.4 dBFS
Cleaned RMS: -40.5 dBFS
RMS reduction: 17.1 dB
```

RMS 可以简单理解成“这段音频整体能量”。如果测试时没有人说话，只有 TTS 回声，那么 AEC 后的 RMS 越低，通常说明 TTS 残留越少。

### 为什么不是直接用物理估计值

我们也做过互相关估计，物理声学峰值大约是 `363.6ms`。但是 WebRTC AEC 里的 `set_stream_delay()` 不是单纯的“空气传播时间”，它还涉及 WebRTC 内部缓冲和算法对齐。最终应该以 WebRTC 输出效果为准。

所以更准确的说法是：

```text
363.6ms 是录音里参考音频和麦克风回声的相关峰值。
260ms 是当前 WebRTC AEC 实测效果最好的 delay-ms 参数。
```

这两个值不完全相等是正常的。

### 教学版理解

你可以把调 `delay-ms` 理解成对齐两条时间线：

```text
时间线 A：程序知道自己什么时候播放了 TTS
时间线 B：麦克风什么时候听到了这个 TTS 的回声
```

如果对齐差太多，WebRTC 就像拿错时间段的参考音频去消当前麦克风声音，效果会变差。

## 问题 2：什么是峰值归一化，为什么还要乘以播放音量

教程里的代码是：

```python
tts, sr = read_mono(args.wav, target_sr=args.samplerate)
tts = peak_normalize(tts) * args.volume
playback = np.concatenate([tts, tail]).astype(np.float32)
```

位置在：

```text
src\play_record.py
```

### 什么是峰值

数字音频一般可以理解成一串数字。这个项目里音频通常是 `float32`，范围大概是：

```text
-1.0 到 1.0
```

其中：

- `0.0` 表示没有振动
- 正数表示波形向一个方向偏
- 负数表示波形向另一个方向偏
- 绝对值越大，瞬时振幅越大

峰值就是整段音频里绝对值最大的那个点：

```python
current = float(np.max(np.abs(audio)))
```

比如一段音频的最大绝对值是：

```text
0.40
```

那它的峰值就是 `0.40`。

### 什么是峰值归一化

峰值归一化就是把整段音频按比例放大或缩小，让它的最大峰值接近目标值。

项目代码在 `src\audio_utils.py`：

```python
def peak_normalize(audio: np.ndarray, peak: float = 0.95) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    current = float(np.max(np.abs(audio))) if audio.size else 0.0
    if current <= 1e-8:
        return audio
    return audio * min(1.0, peak / current)
```

这段代码的默认目标峰值是 `0.95`。

如果原始音频峰值是 `0.40`：

```text
0.95 / 0.40 = 2.375
```

理论上可以放大 `2.375` 倍。

但是这段代码用了：

```python
min(1.0, peak / current)
```

这意味着它最多只会防止过大，不会把小音频主动放大超过原音量。更准确地说：

- 如果音频峰值超过 `0.95`，就缩小到不爆音。
- 如果音频峰值本来小于 `0.95`，就保持不变。

所以当前项目里的 `peak_normalize()` 是“防爆音式归一化”，不是“强制拉满式归一化”。

### 为什么要做峰值归一化

原因是避免播放时削波。

如果音频数字超过可播放范围，系统最终会把超过的地方截断。比如本来波形应该是：

```text
0.2, 0.6, 1.1, 0.7
```

超过 `1.0` 的 `1.1` 可能会被硬截成 `1.0`：

```text
0.2, 0.6, 1.0, 0.7
```

这叫削波，听起来会失真。对 AEC 来说，失真更麻烦，因为 WebRTC 手里的 `farend_ref` 是没失真的数字音频，但麦克风录到的是扬声器失真后的版本，两者更不像，消除更困难。

### 为什么还要乘以播放音量

这一行：

```python
tts = peak_normalize(tts) * args.volume
```

里面的 `args.volume` 是人为设置的播放增益，默认在 `play_record.py` 里是：

```python
parser.add_argument("--volume", type=float, default=0.85)
```

它的作用是控制播放给扬声器的音量。

比如：

```text
volume = 1.0  原始音量
volume = 0.85 稍微小一点
volume = 0.5  小很多
```

为什么不直接用系统音量？因为代码里乘以 `volume` 有两个好处：

1. 测试可复现：同一个 wav，每次送给声卡的数字音量一致。
2. 避免过载：TTS 太响会让麦克风或扬声器失真，AEC 更难处理。

### 小白版总结

```text
peak_normalize：先保证音频不要太大，避免爆音和削波。
volume：再由你决定这次测试想用多大播放音量。
```

## 问题 3：AEC 和 NS 的底层原理是什么

代码是：

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

### AEC 是什么

AEC 是 Acoustic Echo Cancellation，声学回声消除。

它处理的是这种问题：

```text
扬声器播放的声音，被麦克风又录回来了。
```

在这个项目里，AEC 的目标是压掉机器人自己播放的 TTS，让 ASR 更关注用户插话。

### AEC 的技术原理

简化理解，AEC 会做三件事：

1. 拿到参考信号：我知道自己正在播放什么。
2. 建模回声路径：估计这段声音经过扬声器、空气、房间、麦克风后会变成什么样。
3. 从麦克风里减掉估计出来的回声。

用公式感受一下：

```text
mic = user_voice + echo(tts) + noise
clean = mic - estimated_echo(tts)
```

真实 WebRTC AEC 比这个复杂得多。它不仅有自适应滤波，还会处理双讲检测、非线性残留、频域抑制、延时对齐等问题。

双讲检测很重要。所谓双讲就是：

```text
机器人在说话，用户也在说话。
```

这时 AEC 不能粗暴地把所有像 TTS 的东西都压掉，否则可能伤到用户声音。

### 项目里如何实现 AEC

项目没有自己手写 AEC，而是调用 WebRTC Audio Processing：

```python
processor = AudioProcessor(enable_aec=True, ...)
```

实时处理中，每 10ms 执行：

```python
processor.process_reverse_stream(float_to_i16_bytes(ref_frame))
clean_frame = i16_bytes_to_float(
    processor.process_stream(float_to_i16_bytes(mic_frame)),
    frames,
)
```

这里：

- `ref_frame`：当前播放的 TTS
- `mic_frame`：当前麦克风录音
- `clean_frame`：AEC 后的麦克风

### NS 是什么

NS 是 Noise Suppression，噪声抑制。

它处理的是更泛化的背景噪声，比如：

- 风扇声
- 空调声
- 电流底噪
- 轻微环境噪音
- AEC 后残留的一些细碎噪声

### NS 的技术原理

NS 通常会估计“哪些频率更像稳定噪声”，然后在频域里把这些部分压低。

非常简化地说：

```text
语音：频率和能量随时间变化，有明显结构
噪声：更稳定、更连续、更像背景
```

NS 会尽量压掉背景里稳定的部分，同时保留语音。

WebRTC NS 不是万能的。如果噪声本身像人声，比如旁边有人说话，NS 很难完全去掉。

### 项目里如何打开 NS

命令行加：

```powershell
--enable-ns
```

代码里：

```python
enable_ns=args.enable_ns
ns_level=2
```

`ns_level=2` 是噪声抑制等级。越强可能噪声越小，但也可能让人声变薄、变闷、有机械感。

### 一点工程思考

AEC 和 NS 是两类问题：

```text
AEC：我知道噪声源是什么，因为 TTS 是我自己播放的。
NS：我不知道噪声具体是什么，只能估计它像不像背景噪声。
```

所以 AEC 通常比 NS 更适合处理机器人自回声。因为 AEC 有 `farend_ref` 这个参考答案。

推荐顺序是：

```text
先用 AEC 压掉已知 TTS 回声，再用 NS 处理剩余背景噪声。
```

这也是项目现在的做法。

## 问题 4：为什么 WebRTC AEC 按 10ms 一帧工作

教程里写：

```python
processor.process_reverse_stream(ref_frame)
processed = processor.process_stream(mic_frame)
```

你问得很准：为什么是 10ms？采样率整数倍是不是原因？是不是有点内存对齐的意思？

### 先看项目代码

实时脚本里：

```python
sr = args.samplerate
frame_size = sr // 100
```

默认采样率：

```text
sr = 16000
```

所以：

```text
frame_size = 16000 // 100 = 160 samples
```

160 个采样点，在 16000Hz 下正好是：

```text
160 / 16000 = 0.01 秒 = 10ms
```

离线脚本里也会向 WebRTC 查询它要求的帧长：

```python
frame_size = int(processor.get_frame_size())
```

### 实测结果

我在当前环境里测了这个 Python 封装：

```text
采样率 8000  -> frame_size 80
采样率 16000 -> frame_size 160
采样率 32000 -> frame_size 320
采样率 48000 -> frame_size 480
```

这些都是 10ms：

```text
8000 * 0.01 = 80
16000 * 0.01 = 160
32000 * 0.01 = 320
48000 * 0.01 = 480
```

同时测试发现，在 16000Hz 下：

```text
160 samples：可以处理
80 samples：报错
320 samples：报错
```

报错信息是：

```text
ValueError: Input size does not match the expected frame size.
```

所以对当前 `aec-audio-processing` 封装来说，不是“建议 10ms”，而是“必须传它要求的 10ms 帧”。

### 为什么 WebRTC 常用 10ms

语音实时处理里，10ms 是一个很常见的折中点。

太小，比如 1ms：

- 调用次数变多
- Python callback 压力变大
- 系统调度开销变大
- 每帧信息太少，算法估计不稳定

太大，比如 100ms：

- 延迟明显增加
- ASR 和插话响应变慢
- AEC 更新不够及时
- 实时互动体验变差

10ms 的好处是：

- 延迟足够低
- 每帧又有足够语音信息
- CPU 调用频率还可以接受
- 和很多语音算法、声卡缓冲、WebRTC 内部设计匹配

### 关于“采样率整数倍”的理解

你的直觉是对的，但可以更准确一点：

不是为了“内存对齐”，而是为了“时间分帧对齐”。

`10ms` 对常见语音采样率都能得到整数采样点：

```text
8k  -> 80 samples
16k -> 160 samples
32k -> 320 samples
48k -> 480 samples
```

这样每帧没有小数采样点，也就不用纠结余数。

内存对齐也可能影响底层性能，但在这个项目里，更核心的是：

```text
WebRTC API 明确要求固定帧长。
语音实时算法需要稳定的时间块。
```

### 如果想用更大块怎么办

如果上层一次拿到 320 个采样，不应该直接传给 WebRTC。正确做法是拆成两个 160：

```text
320 samples = 160 samples + 160 samples
```

也就是两个 10ms 帧连续处理。

## 问题 5：10ms 一帧播放会不会很耗性能

直觉上看，10ms 一帧意味着每秒 100 次 callback，好像挺频繁。

但对现代电脑来说，这个开销通常不大。

### 为什么开销不大

默认配置：

```text
采样率：16000 Hz
声道：1
帧长：160 samples
每秒帧数：100
```

每帧是 160 个 float 或 int16 样本。这个数据量非常小。

每秒音频原始数据大概：

```text
16000 samples/s * 2 bytes = 32 KB/s
```

这点数据对 CPU 和内存都很轻。

### 真正需要注意的不是计算量，而是实时调度

音频 callback 要稳定。每 10ms 系统会叫一次 callback，如果 callback 里做太慢，就可能出现爆音、卡顿、丢帧。

所以项目里 callback 只做很少的事情：

```python
processor.process_reverse_stream(...)
clean_frame = processor.process_stream(...)
outdata[:, 0] = ref_frame
clean_asr.put(clean_frame[:n])
```

ASR 没有直接在 callback 里跑，而是放进队列：

```python
clean_asr.put(clean_frame[:n])
```

真正识别在线程里做：

```python
self._thread = threading.Thread(target=self._run, daemon=True)
```

这是一个重要设计：音频 callback 保持轻量，ASR 这种可能比较慢的任务放到后台线程。

### 会不会因为“频繁启动和结束播放”而耗性能

不会。这里不是每 10ms 启动一次播放器。

程序只创建了一次音频流：

```python
with sd.Stream(..., callback=callback):
    sd.sleep(...)
```

然后声卡系统每 10ms 调用一次 callback。可以理解成：

```text
播放器一直开着，只是每次往里面填 10ms 的新音频。
```

所以没有反复启动/停止播放器的高成本。

### 什么时候性能会出问题

常见风险是：

- callback 里做了太多 Python 计算
- 在 callback 里读写大文件
- 在 callback 里做 ASR 推理
- 电脑 CPU 很忙
- 声卡驱动不稳定
- blocksize 太小导致调度压力过大

当前项目的 10ms 分帧设计是合理的。

## 问题 6：能不能处理动态环境噪音，或者其他播放器播放的歌曲

这个问题非常好，因为它触到了 AEC 的边界。

### 先区分两类声音

第一类：程序自己播放的声音。

比如本项目里的 TTS：

```text
TTS wav -> 程序播放 -> 扬声器 -> 麦克风
```

这种声音程序知道原始数字音频，所以可以生成 `farend_ref`。AEC 最擅长处理这种。

第二类：程序拿不到原始音频的声音。

比如：

- 其他播放器正在放歌
- 浏览器视频声音
- 微信语音外放
- 房间里其他人说话
- 外面施工声

这些声音如果项目拿不到对应的数字参考信号，就不能作为 AEC 的 `farend_ref`。WebRTC AEC 不知道“要消掉的具体是什么”，只能靠 NS 或其他降噪方法压一压。

### 其他播放器放歌能不能处理

理论上可以，但前提是你能拿到系统播放混音，也就是所谓 loopback 音频。

如果能拿到：

```text
系统正在播放的所有声音 -> loopback_ref
麦克风录音 -> mic
WebRTC AEC -> 尝试消掉系统播放声音
```

这个思路在工程上是可行的。

但它和当前项目不同。当前项目的 `farend_ref` 来自我们自己要播放的 TTS：

```python
farend_ref[start:end] = ref_frame[:n]
```

如果要处理其他播放器，需要新增“系统回放采集”能力。Windows 上通常要考虑 WASAPI loopback。

### 动态环境噪声能不能处理

如果是风扇、空调、电流声这种稳定噪声，NS 可以帮忙：

```powershell
--enable-ns
```

如果是旁边人说话、音乐、人群声，这类噪声和目标人声很像，普通 NS 很难完美分离。

这时可能要考虑更高级的方案：

- 麦克风阵列波束形成：利用多个麦克风的空间方向信息。
- 神经网络降噪：比如 RNNoise、DeepFilterNet、DNS 类模型。
- 目标说话人识别/声纹增强：只保留某个说话人的声音。
- 系统 loopback + AEC：如果噪声来自电脑播放源，就拿播放源当参考信号。

### 一个现实路线

当前项目可以按这个路线升级：

1. 保留现有 TTS AEC：这是最确定、最可控的。
2. 加 Windows WASAPI loopback：捕获系统正在播放的所有声音。
3. 把 loopback 作为更完整的 `farend_ref`。
4. AEC 后再接 NS 或神经网络降噪。
5. 最后送 ASR。

流程可能变成：

```text
系统播放混音 loopback -> WebRTC reverse stream
麦克风 mic             -> WebRTC stream
clean_frame           -> NS/神经网络降噪 -> VAD/ASR
```

### 需要注意的坑

loopback 会把所有系统声音都当成“要消掉的远端声音”。如果用户声音也从某个软件里播放出来，它也可能被消掉。

另外，其他播放器的音频和麦克风之间的延时可能和本项目 TTS 播放链路不同。多个播放源混在一起时，延时估计会更复杂。

### 一点判断

你的大胆假设是有价值的。它可以分成两种可行性：

```text
电脑能拿到数字参考音频：可以尝试 AEC，工程上有希望。
电脑拿不到数字参考音频：只能当普通噪声处理，难度明显更高。
```

所以关键问题不是“声音是不是动态”，而是：

```text
我们能不能拿到它的参考信号？
```

只要能拿到参考信号，就能进入 AEC 的范畴。拿不到，就进入降噪、分离、增强的范畴。

## 最后总结

当前项目的核心思想是：

```text
已知自己播放了什么 -> 把它作为参考信号 -> WebRTC AEC 从麦克风里压掉这部分 -> ASR 只听更干净的人声
```

`260ms` 是当前机器实测出来的 WebRTC 延时参数。  
10ms 一帧是 WebRTC Audio Processing 的固定处理节奏。  
AEC 适合处理“有参考信号”的回声。  
NS 适合处理“没有明确参考信号”的稳定背景噪声。  
其他播放器声音如果能通过 loopback 拿到，就可以进一步尝试扩展成系统级 AEC。

