import argparse

from .server import serve

p = argparse.ArgumentParser(prog="python -m studio", description="L120 Studio web app")
p.add_argument("--port", type=int, default=8120)
p.add_argument("--lan", action="store_true",
               help="also serve on this Wi-Fi network for the phone remote (random access token, shown as a QR code)")
a = p.parse_args()
serve(a.port, a.lan)
