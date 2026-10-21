# SPDX-License-Identifier: Apache-2.0
"""Command line: python -m comfyq_bridge.client <command>

  doctor                               check the classroom setup
  workflows [--served]                 list workflows (served ones marked *)
  params WORKFLOW                      show a workflow's parameters
  submit WORKFLOW [-p key=value]... [--file key=path]... [--prompt TEXT] [--wait] [--fetch DIR]
  fetch JOB_ID DIR                     download a finished job's outputs
"""
from __future__ import annotations

import argparse
import json
import sys

from . import doctor
from .api import ComfyQClient
from .http import ComfyQError
from .profile import load_profile

_MARK = {"ok": "  ok ", "warn": "WARN ", "fail": "FAIL "}


def _kv(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items or ():
        k, sep, v = it.partition("=")
        if not sep:
            raise SystemExit(f"expected key=value, got {it!r}")
        out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="comfyq-bridge")
    ap.add_argument("--profile")
    ap.add_argument("--user", help="override the student name")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    w = sub.add_parser("workflows")
    w.add_argument("--served", action="store_true")
    pa = sub.add_parser("params")
    pa.add_argument("workflow")
    s = sub.add_parser("submit")
    s.add_argument("workflow")
    s.add_argument("-p", "--param", action="append", default=[])
    s.add_argument("--file", action="append", default=[], help="key=path: upload and use as a media param")
    s.add_argument("--prompt", default="")
    s.add_argument("--wait", action="store_true")
    s.add_argument("--fetch", metavar="DIR")
    f = sub.add_parser("fetch")
    f.add_argument("job_id")
    f.add_argument("dir")
    args = ap.parse_args(argv)

    prof = load_profile(args.profile)
    if args.user:
        from dataclasses import replace
        prof = replace(prof, user=args.user)
    client = ComfyQClient(prof)
    try:
        return _run(client, args)
    except ComfyQError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()


def _run(client: ComfyQClient, args: argparse.Namespace) -> int:
    if args.cmd == "doctor":
        results = doctor.run(client, client.profile.reserve_gb)
        for level, msg in results:
            print(_MARK[level] + msg)
        return 1 if any(l == doctor.FAIL for l, _ in results) else 0

    if args.cmd == "workflows":
        client.connect()
        served = client.served_ids()
        for wf in client.workflows():
            if args.served and wf.id not in served:
                continue
            vram = f"{wf.vram_peak_gb:5.1f} GB" if wf.vram_peak_gb else "   ?    "
            flag = "*" if wf.id in served else ("x" if wf.unavailable else " ")
            print(f"{flag} {wf.category:12} {vram}  {wf.id}")
        return 0

    if args.cmd == "params":
        for spec in client.params(args.workflow):
            extra = f" [{spec.min}..{spec.max}]" if spec.min is not None else ""
            extra += f" options={list(spec.options)}" if spec.options else ""
            print(f"{spec.key:48} {spec.kind:7} default={spec.default!r}{extra}\n    {spec.label}")
        return 0

    if args.cmd == "submit":
        client.connect()
        values: dict = dict(_kv(args.param))
        for key, path in _kv(args.file).items():
            values[key] = client.upload(path)
            print(f"uploaded {path} → {values[key]}")
        job_id = client.submit(args.workflow, values, args.prompt)
        print(f"booked {job_id}")
        if not (args.wait or args.fetch):
            return 0

        def show(j):
            pct = f"{j.progress * 100:5.1f}%" if j.progress is not None else "      "
            print(f"  {j.phase:20} {pct}", flush=True)

        job = client.wait(job_id, on_update=show)
        if not job.ok:
            print(f"job {job.phase}: {job.error}", file=sys.stderr)
            return 1
        print(json.dumps([o.media_path for o in job.outputs]))
        if args.fetch:
            for p in client.download_outputs(job, args.fetch):
                print(f"saved {p}")
        return 0

    if args.cmd == "fetch":
        job = client.job(args.job_id)
        for p in client.download_outputs(job, args.dir):
            print(f"saved {p}")
        return 0
    return 2
