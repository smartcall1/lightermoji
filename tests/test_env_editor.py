from env_editor import upsert_key, remove_key

SAMPLE = """# comment
TELEGRAM_BOT_TOKEN=abc
DCA_SKHYNIXUSD=50
# DCA_NVDAUSD=30
DCA_TIME_AEST=09:00
"""


def test_upsert_new_key(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE, encoding="utf-8")
    upsert_key("DCA_TSLAUSD", "20", path=p)
    content = p.read_text(encoding="utf-8")
    assert "DCA_TSLAUSD=20" in content
    assert "DCA_SKHYNIXUSD=50" in content  # 기존 줄 보존


def test_upsert_existing_key_replaces_value(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE, encoding="utf-8")
    upsert_key("DCA_SKHYNIXUSD", "80", path=p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert "DCA_SKHYNIXUSD=80" in lines
    assert sum(1 for l in lines if l.startswith("DCA_SKHYNIXUSD=")) == 1


def test_upsert_uncomments_commented_key(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE, encoding="utf-8")
    upsert_key("DCA_NVDAUSD", "30", path=p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert "DCA_NVDAUSD=30" in lines
    assert "# DCA_NVDAUSD=30" not in lines


def test_remove_key_success(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE, encoding="utf-8")
    removed = remove_key("DCA_SKHYNIXUSD", path=p)
    assert removed is True
    content = p.read_text(encoding="utf-8")
    assert "DCA_SKHYNIXUSD" not in content
    assert "TELEGRAM_BOT_TOKEN=abc" in content  # 다른 줄은 보존


def test_remove_key_not_found(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE, encoding="utf-8")
    removed = remove_key("DCA_NOTEXIST", path=p)
    assert removed is False
