"""Keep developer credentials and per-review spending limits out of offline tests.

Set these before test modules import Gateway, whose dotenv loader uses setdefault.
Tests that exercise configuration explicitly override these values with monkeypatch.
"""
import os

os.environ['CLOCKED_API_KEY'] = ''
os.environ['CLOCKED_CREDIT_CAP'] = '1500'
os.environ['CLOCKED_CONCURRENCY'] = '3'
