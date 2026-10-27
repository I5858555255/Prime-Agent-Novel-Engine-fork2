"""Count a pure-prose UTF-8 file; no Novel-Engine installation or API is needed."""
import argparse
import json
import re
from pathlib import Path

HAN = re.compile(r"[\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]")


def han_count(text):
    return len(HAN.findall(text))


def main(argv=None):
    parser = argparse.ArgumentParser(description="统计纯正文汉字，不含标点、空白、阿拉伯数字和英文")
    parser.add_argument("file", type=Path, help="只包含正文的UTF-8文件，不要包含验收意见或事实变更说明")
    parser.add_argument("--min", dest="minimum", type=int, default=4000)
    parser.add_argument("--max", dest="maximum", type=int, default=5000)
    parser.add_argument("--target", type=int, default=4210)
    args = parser.parse_args(argv)
    if args.minimum < 1 or args.maximum < args.minimum:
        parser.error("invalid range")
    count = han_count(args.file.read_text(encoding="utf-8-sig"))
    passed = args.minimum <= count <= args.maximum
    print(json.dumps({"han": count, "target": args.target, "minimum": args.minimum,
                      "maximum": args.maximum, "in_range": passed}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
