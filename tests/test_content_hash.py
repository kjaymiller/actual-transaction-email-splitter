"""Content-hash dedup survives re-forwarding by Spark/Gmail/etc.

The forwarding client regenerates Message-ID per send, so Message-ID-only
dedup fires false-negative on retries. Hashing sender + normalized subject +
body collapses those into one ledger entry.
"""

from actual_tx_splitter.webhook import _content_hash


def _email(subject: str, sender: str, body: str, msg_id: str = "x@y") -> dict:
    return {
        "headers": {"from": sender, "subject": subject, "message_id": msg_id},
        "plain": body,
        "html": "",
    }


def test_same_body_different_message_id_hashes_equal():
    a = _email("Your Amazon.com order", "auto-confirm@amazon.com", "Order #1\n$5.00", msg_id="aaa@spark")
    b = _email("Your Amazon.com order", "auto-confirm@amazon.com", "Order #1\n$5.00", msg_id="bbb@spark")
    assert _content_hash(a) == _content_hash(b)


def test_fwd_prefix_collapses():
    a = _email("Your Amazon.com order", "me@gmail.com", "Order #1\n$5.00")
    b = _email("Fwd: Your Amazon.com order", "me@gmail.com", "Order #1\n$5.00")
    c = _email("Fwd: Fwd: Re: Your Amazon.com order", "me@gmail.com", "Order #1\n$5.00")
    assert _content_hash(a) == _content_hash(b) == _content_hash(c)


def test_whitespace_normalization():
    a = _email("S", "x@y", "line one\nline two")
    b = _email("S", "x@y", "line one     line two")
    c = _email("S", "x@y", "  line one\n\n\nline two\n")
    assert _content_hash(a) == _content_hash(b) == _content_hash(c)


def test_different_body_different_hash():
    a = _email("S", "x@y", "Order #1 total $5")
    b = _email("S", "x@y", "Order #2 total $7")
    assert _content_hash(a) != _content_hash(b)


def test_different_sender_different_hash():
    a = _email("S", "alice@x.com", "body")
    b = _email("S", "bob@x.com", "body")
    assert _content_hash(a) != _content_hash(b)
