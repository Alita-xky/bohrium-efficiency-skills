#!/usr/bin/env python3
"""Assemble /app/output/result.json so that it contains case_id and EVERY field of the case's
output_contract, filled from the agent's fill.json plus automatically collected artifact facts.

Usage:
  python3 make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json

fill.json: the agent's task-specific answers, keyed by output_contract field names (any extra keys are
kept under "extra"). Allowed status values: "complete" | "partial" | "failed".
Missing contract fields are NOT invented: they are written as
  {"status": "not_provided", "reason": "..."} and the overall status is downgraded to "partial".
"""
import argparse, glob, hashlib, json, os, sys


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def contract_fields(contract):
    if contract is None:
        return []
    if isinstance(contract, dict):
        if "fields" in contract and isinstance(contract["fields"], (list, dict)):
            return contract_fields(contract["fields"])
        if "required" in contract and isinstance(contract["required"], list):
            return list(contract["required"]) + [k for k in contract.get("properties", {}) if k not in contract["required"]]
        if "properties" in contract and isinstance(contract["properties"], dict):
            return list(contract["properties"].keys())
        return list(contract.keys())
    if isinstance(contract, list):
        out = []
        for x in contract:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                out.append(x.get("name") or x.get("field") or x.get("key") or json.dumps(x, ensure_ascii=False)[:40])
        return out
    if isinstance(contract, str):
        return [s.strip() for s in contract.replace(";", ",").split(",") if s.strip()]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--fill", required=True)
    a = ap.parse_args()
    case = {}
    try:
        case = json.load(open(a.case, encoding="utf-8"))
    except Exception as e:
        print(f"WARN cannot read case: {e!r}")
    fill = json.load(open(a.fill, encoding="utf-8")) if os.path.exists(a.fill) else {}
    contract = find_key(case, "output_contract")
    fields = contract_fields(contract)
    case_id = find_key(case, "case_id") or find_key(case, "id") or fill.get("case_id") or "unknown"

    artifacts = []
    for p in sorted(glob.glob(os.path.join(a.outdir, "**", "*"), recursive=True)):
        if os.path.isfile(p) and os.path.basename(p) != "result.json":
            artifacts.append(dict(path=p, bytes=os.path.getsize(p), sha256=sha256(p)))
    render = {}
    rp = os.path.join(a.outdir, "render_report.json")
    if os.path.exists(rp):
        r = json.load(open(rp, encoding="utf-8"))
        render = dict(status=r.get("status"), fonts=r.get("fonts"), font_notes=r.get("font_notes"),
                      issues=[i for i in r.get("issues", []) if i.get("level") == "fail"][:20])

    result = {"case_id": case_id}
    missing = []
    for f in fields:
        if f == "case_id":
            continue
        if f in fill:
            result[f] = fill[f]
        else:
            result[f] = {"status": "not_provided", "reason": "no evidence produced for this field in this run"}
            missing.append(f)
    extra = {k: v for k, v in fill.items() if k not in fields and k != "case_id"}
    status = fill.get("status", "complete")
    if missing and status == "complete":
        status = "partial"
    if render.get("status") == "needs_fix" and status == "complete":
        status = "partial"
    if "status" not in fields:
        result["status"] = status
    if "status" in fields and isinstance(result.get("status"), str) and result["status"] == "complete" and (missing or render.get("status") == "needs_fix"):
        result["status"] = "partial"
        result["status_note"] = "downgraded from complete: missing contract fields or failed render checks"
    result["artifacts"] = artifacts if "artifacts" not in fields else result.get("artifacts")
    result["render_check"] = render
    if extra:
        result["extra"] = extra
    result["contract_fields"] = fields
    result["missing_fields"] = missing
    os.makedirs(a.outdir, exist_ok=True)
    out = os.path.join(a.outdir, "result.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(result=out, case_id=case_id, n_contract_fields=len(fields), missing=missing,
                          status=result.get("status", status), n_artifacts=len(artifacts)), ensure_ascii=False))
    if not fields:
        print("WARN no output_contract found in case.json: print the case file and fill fields by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
