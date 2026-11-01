import settings

def test_usd():
    assert settings.enabled('USD')

def test_supported_eur():
    assert 'EUR' in settings.SUPPORTED_CURRENCIES
