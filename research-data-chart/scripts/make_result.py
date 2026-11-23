#!/usr/bin/env python3
"""Assemble /app/output/result.json so that it contains case_id and EVERY field of the case's
output_contract, filled from the agent's fill.json plus automatically collected artifact facts.

Usage:
  python3 make_result.py --case /input/case.json --list-fields          (step 1: which keys are required)
  python3 make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json

fill.json: the agent's task-specific answers, keyed by output_contract field names (any extra keys are
kept under "extra"). Allowed status values: "complete" | "partial" | "failed".
Missing contract fields are NOT invented: they are written as
  {"status": "not_provided", "reason": "..."} and the overall status is downgraded to "partial".
"""
import argparse, glob, hashlib, json, os, re, sys



def load_json_lenient(path):
    """Agent-written JSON: allow raw newlines/tabs inside strings and a trailing comma; on failure
    exit with the line/column and the offending line instead of a traceback."""
    txt = open(path, encoding="utf-8-sig").read()
    try:
        return json.loads(txt, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", txt), strict=False)
        except json.JSONDecodeError as e:
            line = txt.splitlines()[e.lineno - 1] if e.lineno - 1 < len(txt.splitlines()) else ""
            sys.exit(f"INVALID JSON in {path}: {e.msg} at line {e.lineno} col {e.colno}: {line[:160]!r}")

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


LIST_KEYS = ("fields", "required", "required_fields", "result_fields", "required_keys", "keys", "outputs",
             "result_json_fields", "result.json", "result_json", "items", "output_fields")
META_KEYS = {"notes", "note", "description", "comment", "format", "type", "title", "$schema"}


def contract_fields(contract):
    """Field names from any common output_contract shape: list of names, list of {name|field|key},
    JSON-schema {required, properties}, {"required_fields": [...]} / {"fields": ...} wrappers, or a
    {field: description} dict. Wrapper/meta keys (notes, description ...) are never fields."""
    if contract is None:
        return []
    if isinstance(contract, str):
        return [s.strip() for s in contract.replace(";", ",").split(",") if s.strip()]
    if isinstance(contract, list):
        out = []
        for x in contract:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                out.append(x.get("name") or x.get("field") or x.get("key") or json.dumps(x, ensure_ascii=False)[:40])
        return out
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            req = contract.get("required") if isinstance(contract.get("required"), list) else []
            return list(req) + [k for k in contract["properties"] if k not in req]
        out = []
        for k in LIST_KEYS:
            if isinstance(contract.get(k), (list, dict)):
                out += [f for f in contract_fields(contract[k]) if f not in out]
        if out:
            return out
        return [k for k in contract if k not in META_KEYS]
    return []


def broken_paths(obj, outdir, found=None):
    """String values that look like output file paths but do not exist."""
    found = [] if found is None else found
    if isinstance(obj, dict):
        for v in obj.values():
            broken_paths(v, outdir, found)
    elif isinstance(obj, list):
        for v in obj:
            broken_paths(v, outdir, found)
    elif isinstance(obj, str) and re.fullmatch(r"[^\s]+\.(svg|pdf|png|json|csv|pptx|docx|xlsx)", obj):
        cands = [obj, os.path.join(outdir, obj), os.path.join(outdir, os.path.basename(obj))]
        if not any(os.path.exists(c) for c in cands):
            found.append(obj)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--fill", default=None)
    ap.add_argument("--list-fields", action="store_true", help="only print case_id and the contract field names")
    a = ap.parse_args()
    if a.list_fields or not a.fill:
        case = json.load(open(a.case, encoding="utf-8"))
        print(json.dumps(dict(case_id=find_key(case, "case_id") or find_key(case, "id"),
                              contract_fields=contract_fields(find_key(case, "output_contract")),
                              raw_contract=find_key(case, "output_contract")), ensure_ascii=False))
        print("fill.json needs exactly these keys (plus 'status'); values = paths, tables or short text")
        return 0
    case = {}
    try:
        case = json.load(open(a.case, encoding="utf-8"))
    except Exception as e:
        print(f"WARN cannot read case: {e!r}")
    fill = load_json_lenient(a.fill) if os.path.exists(a.fill) else {}
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

    def jl(n):
        try:
            return json.load(open(os.path.join(a.outdir, n), encoding="utf-8"))
        except Exception:
            return None
    cr, rr, el, vr = jl("check_report.json"), jl("render_report.json"), jl("edit_log.json"), jl("version_record.json")
    verification = {}   # one place where a reviewer sees what was machine-checked, and the verdicts
    if cr:
        verification["spec_audit"] = dict(status=cr.get("status"), n_fail=cr.get("n_fail"), n_warn=cr.get("n_warn"),
                                          failed=[r.get("check") for r in cr.get("results", []) if r.get("level") == "fail"][:15],
                                          what="sources and verbatim quotes, every printed number found in the materials, "
                                               "arrow ends, legend/category colours, units, mode rules")
    if rr:
        svg = (rr.get("outputs") or {}).get("svg") or {}
        png = (rr.get("outputs") or {}).get("png") or {}
        verification["render_layout"] = dict(status=rr.get("status"),
                                             failed=[i.get("check") for i in rr.get("issues", []) if i.get("level") == "fail"][:15],
                                             n_warn=sum(1 for i in rr.get("issues", []) if i.get("level") == "warn"),
                                             svg_text_elements=svg.get("text_elements"), png_pixels=png.get("pixels"),
                                             png_dpi=png.get("dpi"), fonts=rr.get("fonts"))
    if el:
        ch = el.get("changes", [])
        verification["edit"] = dict(ops=len(ch), verified=sum(1 for c in ch if c.get("verified")),
                                    unchanged_check=el.get("unchanged_check"))
    if vr:
        verification["reproducibility"] = vr.get("reproducibility")
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
    # every key the agent filled is kept at top level too (a contract-parsing surprise never hides answers)
    extra = {k: v for k, v in fill.items() if k not in fields and k != "case_id"}
    for k, v in extra.items():
        result.setdefault(k, v)
    status = fill.get("status", "complete")
    if missing and status == "complete":
        status = "partial"
    if render.get("status") == "needs_fix" and status == "complete":
        status = "partial"
    if cr and cr.get("status") == "fail" and status == "complete":
        status = "partial"
    if "status" not in fields:
        result["status"] = status
    if "status" in fields and isinstance(result.get("status"), str) and result["status"] == "complete" and (
            missing or render.get("status") == "needs_fix" or (cr and cr.get("status") == "fail")):
        result["status"] = "partial"
        result["status_note"] = "downgraded from complete: missing contract fields or failed spec/render checks"
    result["artifacts"] = artifacts if "artifacts" not in fields else result.get("artifacts")
    result["render_check"] = render
    if verification:
        result.setdefault("verification", verification)
    if vr:
        result.setdefault("inputs", [dict(file=x.get("file"), sha256=x.get("sha256")) for x in vr.get("inputs", [])][:40])
        result.setdefault("reproduce", dict(commands=vr.get("rerun_commands"), script="reproduce.sh",
                                            export_parameters=vr.get("export_parameters"), tools=vr.get("tools")))
    result["contract_fields"] = fields
    result["missing_fields"] = missing
    bad = broken_paths(fill, a.outdir)
    if bad:
        result["broken_paths"] = bad
    os.makedirs(a.outdir, exist_ok=True)
    out = os.path.join(a.outdir, "result.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(result=out, case_id=case_id, contract_fields=fields, missing=missing,
                          status=result.get("status", status), n_artifacts=len(artifacts), broken_paths=bad),
                     ensure_ascii=False))
    if missing:
        print("FIX: add these keys to fill.json and rerun: " + ", ".join(missing))
    if bad:
        print("FIX: these paths in fill.json do not exist: " + ", ".join(bad))
    if not fields:
        print("WARN no output_contract found in case.json: print the case file and fill fields by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
