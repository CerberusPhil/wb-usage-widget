"""Pass URLs to OS browser services, never to a command-line subprocess."""
import os
import sys


def open_browser(url):
    if sys.platform == 'darwin':
        from AppKit import NSWorkspace
        from Foundation import NSURL
        native_url = NSURL.URLWithString_(url)
        if native_url is None or not NSWorkspace.sharedWorkspace().openURL_(native_url):
            raise RuntimeError('The default browser could not be opened.')
    elif sys.platform == 'win32':
        os.startfile(url)
    else:
        raise RuntimeError('Desktop browser handoff supports macOS and Windows only.')
