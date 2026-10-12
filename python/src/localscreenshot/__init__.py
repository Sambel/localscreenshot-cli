"""Client of the localscreenshot API: screenshots of public web pages.

    from localscreenshot import screenshot

    shot = screenshot("https://example.com", country="BR", full_page=True)
    open("example.png", "wb").write(shot.image)

The API key is read from the LOCALSCREENSHOT_API_KEY environment variable, or from the
file written by ``localscreenshot login``, unless ``api_key`` is given.
"""

from .client import VERSION, ApiError, Screenshot, countries, credits, screenshot

__version__ = VERSION
__all__ = ["ApiError", "Screenshot", "countries", "credits", "screenshot", "__version__"]
