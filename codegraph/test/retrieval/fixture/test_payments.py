"""Test file fixture — exercises the hybrid search test-path penalty; these
functions share names/keywords with payments.py but should rank lower for
the same query since they are tests, not production code."""


def test_process_payment():
    assert True


def test_refund_payment():
    assert True
