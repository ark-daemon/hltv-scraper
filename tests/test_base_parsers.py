from scrapers.base import BaseScraper


class _DummyScraper(BaseScraper):
    async def run(self):
        return {}


# ------------------------------------------------------------------
# parse_float
# ------------------------------------------------------------------

def test_parse_float_rejects_internal_minus():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("1-2.5") == 12.5


def test_parse_float_leading_minus_kept():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("-12.5%") == -12.5


def test_parse_float_none_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float(None) is None


def test_parse_float_empty_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("") is None


def test_parse_float_whitespace_stripped():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("  3.14  ") == 3.14


def test_parse_float_currency_symbols_stripped():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("$1,234.56") == 1234.56


# ------------------------------------------------------------------
# parse_int
# ------------------------------------------------------------------

def test_parse_int_basic():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_int("42") == 42


def test_parse_int_with_commas():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_int("1,000,000") == 1000000


def test_parse_int_none_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_int(None) is None


def test_parse_int_dash_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_int("-") is None


def test_parse_int_negative():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_int("-7") == -7


def test_parse_int_mixed_text():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_int("Rank #12") == 12


# ------------------------------------------------------------------
# parse_percent
# ------------------------------------------------------------------

def test_parse_percent_basic():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_percent("53.8%") == 53.8


def test_parse_percent_without_symbol():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_percent("75.5") == 75.5


def test_parse_percent_none_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_percent(None) is None


# ------------------------------------------------------------------
# parse_prize_usd
# ------------------------------------------------------------------

def test_parse_prize_usd_basic():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_prize_usd("$500,000") == 500000


def test_parse_prize_usd_with_currency_code():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_prize_usd("USD 250 000") == 250000


def test_parse_prize_usd_none_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_prize_usd(None) is None


def test_parse_prize_usd_empty_returns_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_prize_usd("") is None


# ------------------------------------------------------------------
# extract_id_from_url
# ------------------------------------------------------------------

def test_extract_id_from_url_standard():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url("/matches/2370727/faze-vs-navi") == 2370727


def test_extract_id_from_url_player():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url("/player/7998/zywoo") == 7998


def test_extract_id_from_url_team():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url("/team/6651/navi") == 6651


def test_extract_id_from_url_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url(None) is None


def test_extract_id_from_url_empty():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url("") is None


def test_extract_id_from_url_no_number():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url("/news/some-article") is None


def test_extract_id_from_url_custom_position():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url("/stats/matches/mapstatsid/123456/match", position=-2) == 123456


# ------------------------------------------------------------------
# extract_id_from_url_regex
# ------------------------------------------------------------------

def test_extract_id_from_url_regex():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.extract_id_from_url_regex("/stats/matches/mapstatsid/123456/match", r"/mapstatsid/(\d+)/") == 123456


# ------------------------------------------------------------------
# safe_text / safe_attr
# ------------------------------------------------------------------

def test_safe_text_none():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.safe_text(None) is None
