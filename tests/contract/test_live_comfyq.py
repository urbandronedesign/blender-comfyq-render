"""Contract tests against a running ComfyQ. Phase 2's REST transport must pass the same file.

Read-only by default. COMFYQ_E2E=1 also books a real (short) job on whatever is served.
"""
import os

import pytest
from client import ComfyQClient, ComfyQError, load_profile
from client.http import Http

URL = os.environ.get("COMFYQ_URL", "http://127.0.0.1:3000")


def _up() -> bool:
    try:
        Http(URL, timeout_s=3).get("/access/status")
        return True
    except ComfyQError:
        return False


pytestmark = [pytest.mark.live, pytest.mark.skipif(not _up(), reason=f"no ComfyQ at {URL}")]


@pytest.fixture(scope="module")
def client():
    c = ComfyQClient(load_profile())
    c.connect()
    yield c
    c.close()


def test_workflows_listed(client):
    wfs = client.workflows()
    assert wfs and all(w.id and w.category for w in wfs)


def test_served_lanes_have_parameter_maps(client):
    lanes = client.lanes()
    assert lanes, "ComfyQ serves no workflow"
    assert all(l.parameter_map for l in lanes if l.state != "starting")


def test_every_param_type_is_known(client):
    """A type we don't map degrades to `text`; flag it so the form gets a proper widget."""
    unknown = [(wf.id, s.key, s.wire_type)
               for wf in client.workflows(include_unavailable=False)
               for s in client.params(wf.id)
               if s.kind == "text" and s.wire_type != "text"]
    assert not unknown


def test_booking_unserved_is_refused(client):
    served = client.served_ids()
    other = next(w.id for w in client.workflows(include_unavailable=False) if w.id not in served)
    with pytest.raises(ComfyQError, match="not being served"):
        client.submit(other, {}, check_served=True)
    # The server refuses too, even when the client check is skipped.
    with pytest.raises(ComfyQError, match="not being served"):
        client.transport.book(other, {}, client.profile.user or "contract-test")


def test_job_prompt_is_stored_without_running(client):
    """Book a served workflow a day ahead (it never runs), read what ComfyQ stored, delete it.

    ComfyQ's job cards show the job's top-level `prompt`; it must hold the prompt text, multi-line included.
    """
    import time

    lane = client.lanes()[0]
    specs = client.params(lane.workflow_id)
    prompt_spec = next((s for s in specs if s.kind == "prompt"), None)
    if prompt_spec is None:
        pytest.skip(f"{lane.workflow_id} has no prompt")
    text = "Contract test prompt\nsecond line — accents éà"
    values = {prompt_spec.key: text}
    for s in specs:                           # required media inputs: a name is enough, the job never runs
        if s.is_media and s.required and s.default in (None, ""):
            values[s.key] = "comfyq_contract_placeholder.png"
    tomorrow = int((time.time() + 86400) * 1000)
    job_id = client.submit(lane.workflow_id, values, specs=specs, scheduled_time=tomorrow)
    try:
        stored = client.http.get(f"/jobs/{job_id}")["job"]
        assert stored["status"] == "scheduled"
        assert stored["params"][prompt_spec.key] == text
        assert stored["prompt"] == text, "the job card would say 'no prompt'"
    finally:
        client.transport.delete(job_id)
        time.sleep(0.5)
    with pytest.raises(ComfyQError):
        client.http.get(f"/jobs/{job_id}")


@pytest.mark.gpu
@pytest.mark.skipif(os.environ.get("COMFYQ_E2E") != "1", reason="set COMFYQ_E2E=1 to book a real job")
def test_book_wait_download(client, tmp_path):
    lane = client.lanes()[0]
    specs = client.params(lane.workflow_id)
    values = {}
    needs_image = [s for s in specs if s.kind in ("image", "mask") and s.required]
    if needs_image:
        png = tmp_path / "in.png"
        _write_png(png, 512, 512)
        name = client.upload(png)
        values = {s.key: name for s in needs_image}
    job_id = client.submit(lane.workflow_id, values)
    job = client.wait(job_id, timeout_s=1200)
    assert job.ok, job.error
    files = client.download_outputs(job, tmp_path / "out")
    assert files and all(f.stat().st_size > 0 for f in files)


def _write_png(path, w, h):
    import struct
    import zlib
    raw =b"".join(b"\x00" + b"".join(bytes([x * 255 // w, y * 255 // h, 128]) for x in range(w)) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
