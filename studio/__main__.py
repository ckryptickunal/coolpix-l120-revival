import argparse

from .server import serve

p = argparse.ArgumentParser(prog="python -m studio", description="L120 Studio web app")
p.add_argument("--port", type=int, default=8120)
p.add_argument("--host", default="127.0.0.1")
a = p.parse_args()
serve(a.host, a.port)
