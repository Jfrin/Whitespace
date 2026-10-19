class ExtractorStop(Exception):
    """Raised by an extractor when the whole run should stop (daily quota used up, bad API key,
    unknown model). Work already saved is kept; the next run picks up where this one stopped."""
