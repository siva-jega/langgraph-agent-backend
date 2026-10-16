"""Cache-read extraction must survive the service-tier key prefix.

With OPENAI_SERVICE_TIER=priority, langchain-openai stores cached tokens under
``priority_cache_read`` (not ``cache_read``); reading the plain key reported 0
cache hits platform-wide.
"""

from common.llm_utils import cache_read_tokens


def test_priority_tier_prefixed_key_is_captured() -> None:
    assert cache_read_tokens({"priority_cache_read": 1024, "priority": 112}) == 1024


def test_plain_and_flex_keys() -> None:
    assert cache_read_tokens({"cache_read": 512}) == 512
    assert cache_read_tokens({"flex_cache_read": 256}) == 256


def test_missing_or_none() -> None:
    assert cache_read_tokens(None) == 0
    assert cache_read_tokens({}) == 0
    assert cache_read_tokens({"priority_cache_read": None}) == 0
