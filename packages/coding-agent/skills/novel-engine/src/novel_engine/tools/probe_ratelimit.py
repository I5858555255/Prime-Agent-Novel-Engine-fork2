import json, time, os, sys

# make novel_engine importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from novel_engine.core.model_router import ModelRouter

def _classify(err):
    s = str(err).lower()
    t = type(err).__name__.lower()
    if "timeout" in s or "readtimeout" in t or "connecttimeout" in t:
        return "timeout"
    return "error"

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="list models and exit without calling the API")
    ap.add_argument("--calls", type=int, default=3, help="requests per model")
    ap.add_argument("--timeout", type=int, default=45, help="per-request timeout seconds for the probe")
    ap.add_argument("--max-tokens", type=int, default=32, help="max_tokens per probe request")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(base, "config", "runtime_config.json"), encoding="utf-8"))
    models = sorted({m for ph in cfg["model_router"]["phases"].values() for m in ph})
    if args.dry:
        print("Would probe:", models)
        return

    out = {}
    for m in models:
        probe_cfg = {
            "llm": {**cfg.get("llm", {}), "timeout_seconds": args.timeout},
            "model_router": {
                "phases": {"probe": [m]},
                "limits": {m: cfg["model_router"]["limits"].get(m, {"rpm": 1000, "tpm": 100000})},
            },
        }
        mr = ModelRouter("probe", probe_cfg)
        lats = []; timeouts = 0; errors = 0; ok = 0
        for _ in range(args.calls):
            t0 = time.monotonic()
            try:
                mr.chat_completion([{"role": "user", "content": "回复一个字：好"}], max_tokens=args.max_tokens)
                lats.append(time.monotonic() - t0); ok += 1
            except Exception as e:
                lats.append(time.monotonic() - t0)
                if _classify(e) == "timeout":
                    timeouts += 1
                else:
                    errors += 1
        lats.sort()
        p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))] if lats else 0
        avg = sum(lats) / len(lats) if lats else 0
        rec = {
            "calls": args.calls, "ok": ok, "timeouts": timeouts, "errors": errors,
            "avg_latency_s": round(avg, 2), "p95_latency_s": round(p95, 2),
            "success_rate": round(ok / args.calls, 2),
        }
        out[m] = rec
        print(f"{m}: ok={ok} timeouts={timeouts} errors={errors} avg={rec['avg_latency_s']}s p95={rec['p95_latency_s']}s")
    os.makedirs("runtime", exist_ok=True)
    json.dump(out, open(os.path.join(base, "runtime", "ratelimit_probe.json"), "w"), ensure_ascii=False, indent=2)
    print("wrote runtime/ratelimit_probe.json")

if __name__ == "__main__":
    main()
