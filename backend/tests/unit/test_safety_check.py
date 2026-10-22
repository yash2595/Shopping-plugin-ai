import pytest
from sqlalchemy.engine.url import make_url


def test_refuse_run_on_non_test_database():
    unsafe_urls = [
        "postgresql+psycopg://postgres@127.0.0.1:5433/shopping",
        "postgresql+psycopg://postgres@127.0.0.1:5433/production",
        "postgresql+psycopg://postgres@127.0.0.1:5433/test_db",
        "postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test_extra",
    ]
    for url in unsafe_urls:
        db_name = make_url(url).database
        assert not db_name.endswith("_test"), f"URL unexpectedly ended with _test: {url}"

    safe_url = "postgresql+psycopg://postgres@127.0.0.1:5433/shopping_test"
    safe_db_name = make_url(safe_url).database
    assert safe_db_name.endswith("_test")
