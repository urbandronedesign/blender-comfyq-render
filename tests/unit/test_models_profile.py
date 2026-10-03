from client.models import Job, Workflow
from client.profile import load_profile

WIRE_JOB = {  # GET /jobs/:id shape, from a real completed job
    "id": "2c8b50a9-3229-4cf4-89e7-c1e6c2356d96", "user_id": "b2renger", "status": "completed",
    "phase": "completed", "progress": {"stepsDone": 10, "stepsTotal": 10},
    "outputs": [{"kind": "image", "mime": "image/png", "filename": "b2renger_x_00001_.png", "subfolder": "",
                 "type": "output", "nodeId": "29", "sizeBytes": 1934989}],
    "workflow_id": "image_edit_krea2_quadview", "error_reason": None,
}


def test_job_from_http_shape():
    j = Job.from_wire(WIRE_JOB)
    assert j.ok and j.done and j.progress == 1.0
    assert j.outputs[0].media_path == "b2renger_x_00001_.png"


def test_job_cancelled_while_running_reads_as_cancelled():
    # real ComfyQ shape of a job cancelled while executing (52d895a8, 2026-10-03)
    j = Job.from_wire({"id": "x", "status": "failed", "error_reason": "cancelled"})
    assert j.phase == "cancelled" and j.done and not j.ok
    assert Job.from_wire({"id": "y", "status": "failed", "error_reason": "OOM"}).phase == "failed"


def test_job_from_socket_shape():
    j = Job.from_wire({"id": "x", "status": "processing", "phase": "executing", "progress": {"value": 3, "max": 12},
                       "outputs": [{"kind": "video", "filename": "a.mp4", "subfolder": "sub"}]})
    assert not j.done and j.progress == 0.25 and j.outputs[0].media_path == "sub/a.mp4"


def test_workflow_calibrated_means_vram_measured():
    timed_only = {"id": "w", "hasCalibration": True, "calibration": {"durationSec": 51, "vramPeakGb": None}}
    measured = {"id": "v", "hasCalibration": True, "calibration": {"vramPeakGb": 16.94}}
    assert not Workflow.from_wire(timed_only).calibrated
    assert Workflow.from_wire(measured).vram_peak_gb == 16.94


def test_profile_file_and_env(tmp_path, monkeypatch):
    for k in ("COMFYQ_URL", "COMFYQ_TOKEN", "COMFYQ_USER", "COMFYQ_PASSWORD", "COMFYQ_ADMIN_PASSWORD",
              "COMFYQ_BRIDGE_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "pw.txt").write_text("s3cret\n")
    cfg = tmp_path / "profiles.toml"
    cfg.write_text(f'''
default = "class"
[profiles.class]
comfyq = {{ url = "http://192.168.1.10:3000/", user = "ana", password_file = "{(tmp_path / 'pw.txt').as_posix()}" }}
[profiles.solo]
comfyq = {{ url = "http://127.0.0.1:3000", token_env = "MY_TOKEN" }}
gpu = {{ policy = "shared", mixar_reserve_gb = 8 }}
''')
    p = load_profile(path=cfg)
    assert (p.name, p.url, p.user, p.password) == ("class", "http://192.168.1.10:3000", "ana", "s3cret")
    assert p.admin_password == ""                     # students: no admin password unless given one
    monkeypatch.setenv("MY_TOKEN", "abc")
    s = load_profile("solo", path=cfg)
    assert s.token == "abc" and s.gpu_policy == "shared" and s.mixar_reserve_gb == 8
    monkeypatch.setenv("COMFYQ_URL", "http://10.0.0.5:3000")
    assert load_profile("solo", path=cfg).url == "http://10.0.0.5:3000"
    monkeypatch.setenv("COMFYQ_ADMIN_PASSWORD", "teach")
    assert load_profile("solo", path=cfg).admin_password == "teach"


def test_missing_file_gives_local_default(tmp_path, monkeypatch):
    monkeypatch.delenv("COMFYQ_URL", raising=False)
    monkeypatch.delenv("COMFYQ_BRIDGE_PROFILE", raising=False)
    p = load_profile(path=tmp_path / "nope.toml")
    assert p.name == "local" and p.url == "http://127.0.0.1:3000"
