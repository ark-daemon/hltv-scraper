from scrapers.base import BaseScraper


class _DummyScraper(BaseScraper):
    async def run(self):
        return {}


def test_parse_float_rejects_internal_minus():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("1-2.5") == 12.5


def test_parse_float_leading_minus_kept():
    scraper = _DummyScraper(db=None, browser=None, checkpoint=None)
    assert scraper.parse_float("-12.5%") == -12.5

