from jevtest.adapters.clock import SystemClock


def test_the_system_clock_moves_forward():
    c = SystemClock()
    before = c.now()
    c.sleep(0)
    assert c.now() >= before
