import os
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="c3d-test-")
os.environ.update({
    # Set TEST_DATABASE_URL to run the suite against Postgres.
    "DATABASE_URL": os.getenv("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"),
    "LOCAL_STORAGE_DIR": f"{_tmp}/files",
    "IMAGE_PROVIDER": "mock", "BG_PROVIDER": "mock", "MESH_PROVIDER": "mock",
    "MOCK_DELAY": "0", "SECRET_KEY": "test-secret", "ADMIN_EMAILS": "admin@example.com",
    "SIGNUP_CREDITS": "12", "MAX_ACTIVE_JOBS_PER_USER": "2",
    "RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "rzp_secret", "RAZORPAY_WEBHOOK_SECRET": "rzp_hook",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_test",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402


@pytest.fixture()
def client():
    from backend import security

    security._hits.clear()
    with TestClient(app) as c:
        yield c


_n = [0]


def signup(c: TestClient, email: str | None = None) -> dict:
    _n[0] += 1
    email = email or f"user{_n[0]}_{time.time_ns()}@example.com"
    r = c.post("/api/auth/signup", json={"email": email, "password": "hunter2hunter2", "accept_terms": True})
    assert r.status_code == 200, r.text
    return r.json()


def wait_done(c: TestClient, gen_id: str, path: str = "/api/generations/", headers=None, timeout=15) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        g = c.get(path + gen_id, headers=headers).json()
        if g["status"] in ("done", "failed"):
            return g
        time.sleep(0.05)
    raise AssertionError("generation did not finish")
