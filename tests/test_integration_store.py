def test_stn_password_falls_back_to_env_when_db_decrypt_fails(monkeypatch):
    monkeypatch.setenv("PDF_STN_LOGIN", "ips_user")
    monkeypatch.setenv("PDF_STN_PASSWORD", "secret-from-env")

    from belener.integration_store import _effective_stn_password

    pwd = _effective_stn_password("ips_user", "", "encrypted-blob-that-failed")
    assert pwd == "secret-from-env"
