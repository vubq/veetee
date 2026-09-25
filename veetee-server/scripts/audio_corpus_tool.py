#!/usr/bin/env python3
"""Plan, record and validate a natural Vietnamese endpoint/ASR corpus.

This is evaluation tooling only. It never feeds prompt/category labels into
runtime routing. Production benchmark fixtures remain ordinary WAV files plus
<name>.wav.json sidecars consumed by benchmark_corpus.py.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import wave
from collections import Counter
from pathlib import Path
from typing import Any


PLAN_CASES: list[dict[str, Any]] = [
    {"id":"short-01","category":"very_short","text":"Mấy giờ rồi?","critical":True},
    {"id":"short-02","category":"very_short","text":"Hôm nay thứ mấy?","critical":True},
    {"id":"short-03","category":"very_short","text":"Bạn tên gì?","critical":False},
    {"id":"pause-01","category":"internal_pause","text":"Cho tôi biết... thời tiết hôm nay thế nào?","critical":True},
    {"id":"pause-02","category":"internal_pause","text":"Tôi muốn hỏi một chút... bây giờ là mấy giờ?","critical":True},
    {"id":"pause-03","category":"internal_pause","text":"Ừm... bạn có thể giải thích việc này không?","critical":True},
    {"id":"hesitate-01","category":"hesitation","text":"À... tôi đang nghĩ... bạn kể ngắn gọn một câu chuyện nhé.","critical":True},
    {"id":"hesitate-02","category":"hesitation","text":"Ờ thì... hôm nay tôi nên làm gì trước nhỉ?","critical":False},
    {"id":"hesitate-03","category":"hesitation","text":"Để xem nào... bạn nhắc lại câu vừa rồi được không?","critical":False},
    {"id":"name-01","category":"proper_name","text":"Nguyễn Trãi sinh năm bao nhiêu?","critical":True},
    {"id":"name-02","category":"proper_name","text":"Đường Nguyễn Xiển ở quận nào?","critical":True},
    {"id":"name-03","category":"proper_name","text":"Bambu Lab A1 Mini có khổ in bao nhiêu?","critical":False},
    {"id":"number-01","category":"long_number","text":"Đọc giúp tôi số 0987654321.","critical":True},
    {"id":"number-02","category":"long_number","text":"Một triệu hai trăm ba mươi bốn nghìn năm trăm sáu mươi bảy là bao nhiêu?","critical":True},
    {"id":"number-03","category":"long_number","text":"Nhắc lại mã 20260925123045.","critical":True},
    {"id":"long-01","category":"long_sentence","text":"Giải thích ngắn gọn vì sao trợ lý giọng nói cần phản hồi nhanh nhưng vẫn phải giữ câu trả lời tự nhiên và chính xác.","critical":True},
    {"id":"long-02","category":"long_sentence","text":"Nếu tôi hỏi một câu khá dài và có nhiều ý nối tiếp nhau thì bạn hãy nghe hết trước khi bắt đầu trả lời nhé.","critical":True},
    {"id":"long-03","category":"long_sentence","text":"Tôi muốn bạn tóm tắt ba điểm quan trọng nhất của một kế hoạch làm việc nhưng đừng bỏ sót phần rủi ro và việc cần làm tiếp theo.","critical":False},
    {"id":"command-01","category":"command","text":"Nói nhỏ lại một chút.","critical":True},
    {"id":"command-02","category":"command","text":"Dừng lại.","critical":True},
    {"id":"command-03","category":"command","text":"Tiếp tục câu trả lời đi.","critical":True},
    {"id":"neg-01","category":"negation","text":"Đừng dừng, hãy nói tiếp.","critical":True},
    {"id":"neg-02","category":"negation","text":"Tôi không bảo bạn tăng âm lượng.","critical":True},
    {"id":"neg-03","category":"negation","text":"Không phải hôm qua, tôi hỏi hôm nay.","critical":True},
    {"id":"noise-01","category":"background_noise","text":"Mấy giờ rồi?","critical":True,"environment":"fan"},
    {"id":"noise-02","category":"background_noise","text":"Bạn nghe rõ tôi không?","critical":True,"environment":"music_low"},
    {"id":"noise-03","category":"background_noise","text":"Hôm nay thứ mấy?","critical":True,"environment":"street_low"},
    {"id":"repeat-01","category":"self_correction","text":"Ngày mai... à không, hôm nay là thứ mấy?","critical":True},
    {"id":"repeat-02","category":"self_correction","text":"Tăng âm lượng... thôi, giữ nguyên âm lượng.","critical":True},
    {"id":"repeat-03","category":"self_correction","text":"Gọi là A1... ý tôi là A1 Mini.","critical":False},
    {"id":"silence-01","category":"trailing_silence","text":"Xin chào.","critical":True,"trailing_silence_ms":800},
    {"id":"silence-02","category":"trailing_silence","text":"Mấy giờ rồi?","critical":True,"trailing_silence_ms":1200},
    {"id":"silence-03","category":"trailing_silence","text":"Bạn khỏe không?","critical":False,"trailing_silence_ms":1600},
]

MIN_CASES = 30
REQUIRED_CATEGORIES = {
    "very_short": 3,
    "internal_pause": 3,
    "hesitation": 3,
    "proper_name": 3,
    "long_number": 3,
    "long_sentence": 3,
    "background_noise": 3,
    "negation": 3,
}


def write_plan(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "purpose": "natural_audio_endpoint_asr_acceptance",
        "instructions": [
            "Mỗi case thu bằng giọng người thật, không dùng TTS để thay gate tự nhiên.",
            "Giữ nguyên ngập ngừng/pause nếu prompt có dấu ba chấm.",
            "Case background_noise phải ghi môi trường thực tế trong sidecar.",
            "Sau thu âm, label speech_end_sample bằng điểm kết thúc lời nói thực, không phải cuối file.",
        ],
        "cases": PLAN_CASES,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def wav_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        return {
            "channels": handle.getnchannels(),
            "sample_width": handle.getsampwidth(),
            "sample_rate": handle.getframerate(),
            "frames": handle.getnframes(),
            "duration_s": handle.getnframes() / max(1, handle.getframerate()),
        }


def validate_corpus(root: Path) -> dict[str, Any]:
    rows = []
    errors: list[str] = []
    category_counts: Counter[str] = Counter()
    critical = 0
    for wav in sorted(root.glob("*.wav")):
        sidecar = wav.with_suffix(".wav.json")
        if not sidecar.is_file():
            errors.append(f"{wav.name}: missing sidecar")
            continue
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            info = wav_info(wav)
        except Exception as exc:
            errors.append(f"{wav.name}: unreadable: {exc}")
            continue
        transcript = str(meta.get("expected_transcript") or "").strip()
        speech_end = meta.get("speech_end_sample")
        category = str(meta.get("category") or "").strip()
        if not transcript:
            errors.append(f"{wav.name}: expected_transcript missing")
        if not category:
            errors.append(f"{wav.name}: category missing")
        else:
            category_counts[category] += 1
        if meta.get("critical"):
            critical += 1
        try:
            speech_end_i = int(speech_end)
        except (TypeError, ValueError):
            speech_end_i = 0
        if speech_end_i < 1 or speech_end_i > info["frames"]:
            errors.append(
                f"{wav.name}: speech_end_sample={speech_end!r} outside 1..{info['frames']}"
            )
        if info["channels"] != 1:
            errors.append(f"{wav.name}: expected mono, got {info['channels']} channels")
        if info["sample_width"] != 2:
            errors.append(f"{wav.name}: expected 16-bit PCM")
        rows.append({"wav": wav.name, **info, **meta})

    if len(rows) < MIN_CASES:
        errors.append(f"corpus too small: {len(rows)} < {MIN_CASES}")
    for category, required in REQUIRED_CATEGORIES.items():
        actual = category_counts.get(category, 0)
        if actual < required:
            errors.append(f"coverage {category}: {actual} < {required}")

    return {
        "root": str(root),
        "fixtures": len(rows),
        "critical": critical,
        "categories": dict(sorted(category_counts.items())),
        "errors": errors,
        "valid": not errors,
        "fixtures_detail": rows,
    }


def record_case(case: dict[str, Any], output_dir: Path, *, device: str, rate: int, seconds: float) -> Path:
    arecord = shutil.which("arecord")
    if not arecord:
        raise RuntimeError("arecord not found; install alsa-utils or record WAV externally")
    output_dir.mkdir(parents=True, exist_ok=True)
    wav = output_dir / f"{case['id']}.wav"
    command = [
        arecord,
        "-q",
        "-D", device,
        "-f", "S16_LE",
        "-c", "1",
        "-r", str(rate),
        "-d", str(max(1, int(round(seconds)))),
        str(wav),
    ]
    subprocess.run(command, check=True)
    info = wav_info(wav)
    sidecar = {
        "expected_transcript": case["text"].replace("...", " ").strip(),
        "speech_end_sample": info["frames"],
        "category": case["category"],
        "critical": bool(case.get("critical")),
        "environment": case.get("environment", "quiet"),
        "label_status": "needs_human_speech_end_review",
    }
    wav.with_suffix(".wav.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return wav


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--output", default="eval/natural_audio_plan.json")

    validate = sub.add_parser("validate")
    validate.add_argument("--corpus-dir", default="eval/natural_audio")

    record = sub.add_parser("record")
    record.add_argument("--plan", default="eval/natural_audio_plan.json")
    record.add_argument("--id", required=True)
    record.add_argument("--output-dir", default="eval/natural_audio")
    record.add_argument("--device", default="default")
    record.add_argument("--rate", type=int, default=16000)
    record.add_argument("--seconds", type=float, default=8.0)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "plan":
        payload = write_plan(Path(args.output))
        print(json.dumps({
            "output": args.output,
            "cases": len(payload["cases"]),
            "critical": sum(bool(x.get("critical")) for x in payload["cases"]),
            "categories": dict(Counter(x["category"] for x in payload["cases"])),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate":
        report = validate_corpus(Path(args.corpus_dir))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["valid"] else 1

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    case = next((x for x in plan.get("cases", []) if x.get("id") == args.id), None)
    if not case:
        raise SystemExit(f"case id not found: {args.id}")
    print(f"RECORD: {case['id']} [{case['category']}]")
    print(f"SAY: {case['text']}")
    wav = record_case(
        case,
        Path(args.output_dir),
        device=args.device,
        rate=args.rate,
        seconds=args.seconds,
    )
    print(wav)
    print("IMPORTANT: review speech_end_sample in the sidecar before benchmarking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
