from client.models import Job
from client.queue import Place, describe, place


def job(i, wf="a", user="u", phase="scheduled", slot=0):
    return Job(id=i, workflow_id=wf, user_id=user, phase=phase, time_slot=slot)


def test_place_in_a_lane_running_job_first_then_booking_time():
    jobs = [job("r", user="tom", phase="executing"), job("w2", slot=20), job("w1", slot=10),
            job("other", wf="b", slot=5), job("old", phase="completed", slot=1)]
    assert place(jobs, "w1") == Place(1, "tom", 2)
    assert place(jobs, "w2") == Place(2, "tom", 2)
    assert place(jobs, "r") == Place(0, "tom", 2)
    assert place(jobs, "other") == Place(1, "", 1)          # its own lane, nothing running there
    assert place(jobs, "old") is None and place(jobs, "nope") is None


def test_describe():
    assert describe(Place(0, "tom", 3)) == "Running on the GPU"
    assert describe(Place(1, "tom", 3)) == "Next in line · now running: tom"
    assert describe(Place(3, "lea", 3), me="lea") == "3rd in line · now running: lea (this PC)"
    assert describe(Place(11, "", 12)) == "11th in line"
    assert describe(Place(22, "", 30)) == "22nd in line"


def test_unreachable_server_message_is_plain_words():
    import pytest
    from client.http import ComfyQError, Http
    with pytest.raises(ComfyQError) as e:
        Http("http://127.0.0.1:9", "", 2.0).get("/workflows")
    msg = str(e.value)
    assert "is not answering" in msg and "Connect again" in msg and "Error" not in msg
