from decimal import Decimal

from actual_tx_splitter.parsers.amazon import AmazonParser


def _email(plain: str = "", html: str = "", subject: str = "", sender: str = "auto-confirm@amazon.com") -> dict:
    return {
        "headers": {"from": sender, "subject": subject},
        "plain": plain,
        "html": html,
    }


def test_matches_by_sender():
    p = AmazonParser()
    assert p.matches(_email(subject="anything"))


def test_matches_by_subject():
    p = AmazonParser()
    assert p.matches(_email(subject="Your Amazon.com order #123-456", sender="forwarder@me.com"))


def test_does_not_match_random():
    p = AmazonParser()
    assert not p.matches(_email(subject="Hi", sender="friend@example.com"))


def test_matches_forwarded_ordered_subject():
    p = AmazonParser()
    assert p.matches(_email(subject='Fwd: Ordered: "Coco Coir 650gm Bricks..."', sender="me@gmail.com"))


def test_matches_capitalized_header_keys():
    p = AmazonParser()
    email = {
        "headers": {"From": "auto-confirm@amazon.com", "Subject": "Your order"},
        "plain": "",
        "html": "",
    }
    assert p.matches(email)


def test_matches_forwarded_body_marker():
    p = AmazonParser()
    body = "---------- Forwarded message ---------\nFrom: Amazon.com <auto-confirm@amazon.com>\nOrder #112-0"
    assert p.matches(_email(plain=body, subject="Fwd: heads up", sender="me@gmail.com"))


def test_summary_extracted_from_subject():
    p = AmazonParser()
    body = "Order # 111-2222222-3333333\nWidget\n$5.00\nGrand Total: $5.00"
    o = p.parse(_email(plain=body, subject='Fwd: Ordered: "Coco Coir 650gm Bricks..."'))
    assert o.summary == "Coco Coir 650gm Bricks…"


def test_order_id_parsed_through_bidi_marks():
    # Amazon emails interleave a U+202B (RTL embedding) between "Order #" and digits.
    p = AmazonParser()
    body = "Order # ‫111-7767326-0899427\nWidget\n$5.00\nGrand Total: $5.00"
    o = p.parse(_email(plain=body, subject="Your Amazon.com order"))
    assert o.order_id == "111-7767326-0899427"


def test_extracts_order_id_total_card():
    body = """
    Your order has been placed.
    Order #112-2233445-6677889
    Item 1: Widget                $12.34
    Item 2: Gadget                $5.66
    Shipping & Handling:          $0.00
    Estimated tax to be collected: $1.50
    Order Total: $19.50
    Payment Method: Visa ending in 1234
    """
    order = AmazonParser().parse(_email(plain=body))
    assert order.vendor == "amazon"
    assert order.order_id == "112-2233445-6677889"
    assert order.card_last4 == "1234"
    assert order.total == Decimal("19.50")
    assert len(order.line_items) >= 1
    assert sum((li.amount for li in order.line_items), Decimal(0)) - order.total < Decimal("0.50")


def test_falls_back_to_single_line_when_unparseable():
    body = "Order #999-0000000-1111111 Order Total: $42.00"
    order = AmazonParser().parse(_email(plain=body))
    assert len(order.line_items) == 1
    assert order.line_items[0].amount == Decimal("42.00")
