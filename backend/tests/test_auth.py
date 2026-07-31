"""Тесты аутентификации: пароли, JWT, логин и /auth/me."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_auth.db")

from fastapi.testclient import TestClient

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.main import app


def test_password_hash_roundtrip():
    stored = hash_password("secret")
    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("secret", "garbage")


def test_jwt_roundtrip():
    token = create_access_token(subject="ivanov", role="buyer")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "ivanov"
    assert payload["role"] == "buyer"
    assert decode_access_token("not-a-token") is None


def test_login_and_me():
    with TestClient(app) as client:
        # Неверный пароль.
        resp = client.post(
            "/auth/login", json={"username": "ivanov", "password": "bad"}
        )
        assert resp.status_code == 401

        # Успешный вход сидированным пользователем.
        resp = client.post(
            "/auth/login", json={"username": "ivanov", "password": "demo123"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["role"] == "buyer"
        token = data["access_token"]

        # /auth/me с токеном и без.
        assert client.get("/auth/me").status_code == 401
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "ivanov"
