from .amazon import AmazonParser
from .base import LineItem, ParsedOrder, Parser

PARSERS: list[Parser] = [AmazonParser()]

__all__ = ["PARSERS", "LineItem", "ParsedOrder", "Parser"]
