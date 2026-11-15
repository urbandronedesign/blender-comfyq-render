from client.api import ComfyQClient
from client.params import specs_from_wire
from client.profile import Profile
from client.transport.base import ComfyQTransport


class FakeTransport(ComfyQTransport):
    def __init__(self):
        super().__init__()
        self.booked = []

    def connect(self): ...
    def close(self): ...

    @property
    def connected(self):
        return True

    def book(self, workflow_id, params, user_id, prompt="", scheduled_time=None):
        self.booked.append({"workflow_id": workflow_id, "params": params, "user": user_id, "prompt": prompt})
        return "job-1"

    def cancel(self, job_id): ...


SPECS = specs_from_wire([
    {"key": "primitivestringmultiline_value_5508", "type": "textarea", "label": "Prompt", "default": "x"},
    {"key": "neg", "type": "textarea", "label": "Negative", "default": ""},
    {"key": "seed", "type": "number", "label": "Seed", "default": 0, "min": 0, "max": 10},
])


def test_prompt_param_is_also_sent_as_the_job_prompt():
    t = FakeTransport()
    c = ComfyQClient(Profile(name="t", user="ana"), transport=t)
    text = "A dystopic scene\nof a flooded city"
    assert c.submit("wf", {"primitivestringmultiline_value_5508": text, "seed": 3}, specs=SPECS,
                    check_served=False) == "job-1"
    b = t.booked[0]
    assert b["params"]["primitivestringmultiline_value_5508"] == text      # multi-line kept as is
    assert b["prompt"] == text
    assert b["user"] == "ana"


def test_unset_prompt_params_are_sent_so_comfyq_cannot_fill_them_with_the_prompt():
    # ComfyQ copies the job prompt into the first unset textarea with field "text": for workflows whose
    # real prompt is a Primitive, that was the negative prompt (Flux2 Klein t2i ran with negative = prompt).
    t = FakeTransport()
    c = ComfyQClient(Profile(name="t"), transport=t)
    c.submit("wf", {"primitivestringmultiline_value_5508": "a red car"}, specs=SPECS, check_served=False)
    assert t.booked[0]["params"]["neg"] == ""                       # sent explicitly, its default


def test_explicit_prompt_wins():
    t = FakeTransport()
    c = ComfyQClient(Profile(name="t"), transport=t)
    c.submit("wf", {"primitivestringmultiline_value_5508": "a"}, prompt="b", specs=SPECS, check_served=False)
    assert t.booked[0]["prompt"] == "b"


class DownTransport(FakeTransport):
    """ComfyQ in admin mode has no Socket.IO server: the handshake 404s."""

    def connect(self):
        from client.http import ComfyQError
        raise ComfyQError("Socket connection to http://x failed: Connection error")

    @property
    def connected(self):
        return False


def _client(mode):
    c = ComfyQClient(Profile(name="t", user="ana"), transport=DownTransport())
    c.http.get = lambda path, **_: {"locked": False} if path == "/access/status" else {"mode": mode}
    return c


def test_admin_mode_says_comfyq_serves_nothing():
    import pytest
    from client.api import NOT_SERVING
    from client.http import ComfyQError
    with pytest.raises(ComfyQError) as e:
        _client("admin").connect()
    assert str(e.value) == NOT_SERVING
    with pytest.raises(ComfyQError) as e:                  # serving but unreachable: the socket's own error
        _client("student").connect()
    assert "Connection error" in str(e.value)
