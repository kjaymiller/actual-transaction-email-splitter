from __future__ import annotations

from .parsers import PARSERS, ParsedOrder, Parser


class UnknownVendor(Exception):
    pass


def pick_parser(email: dict) -> Parser:
    for p in PARSERS:
        if p.matches(email):
            return p
    raise UnknownVendor("no parser matched this email")


def parse_email(email: dict) -> tuple[Parser, ParsedOrder]:
    p = pick_parser(email)
    return p, p.parse(email)
