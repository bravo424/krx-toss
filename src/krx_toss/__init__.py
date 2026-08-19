"""KRX spot MFT/LFT trading platform on Toss Open API."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("krx-toss-trading")
except PackageNotFoundError:
    __version__ = "0.1.0"
