class VideoParser {
    constructor(onNaluCallback, debug = false) {
        this.debug = debug
        this.chunks = [];       // 用数组暂存分段，避免每次 appendData 都全量拷贝
        this.totalLength = 0;   // 跟踪总字节数
        this.name = null;
        this.width = null;
        this.height = null;
        this.hasKeyFrame = null;
        this.sps = null;
        this.pps = null;
        this.mimeCodec = null;
        this.onNaluCallback = onNaluCallback;
        this.hasSentSpsPps = false;
        // 【多人观看】标记是否在等待一个完整的关键帧（避免晚入后从 P 帧开头解析导致花屏）
        this.waitingForIFrame = false;
    }

    appendData(data) {
        // Guard against empty or null payloads to avoid slice/read errors.
        if (!data || data.length === 0) {
            return;
        }
        this.chunks.push(data);
        this.totalLength += data.length;
        this.scrcpyProcessBuffer();
    }

    _mergeChunks() {
        if (this.chunks.length === 0) return new Uint8Array(0);
        if (this.chunks.length === 1) return this.chunks[0];
        const merged = new Uint8Array(this.totalLength);
        let offset = 0;
        for (const chunk of this.chunks) {
            merged.set(chunk, offset);
            offset += chunk.length;
        }
        return merged;
    }

    scrcpyProcessBuffer() {
        // 合并所有暂存分段，处理完后只保留未消费的尾部
        const buffer = this._mergeChunks();
        let startIndex = 0;
        if (this.name == null) {
            if (buffer.length >= 64) {
                const name = buffer.slice(0, 64);
                this.name = new TextDecoder().decode(name);
                console.log("Device name:" + this.name);
                if (this.onNaluCallback) {
                    this.onNaluCallback({
                        type: 'name',
                        data: { "name": this.name }
                    });
                }
                startIndex = 64;
            }
        } else if (this.width == null) {
            if (buffer.length >= 12) {
                const id = new DataView(buffer.buffer).getInt32(0, false);
                this.width = new DataView(buffer.buffer).getInt32(4, false);
                this.height = new DataView(buffer.buffer).getInt32(8, false);
                console.log("width:" + this.width + " height:" + this.height);
                if (this.onNaluCallback) {
                    this.onNaluCallback({
                        type: 'screen_size',
                        data: { "width": this.width, "height": this.height }
                    });
                }
                startIndex += 12;
            }
        } else while (buffer.length - startIndex > 12) {
            const size = new DataView(buffer.buffer).getInt32(startIndex + 8, false);

            // 【流同步容错】如果解析到的 frame 尺寸极其荒谬（如负数、>5MB），说明是因为晚入加入导致的 TCP 二进制流截断。
            // 此时抛弃当前的伪头部，直接在剩余流中暴力搜索下一个 00000001（NALU Header）的起点来进行恢复！
            if (size <= 0 || size > 5000000) {
                console.warn(`Stream dropped sync (parsed size=${size}), hunting for next NALU...`);
                // scrcpy 3.x 协议里 H.264 的 header 是 12 字节（pts: 8, size: 4）
                const naluStart = this.findSequence(buffer, [0, 0, 0, 1], startIndex + 12);
                if (naluStart !== -1 && naluStart >= 12) {
                    // 找到下一个 NALU，回退 12 字节对齐到 scrcpy 协议头
                    startIndex = naluStart - 12;
                    continue; // 重新从对齐的头部开始解析这个包！
                } else {
                    // 没有发现完整的 NALU 头部序列，保留最后 15 个字节（防止截断 00000001），中止当前循环
                    startIndex = Math.max(0, buffer.length - 15);
                    break;
                }
            }

            if (buffer.length - startIndex >= 12 + size) {
                const nalu = buffer.slice(startIndex + 12, startIndex + 12 + size);
                this.processBuffer(nalu)
                startIndex = startIndex + 12 + size;
            } else {
                break;
            }
        }
        // 保留未消费的尾部，重置 chunks
        const remaining = buffer.slice(startIndex);
        this.chunks = remaining.length > 0 ? [remaining] : [];
        this.totalLength = remaining.length;
    }


    findSequence(arr, sequence, startIndex = 0) {
        const seqLength = sequence.length;
        for (let i = startIndex; i <= arr.length - seqLength; i++) {
            let match = true;
            for (let j = 0; j < seqLength; j++) {
                if (arr[i + j] !== sequence[j]) {
                    match = false;
                    break;
                }
            }
            if (match) {
                return i;
            }
        }
        return -1;
    }

    processBuffer(nalu) {
        // Need at least NALU header (0 0 0 1 XX)
        if (!nalu || nalu.length < 5) {
            if (this.debug) console.warn('skip short nalu', nalu && nalu.length);
            return;
        }
        const nalu_type = nalu[4] & 0x1f;

        // 等到 IDR 关键帧时，解除限制，允许正常画面渲染
        if (nalu_type === 5) {
            if (this.waitingForIFrame) console.log("I-Frame received, resuming decoding/rendering.");
            this.waitingForIFrame = false;
        }

        // 如果仍处在等待（比如晚入者刚加入），则丢弃所有传入的包，防止因为送入 P/B 帧造成严重花屏
        if (this.waitingForIFrame) {
            if (this.debug) console.log(`Skipping NALU (type=${nalu_type}) waiting for I-Frame...`);
            return;
        }

        if (nalu_type === 1) {
            if (this.debug)
                console.log("P frame", nalu.length)
        } else if (nalu_type === 5) {
            if (this.debug)
                console.log("I frame", nalu.length)
        } else if (nalu_type === 7) {
            const next_pos = this.findSequence(nalu, [0, 0, 0, 1], 5)
            if (next_pos > 0) {
                this.sps = nalu.slice(0, next_pos)
                if (this.debug)
                    console.log("sps", next_pos)
                this.processBuffer(nalu.slice(next_pos))
            } else {
                this.sps = nalu
                if (this.debug)
                    console.log("sps", nalu.length)
            }
            // Guard against malformed SPS
            if (!this.sps || this.sps.length < 5) {
                if (this.debug) console.warn('skip malformed sps', this.sps && this.sps.length);
                return;
            }
            let ret = SPSParser.parseSPS(this.sps.slice(4));
            if (this.onNaluCallback) {
                this.onNaluCallback({
                    type: 'size_change',
                    data: { "width": ret.present_size.width, "height": ret.present_size.height }
                });
            }
            return;
        } else if (nalu_type === 8) {
            const next_pos = this.findSequence(nalu, [0, 0, 0, 1], 5)
            if (next_pos > 0) {
                this.pps = nalu.slice(0, next_pos)
                if (this.debug)
                    console.log("pps", next_pos)
                this.processBuffer(nalu.slice(next_pos))
            } else {
                this.pps = nalu
                if (this.debug)
                    console.log("pps", nalu.length)
            }
            if (!this.pps || this.pps.length === 0) {
                if (this.debug) console.warn('skip malformed pps');
                return;
            }
            return;
        } else {
            console.log("unknow frame type", nalu[0], nalu[1], nalu[2], nalu[3], nalu_type)
        }

        if (this.pps != null && this.sps != null) {
            if (this.onNaluCallback) {
                this.onNaluCallback({
                    type: 'init',
                    data: { "width": this.width, "height": this.height, "pps": this.pps, "sps": this.sps }
                });
            }
            this.pps = null;
            this.sps = null;
        }
        if (this.onNaluCallback) {
            this.onNaluCallback({
                type: 'nalu',
                data: nalu
            });
        }
    }
}
