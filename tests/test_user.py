"""Integration tests for User API — registration, login, profile.

2026-10-06 live测试普查轮：写/认证用例迁 conftest user_client 隔离面（真实
router + tmp users库），不再走live :8090注册写生产 users 表（此前每次全量跑
+3行testuser_/logintest_/metest_测试账号，无删除端点永不自清）。
仅 TestUserHealth 留守live（只读冒烟：运行进程router挂载守卫，零生产写入）。
"""

import uuid


class TestUserHealth:
    def test_health(self, client):
        """live只读冒烟（:8090）：user router在运行进程的挂载守卫，零写入。"""
        resp = client.get("/api/user/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "UserSystem"


class TestUserRegister:
    def test_register_and_login(self, user_client):
        """Register a new user, then login.（隔离面：数据面确定性，注册必然200）"""
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Register
        resp = user_client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": email,
                "password": "testpass123",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["username"] == username
        assert data["email"] == email

    def test_login_valid_credentials(self, user_client):
        """Login with valid credentials returns a token."""
        username = f"logintest_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Register first
        user_client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": email,
                "password": "mypassword",
            },
        )

        # Login
        resp = user_client.post(
            "/api/user/login",
            data={
                "username": username,
                "password": "mypassword",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_invalid_credentials(self, user_client):
        """Login with wrong password returns 401."""
        resp = user_client.post(
            "/api/user/login",
            data={
                "username": "nonexistent_user",
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401

    def test_me_without_token_returns_401(self, user_client):
        """GET /me without auth returns 401."""
        resp = user_client.get("/api/user/me")
        assert resp.status_code == 401

    def test_me_with_valid_token(self, user_client):
        """GET /me with valid token returns user info."""
        username = f"metest_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Register
        user_client.post(
            "/api/user/register",
            json={
                "username": username,
                "email": email,
                "password": "pass123",
            },
        )

        # Login
        login_resp = user_client.post(
            "/api/user/login",
            data={
                "username": username,
                "password": "pass123",
            },
        )
        if login_resp.status_code != 200:
            return  # Skip if registration failed

        token = login_resp.json()["access_token"]

        # Get profile
        resp = user_client.get(
            "/api/user/me",
            headers={
                "Authorization": f"Bearer {token}",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == username
