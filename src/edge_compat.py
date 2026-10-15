"""Compatibility for edge-tts 7.2.8's retired synthesis endpoint.

Endpoint migration follows https://github.com/rany2/edge-tts/pull/491.
Keep the installed library and its audio protocol; apply only to its known
legacy URL, leaving a future upstream endpoint unchanged.
"""
import threading
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

_lock = threading.Lock()


def parse_server_date(value):
    """HTTP dates are English regardless of the Windows display language."""
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def ensure_compatibility():
    import edge_tts.communicate as communicate
    import edge_tts.constants as constants
    from edge_tts.drm import DRM
    with _lock:
        old = urlsplit(communicate.WSS_URL)
        if (old.scheme == 'wss' and old.hostname == 'speech.platform.bing.com'
                and old.path == '/consumer/speech/synthesize/readaloud/edge/v1'):
            endpoint = ('wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1'
                        '?Ocp-Apim-Subscription-Key=' + constants.TRUSTED_CLIENT_TOKEN)
            communicate.WSS_URL = endpoint
            constants.WSS_URL = endpoint
            DRM.parse_rfc2616_date = staticmethod(parse_server_date)
            return True
    return False
